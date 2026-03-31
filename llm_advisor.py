"""
AutoResearch Polymarket - LLM Advisor
Proposes intelligent strategy mutations using Google Gemini API.
Replaces random parameter picking with context-aware, data-driven proposals.
"""

import os
import json
import random
import traceback
from datetime import datetime

try:
    from google import genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

from db import get_db

# ─── Gemini config ────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyDOXaAP8PgJUx2A9gGHf63eP90IeZBYKkM")
GEMINI_MODEL = "gemini-2.5-flash"  # Fast, cheap, excellent reasoning


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] [LLM] {msg}")


# ─── Tunable parameter space ─────────────────────────────────────────

PARAM_SPACE = {
    "MAX_TOTAL_COST": {
        "type": "float",
        "range": [0.94, 0.995],
        "step": 0.005,
        "description": "Maximum combined bid price for Up+Down tokens. Lower = safer but fewer fills. Higher = more fills but less edge per trade.",
    },
    "BID_SPREAD": {
        "type": "float",
        "range": [0.5, 6.0],
        "step": 0.5,
        "description": "Cents below implied price to place each bid. Lower = more aggressive (fills more, less edge). Higher = more conservative (fills less, more edge).",
    },
    "MIN_EDGE_CENTS": {
        "type": "float",
        "range": [0.1, 3.0],
        "step": 0.1,
        "description": "Minimum profit per trade after fees in cents. Lower = takes more trades with less edge. Higher = only trades with strong edge.",
    },
    "ORDER_SIZE_USD": {
        "type": "float",
        "range": [2, 25],
        "step": 1,
        "description": "USD per side of the arb ($X on Up + $X on Down). Larger = more profit per fill but more capital at risk on partial fills.",
    },
    "MAX_ORDERS_PER_POLL": {
        "type": "int",
        "range": [1, 5],
        "step": 1,
        "description": "Maximum new order pairs per 30-second poll cycle. More = more opportunities captured but more capital deployed.",
    },
    "MIN_SECS_LEFT": {
        "type": "int",
        "range": [10, 120],
        "step": 10,
        "description": "Minimum seconds remaining in the 5-min window to place an order. Lower = trades closer to expiry (risky, less fill time). Higher = safer but misses late opportunities.",
    },
    "ASYMMETRY": {
        "type": "float",
        "range": [-3.0, 3.0],
        "step": 0.5,
        "description": "Shift in cents between Up and Down bids. Positive = bid more aggressively on Up side. Negative = more aggressive on Down. Zero = symmetric.",
    },
    "BID_SPREAD_BASE": {
        "type": "float",
        "range": [0.5, 6.0],
        "step": 0.5,
        "description": "Base spread in cents below implied price (before volatility/depth adjustment). Lower = more aggressive fills. Higher = more edge per fill.",
    },
    "DEPTH_MIN": {
        "type": "float",
        "range": [0.0, 50.0],
        "step": 5.0,
        "description": "Minimum orderbook depth (USD) required on each side to trade. Higher = only trade liquid markets. Zero = trade everything.",
    },
    "EDGE_SCALING": {
        "type": "bool",
        "range": [0, 1],
        "step": 1,
        "description": "If True, position size scales with edge quality (better edge = larger position). If False, fixed ORDER_SIZE_USD.",
    },
    "VOL_ADJUSTMENT": {
        "type": "bool",
        "range": [0, 1],
        "step": 1,
        "description": "If True, BID_SPREAD adjusts dynamically based on volatility and depth. If False, uses fixed BID_SPREAD_BASE.",
    },
}

# Legacy random fallback values
MUTATIONS_FALLBACK = [
    ("MAX_TOTAL_COST", [0.96, 0.97, 0.98, 0.985, 0.99, 0.995]),
    ("BID_SPREAD_BASE", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]),
    ("MIN_EDGE_CENTS", [0.2, 0.5, 1.0, 1.5, 2.0]),
    ("ORDER_SIZE_USD", [3, 5, 8, 10, 15, 20]),
    ("MAX_ORDERS_PER_POLL", [1, 2, 3, 5]),
    ("MIN_SECS_LEFT", [10, 20, 30, 60, 90]),
    ("ASYMMETRY", [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]),
    ("DEPTH_MIN", [0, 5, 10, 20, 30]),
]


# ─── Context gathering ───────────────────────────────────────────────

def _get_experiment_history(limit: int = 10) -> list:
    """Get recent experiment results from DB."""
    conn = get_db()
    rows = conn.execute("""
        SELECT id, hypothesis, baseline_rapr, test_rapr, baseline_pnl, test_pnl,
               improvement_pct, p_value, result, status,
               baseline_trades, test_trades
        FROM experiments
        ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_current_params(strategy_path: str) -> dict:
    """Read current parameter values from strategy.py."""
    params = {}
    try:
        with open(strategy_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                for param_name in PARAM_SPACE:
                    if stripped.startswith(f"{param_name}") and "=" in stripped:
                        parts = stripped.split("=", 1)
                        if len(parts) == 2:
                            val_str = parts[1].split("#")[0].strip()
                            try:
                                params[param_name] = eval(val_str)
                            except Exception:
                                pass
    except Exception:
        pass
    return params


def _get_market_stats() -> dict:
    """Get recent market statistics from polls table."""
    conn = get_db()

    # Average implied prices and spreads from last 100 polls
    stats_row = conn.execute("""
        SELECT
            COUNT(*) as poll_count,
            AVG(yes_mid) as avg_implied_up,
            AVG(no_mid) as avg_implied_down,
            AVG(spread_yes) as avg_spread_up,
            AVG(spread_no) as avg_spread_down,
            AVG(volatility_1h) as avg_volatility,
            AVG(depth_yes_usd) as avg_depth_up,
            AVG(depth_no_usd) as avg_depth_down
        FROM polls
        WHERE timestamp > datetime('now', '-60 minutes')
    """).fetchone()

    # Trade fill stats
    trade_stats = conn.execute("""
        SELECT
            COUNT(*) as total_orders,
            SUM(CASE WHEN filled = 1 THEN 1 ELSE 0 END) as arb_fills,
            SUM(CASE WHEN filled = 0 AND net_pnl != 0 THEN 1 ELSE 0 END) as partial_fills,
            AVG(net_pnl) as avg_pnl,
            SUM(net_pnl) as total_pnl,
            AVG(fees) as avg_fees
        FROM trades
        WHERE open_at > datetime('now', '-60 minutes')
    """).fetchone()

    conn.close()

    return {
        "polls": dict(stats_row) if stats_row else {},
        "trades": dict(trade_stats) if trade_stats else {},
    }


def _get_portfolio_stats() -> dict:
    """Get current portfolio state."""
    conn = get_db()
    row = conn.execute("SELECT * FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {}


# ─── LLM proposal ────────────────────────────────────────────────────

def _build_prompt(current_params: dict, history: list,
                  market_stats: dict, portfolio: dict) -> str:
    """Build the prompt for the LLM advisor."""

    # Format experiment history
    history_text = "No experiments yet."
    if history:
        lines = []
        for exp in history:
            status = exp.get("status", "?")
            improvement = exp.get("improvement_pct", 0) or 0
            p_val = exp.get("p_value", 1) or 1
            lines.append(
                f"  Exp #{exp['id']}: {exp.get('hypothesis', '?')[:80]} "
                f"| Result: {status} | Improvement: {improvement:+.1f}% "
                f"| p-value: {p_val:.3f} "
                f"| Trades: {exp.get('baseline_trades', 0)}/{exp.get('test_trades', 0)}"
            )
        history_text = "\n".join(lines)

    # Format current params
    params_text = "\n".join(
        f"  {k} = {v}  # {PARAM_SPACE[k]['description'][:80]}"
        for k, v in current_params.items()
    )

    # Format market stats
    mkt = market_stats.get("polls", {})
    trd = market_stats.get("trades", {})
    # Helper to safely get numeric values (DB may return None)
    def _n(d, k, default=0):
        v = d.get(k, default)
        return v if v is not None else default

    market_text = (
        f"  Polls (last 60 min): {_n(mkt, 'poll_count')}\n"
        f"  Avg implied Up/Down: {_n(mkt, 'avg_implied_up', 0.5):.3f} / {_n(mkt, 'avg_implied_down', 0.5):.3f}\n"
        f"  Avg spread Up/Down: {_n(mkt, 'avg_spread_up'):.4f} / {_n(mkt, 'avg_spread_down'):.4f}\n"
        f"  Avg volatility: {_n(mkt, 'avg_volatility', 0.03):.4f}\n"
        f"  Avg depth Up/Down: ${_n(mkt, 'avg_depth_up'):.0f} / ${_n(mkt, 'avg_depth_down'):.0f}\n"
        f"  Total orders: {_n(trd, 'total_orders')} | ARB fills: {_n(trd, 'arb_fills')} | "
        f"Partials: {_n(trd, 'partial_fills')}\n"
        f"  Avg PnL: ${_n(trd, 'avg_pnl'):.4f} | Total PnL: ${_n(trd, 'total_pnl'):.4f}"
    )

    # Portfolio
    port_text = (
        f"  Balance: ${portfolio.get('balance_usd', 1000):.2f}\n"
        f"  Total PnL: ${portfolio.get('total_pnl', 0):.4f}\n"
        f"  Total trades: {portfolio.get('total_trades', 0)}\n"
        f"  Win rate: {portfolio.get('winning_trades', 0)}/{portfolio.get('total_trades', 0) or 1}"
    )

    # Parameter space description
    space_text = "\n".join(
        f"  {k}: range [{p['range'][0]}, {p['range'][1]}] step {p['step']} ({p['type']})"
        for k, p in PARAM_SPACE.items()
    )

    prompt = f"""You are an expert quantitative trading researcher running autonomous experiments (Karpathy AutoResearch method) on a Polymarket limit-order arbitrage strategy.

The strategy places LIMIT BUY orders on BOTH sides (Up + Down) of 5-minute crypto markets. When both sides fill, profit is guaranteed (sum < $1.00). The goal is to maximize RAPR (Risk-Adjusted Profit Rate).

## Current Strategy Parameters:
{params_text}

## Parameter Space (valid ranges):
{space_text}

## Experiment History (most recent first):
{history_text}

## Market Conditions (last 60 min):
{market_text}

## Portfolio:
{port_text}

## Your Task:
Propose ONE parameter change that you believe will improve RAPR. Consider:
1. What worked/didn't work in past experiments
2. Current market conditions (spreads, volatility, fill rates)
3. The trade-off between fill rate and edge per trade
4. Avoid repeating failed experiments
5. If no experiments yet, start with the most impactful parameter

Respond with ONLY valid JSON (no markdown, no explanation outside JSON):
{{"param": "PARAMETER_NAME", "value": <numeric_value>, "reasoning": "Brief explanation of why this change should improve performance"}}

The value MUST be within the valid range for the parameter."""

    return prompt


def _extract_json(text: str) -> str:
    """Extract JSON object from LLM response, handling markdown and extra text."""
    import re

    # Try to find JSON between ``` markers (greedy to get full object)
    match = re.search(r'```(?:json)?\s*(\{.+?\})\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Find the first { and match to the corresponding }
    start = text.find('{')
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

    return text


def propose_mutation_llm(strategy_path: str) -> dict:
    """
    Use LLM to propose an intelligent strategy mutation.

    Returns:
        {"param": str, "value": number, "reasoning": str, "source": "llm"|"fallback"}
    """
    # Gather context
    current_params = _get_current_params(strategy_path)
    history = _get_experiment_history(limit=10)
    market_stats = _get_market_stats()
    portfolio = _get_portfolio_stats()

    # Try Gemini LLM first
    if HAS_GEMINI and GEMINI_API_KEY:
        try:
            client = genai.Client(api_key=GEMINI_API_KEY)
            prompt = _build_prompt(current_params, history, market_stats, portfolio)

            log(f"Asking Gemini ({GEMINI_MODEL}) for mutation proposal...")
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=1024,
                    temperature=0.7,
                    thinking_config=genai.types.ThinkingConfig(thinking_budget=0),
                ),
            )

            raw_text = response.text.strip()
            log(f"Raw response: {raw_text[:200]}")

            # Extract JSON from response (handle markdown, extra text, etc.)
            text = _extract_json(raw_text)
            result = json.loads(text)

            # Validate
            param = result.get("param", "")
            value = result.get("value")
            reasoning = result.get("reasoning", "")

            if param not in PARAM_SPACE:
                raise ValueError(f"Unknown parameter: {param}")

            spec = PARAM_SPACE[param]
            if spec["type"] == "int":
                value = int(value)
            else:
                value = float(value)

            # Clamp to range
            value = max(spec["range"][0], min(spec["range"][1], value))

            log(f"LLM proposes: {param} = {value}")
            log(f"Reasoning: {reasoning[:120]}")

            return {
                "param": param,
                "value": value,
                "reasoning": reasoning,
                "source": "llm",
            }

        except Exception as e:
            log(f"Gemini proposal failed: {e}")
            traceback.print_exc()

    # Fallback: random mutation
    return _propose_random(current_params)


def _propose_random(current_params: dict) -> dict:
    """Fallback: random parameter mutation."""
    param_name, values = random.choice(MUTATIONS_FALLBACK)
    value = random.choice(values)
    log(f"Random fallback: {param_name} = {value}")
    return {
        "param": param_name,
        "value": value,
        "reasoning": f"Random exploration of {param_name}",
        "source": "fallback",
    }


def apply_mutation(strategy_path: str, param: str, value) -> tuple:
    """
    Apply a parameter mutation to strategy.py.

    Returns: (old_value, new_value, hypothesis_string)
    """
    with open(strategy_path, "r", encoding="utf-8") as f:
        code = f.read()

    lines = code.split("\n")
    old_value = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{param}") and "=" in stripped:
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                old_val_str = parts[1].split("#")[0].strip()
                try:
                    old_value = eval(old_val_str)
                except Exception:
                    old_value = old_val_str

                indent = line[:len(line) - len(line.lstrip())]
                comment = ""
                if "#" in parts[1]:
                    comment = "  #" + parts[1].split("#", 1)[1]
                lines[i] = f"{indent}{param} = {value}{comment}"
                break

    new_code = "\n".join(lines)
    with open(strategy_path, "w", encoding="utf-8") as f:
        f.write(new_code)

    return old_value, value, f"Change {param} from {old_value} to {value}"
