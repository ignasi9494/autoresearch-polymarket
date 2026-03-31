"""
AutoResearch Polymarket - LLM Advisor v6
Agentic multi-turn chat with Gemini 3.1 Pro for intelligent parameter mutations.
Also provides random fallback when LLM is disabled or fails.
"""

import os
import json
import random
import re
import traceback
from datetime import datetime

# Load .env BEFORE anything else
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

try:
    from google import genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

from db import get_db


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] [LLM] {msg}")


# ─── Config ──────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.1-pro-preview"


# ─── Parameter Space (20 mutable params + USE_LLM switch) ───────
PARAM_SPACE = {
    "MAX_TOTAL_COST":      {"values": [0.96, 0.97, 0.975, 0.98, 0.985, 0.99, 0.995],
                            "desc": "Max combined bid Up+Down. Lower=more edge, fewer fills. Higher=more fills, thinner edge."},
    "BID_SPREAD_BASE":     {"values": [0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0],
                            "desc": "Base spread cents below mid (before vol/depth adjust). Lower=aggressive. Higher=conservative."},
    "ASYMMETRY":           {"values": [-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0],
                            "desc": "Shift cents between Up/Down bids. Positive=aggressive on Up. Negative=aggressive on Down."},
    "VOL_REFERENCE":       {"values": [0.01, 0.02, 0.03, 0.04, 0.05],
                            "desc": "Reference volatility for dynamic spread. When actual vol > this, spread tightens."},
    "DEPTH_DIVISOR":       {"values": [50, 75, 100, 150, 200, 300],
                            "desc": "Depth normalization divisor. Lower=depth matters more. Higher=depth matters less."},
    "SPREAD_CLAMP_MIN":    {"values": [0.2, 0.3, 0.5, 0.8, 1.0],
                            "desc": "Floor on dynamic spread (cents). Prevents being too aggressive."},
    "SPREAD_CLAMP_MAX":    {"values": [3.0, 4.0, 5.0, 7.0, 10.0],
                            "desc": "Ceiling on dynamic spread (cents). Prevents being too conservative."},
    "MIN_EDGE_CENTS":      {"values": [0.1, 0.2, 0.3, 0.4, 0.5, 0.8, 1.0],
                            "desc": "Min profit per trade after fees (cents). Lower=more trades. Higher=only profitable trades."},
    "MIN_SECS_LEFT":       {"values": [10, 20, 30, 45, 60, 90, 120],
                            "desc": "Min seconds left in window to trade. Lower=trade late. Higher=only trade early."},
    "DEPTH_MIN":           {"values": [1, 3, 5, 10, 20, 30],
                            "desc": "Min orderbook depth USD per side. Filters illiquid markets."},
    "MAX_IMPLIED_SKEW":    {"values": [0.05, 0.10, 0.15, 0.20, 0.30, 0.40],
                            "desc": "Max |Up-Down| implied diff. Filters skewed markets where arb is unlikely."},
    "MIN_VOLATILITY":      {"values": [0.0, 0.005, 0.01, 0.015, 0.02, 0.03],
                            "desc": "Min volatility to trade. Skips dead/stable markets with low fill probability."},
    "ORDER_SIZE_USD":      {"values": [2, 3, 5, 8, 10, 15, 20],
                            "desc": "Base USD per side. Larger=more profit per fill but more capital at risk."},
    "EDGE_SCALE_BASE":     {"values": [0.002, 0.003, 0.005, 0.008, 0.01, 0.015],
                            "desc": "Edge divisor for position scaling. Lower=more aggressive scaling on good edges."},
    "MAX_SIZE_MULTIPLIER": {"values": [1.0, 1.5, 2.0, 3.0, 4.0, 5.0],
                            "desc": "Cap on edge scaling. Max position = ORDER_SIZE_USD * this."},
    "MAX_EXPOSURE_PCT":    {"values": [0.2, 0.3, 0.4, 0.5, 0.6, 0.8],
                            "desc": "Max fraction of balance as total exposure. Safety limit."},
    "SKIP_FIRST_N_POLLS":  {"values": [0, 1, 2, 3, 4],
                            "desc": "Skip first N polls per window. Lets prices stabilize before trading."},
    "POLL_DELAY_SECS":     {"values": [0, 2, 5, 8, 10],
                            "desc": "Extra delay seconds before each decision. More time for orderbook data."},
    "MAX_ORDERS_PER_POLL": {"values": [1, 2, 3, 4, 5],
                            "desc": "Max order pairs per poll. More=more opportunities but more capital deployed."},
    "VOL_ADJUSTMENT":      {"values": [0, 1],
                            "desc": "1=dynamic spread by volatility/depth, 0=fixed BID_SPREAD_BASE."},
    "USE_LLM":             {"values": [0, 1],
                            "desc": "1=Gemini agentic mutations, 0=pure random mutations."},
}


# ─── Context Gathering ───────────────────────────────────────────

def _get_current_params(strategy_path):
    params = {}
    with open(strategy_path, "r", encoding="utf-8") as f:
        code = f.read()
    for param in PARAM_SPACE:
        match = re.search(rf'^{re.escape(param)}\s*=\s*(.+?)(?:\s*#|$)', code, re.MULTILINE)
        if match:
            try:
                params[param] = eval(match.group(1).strip())
            except Exception:
                params[param] = match.group(1).strip()
    return params


def _get_experiment_history(limit=20):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, hypothesis, baseline_pnl, test_pnl,
               improvement_pct, p_value, result, status,
               baseline_trades, test_trades
        FROM experiments ORDER BY id DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_market_stats():
    conn = get_db()
    polls = conn.execute("""
        SELECT AVG(yes_mid) as avg_up, AVG(no_mid) as avg_down,
               AVG(spread_yes) as avg_spread_up, AVG(spread_no) as avg_spread_down,
               AVG(volatility_1h) as avg_vol,
               AVG(depth_yes_usd) as avg_depth_up, AVG(depth_no_usd) as avg_depth_down,
               COUNT(*) as poll_count
        FROM polls WHERE id > (SELECT MAX(id)-200 FROM polls)
    """).fetchone()
    trades = conn.execute("""
        SELECT COUNT(*) as total, SUM(CASE WHEN filled=1 THEN 1 ELSE 0 END) as arbs,
               AVG(net_pnl) as avg_pnl, SUM(net_pnl) as total_pnl
        FROM trades WHERE id > (SELECT MAX(id)-500 FROM trades)
    """).fetchone()
    conn.close()
    return {"polls": dict(polls) if polls else {}, "trades": dict(trades) if trades else {}}


def _get_portfolio_stats():
    conn = get_db()
    row = conn.execute("SELECT * FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(row) if row else {}


def _fmt_params(params):
    lines = []
    for k, v in params.items():
        desc = PARAM_SPACE.get(k, {}).get("desc", "")
        lines.append(f"  {k} = {v}  # {desc[:70]}")
    return "\n".join(lines)


def _fmt_space():
    lines = []
    for k, spec in PARAM_SPACE.items():
        lines.append(f"  {k}: allowed={spec['values']}")
    return "\n".join(lines)


def _fmt_history(history):
    if not history:
        return "No experiments yet."
    lines = []
    for e in history:
        hyp = (e.get("hypothesis") or "")[:65]
        lines.append(
            f"  #{e['id']} {e.get('status','?'):10s} test=${e.get('test_pnl',0) or 0:+.2f} "
            f"base=${e.get('baseline_pnl',0) or 0:+.2f} p={e.get('p_value',1) or 1:.3f} "
            f"trades={e.get('baseline_trades',0)}/{e.get('test_trades',0)} | {hyp}"
        )
    return "\n".join(lines)


def _fmt_market(stats):
    p = stats.get("polls", {})
    t = stats.get("trades", {})
    return (
        f"  Avg Up/Down: {p.get('avg_up',0.5) or 0.5:.3f}/{p.get('avg_down',0.5) or 0.5:.3f}\n"
        f"  Avg spread: {p.get('avg_spread_up',0) or 0:.4f}/{p.get('avg_spread_down',0) or 0:.4f}\n"
        f"  Avg vol: {p.get('avg_vol',0.03) or 0.03:.4f}\n"
        f"  Avg depth: ${p.get('avg_depth_up',0) or 0:.0f}/${p.get('avg_depth_down',0) or 0:.0f}\n"
        f"  Recent: {t.get('total',0)} trades, {t.get('arbs',0)} ARBs, PnL=${t.get('total_pnl',0) or 0:.2f}"
    )


def _fmt_portfolio(port):
    return (
        f"  Balance: ${port.get('balance_usd',1000):.2f} | "
        f"PnL: ${port.get('total_pnl',0):.2f} | "
        f"Trades: {port.get('total_trades',0)}"
    )


def _extract_json(text):
    """Extract JSON from LLM response (handles multiline, markdown, etc)."""
    # Try markdown code block (multiline)
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Find first { and match to corresponding }
    start = text.find('{')
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{': depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        pass
                    break
    # Last resort: try to find any JSON-like structure
    for m in re.finditer(r'\{[^{}]*\}', text):
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
    # Handle truncated JSON: if starts with { but no closing }, try to fix
    if start is not None and start != -1:
        partial = text[start:]
        # Truncate at last complete key-value and close
        partial = partial.rstrip()
        if not partial.endswith('}'):
            # Find last complete quoted value
            last_quote = partial.rfind('"')
            if last_quote > 0:
                partial = partial[:last_quote+1] + '}'
                try:
                    return json.loads(partial)
                except json.JSONDecodeError:
                    pass
            # Try simpler: just close it
            partial = text[start:].rstrip().rstrip(',') + '}'
            try:
                return json.loads(partial)
            except json.JSONDecodeError:
                pass
    raise ValueError(f"No JSON found in: {text[:200]}")


# ─── System Prompt ────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert quantitative trader and researcher optimizing a Polymarket limit-order arbitrage strategy (Karpathy AutoResearch method).

HOW IT WORKS:
- LIMIT BUY on BOTH sides (Up+Down) of 5-minute crypto markets (BTC, ETH, SOL, XRP, DOGE)
- Both fill = guaranteed profit (paid < $1.00, receive $1.00)
- One fills = cancel other, lose only gas ($0.008)
- Neither fills = no cost

YOUR JOB: Analyze everything, then propose ONE parameter change to improve profitability.

RULES:
- Change ONLY ONE parameter per experiment
- Set it ONLY to a value from the allowed list
- NEVER modify code, only parameter values
- Plan your reasoning steps (1 to 5 max), then execute
- Be conservative: avoid catastrophic changes
- Learn from history: don't repeat failures, build on successes

IMPORTANT OUTPUT FORMAT: Always respond with a SINGLE LINE of valid JSON. No markdown, no code blocks, no backticks, no newlines inside the JSON. Just raw JSON on one line."""


# ─── Agentic Propose (multi-turn chat with Gemini 3.1 Pro) ──────

def agentic_propose(strategy_path):
    """
    Multi-turn chat with Gemini 3.1 Pro. The LLM decides how many steps
    it needs (1-5). Each step sees all previous reasoning (chat context).
    """
    if not HAS_GEMINI or not GEMINI_API_KEY:
        raise RuntimeError("Gemini not available")

    client = genai.Client(api_key=GEMINI_API_KEY)
    current_params = _get_current_params(strategy_path)
    history = _get_experiment_history(limit=20)
    market_stats = _get_market_stats()
    portfolio = _get_portfolio_stats()
    with open(strategy_path, "r", encoding="utf-8") as f:
        strategy_code = f.read()

    chat = client.chats.create(
        model=GEMINI_MODEL,
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=2048,
            temperature=0.3,
        ),
    )

    # STEP 1: Full context + plan
    msg1 = f"""COMPLETE CONTEXT:

## Strategy Code:
```python
{strategy_code}
```

## Current Parameters:
{_fmt_params(current_params)}

## Allowed Values Per Parameter:
{_fmt_space()}

## Experiment History (last 20):
{_fmt_history(history)}

## Market Conditions:
{_fmt_market(market_stats)}

## Portfolio:
{_fmt_portfolio(portfolio)}

---
Plan your analysis (1-5 steps). Keep responses SHORT.
JSON: {{"plan": "brief plan", "steps_needed": N, "initial_thoughts": "brief"}}"""

    log(f"Step 1: Context -> {GEMINI_MODEL}...")
    r1 = chat.send_message(msg1)
    step1 = _extract_json(r1.text)
    steps_needed = min(5, max(1, int(step1.get("steps_needed", 3))))
    log(f"Step 1 done. Plans {steps_needed} steps. Thoughts: {str(step1.get('initial_thoughts',''))[:100]}")

    # STEPS 2..N
    final_result = None
    for step in range(2, steps_needed + 1):
        if step < steps_needed:
            msg = (f"Step {step}/{steps_needed}. Continue your analysis. "
                   f"Which parameter are you leaning towards and why? "
                   f"JSON: {{\"step\": {step}, \"analysis\": \"...\", "
                   f"\"leaning_towards\": {{\"param\": \"...\", \"value\": ...}}}}")
        else:
            msg = (f"FINAL step ({step}/{steps_needed}). Decide NOW.\n"
                   f"Params: {list(PARAM_SPACE.keys())}\n"
                   f"Current: {json.dumps({k:v for k,v in current_params.items()}, default=str)}\n"
                   f"JSON: {{\"param\": \"NAME\", \"value\": <number>, "
                   f"\"reasoning\": \"...\", \"confidence\": \"low|medium|high\"}}")

        log(f"Step {step}/{steps_needed}...")
        r = chat.send_message(msg)
        result = _extract_json(r.text)
        log(f"Step {step}: {json.dumps(result, default=str)[:120]}")
        if step == steps_needed:
            final_result = result

    # Validate
    param = final_result.get("param", "")
    value = final_result.get("value")
    if param not in PARAM_SPACE:
        raise ValueError(f"Invalid param: {param}")
    allowed = PARAM_SPACE[param]["values"]
    if value not in allowed:
        try:
            value = min(allowed, key=lambda v: abs(float(v) - float(value)))
        except (TypeError, ValueError):
            value = allowed[len(allowed) // 2]

    log(f"DECISION: {param} = {value} (confidence: {final_result.get('confidence','?')})")
    return {
        "param": param, "value": value,
        "reasoning": final_result.get("reasoning", ""),
        "source": "llm", "steps_used": steps_needed,
        "confidence": final_result.get("confidence", "unknown"),
    }


# ─── Random Propose ──────────────────────────────────────────────

def random_propose():
    param = random.choice([k for k in PARAM_SPACE if k != "USE_LLM"])
    value = random.choice(PARAM_SPACE[param]["values"])
    log(f"Random: {param} = {value}")
    return {"param": param, "value": value,
            "reasoning": f"Random exploration of {param}", "source": "random"}


# ─── Apply Mutation ──────────────────────────────────────────────

def apply_mutation(strategy_path, param, value):
    with open(strategy_path, "r", encoding="utf-8") as f:
        code = f.read()
    lines = code.split("\n")
    old_value = None
    pattern = re.compile(rf'^(\s*){re.escape(param)}\s*=')
    for i, line in enumerate(lines):
        if pattern.match(line):
            parts = line.strip().split("=", 1)
            if len(parts) == 2:
                old_str = parts[1].split("#")[0].strip()
                try: old_value = eval(old_str)
                except: old_value = old_str
                indent = line[:len(line)-len(line.lstrip())]
                comment = ""
                if "#" in parts[1]:
                    comment = "  #" + parts[1].split("#",1)[1]
                lines[i] = f"{indent}{param} = {value}{comment}"
                break
    new_code = "\n".join(lines)
    try:
        compile(new_code, strategy_path, "exec")
    except SyntaxError as e:
        log(f"REJECTED: {param} syntax error: {e}")
        return old_value, old_value, f"REJECTED: {param} syntax error"
    with open(strategy_path, "w", encoding="utf-8") as f:
        f.write(new_code)
    return old_value, value, f"Change {param} from {old_value} to {value}"
