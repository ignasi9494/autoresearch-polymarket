"""
AutoResearch Polymarket - Orchestrator
The main autonomous research loop. Polls markets, runs strategy, manages experiments.
Inspired by Karpathy's AutoResearch: modify -> test -> evaluate -> keep/discard -> repeat.
"""

import sys
import os
import io
import json
import time
import importlib
import traceback
from datetime import datetime, timezone

# Fix Windows encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from db import get_db, init_db
import market_fetcher
import strategy
from paper_trader import RealisticPaperTrader
from scorer import calculate_rapr, format_comparison
from experiment_manager import (
    ExperimentManager, reload_strategy, revert_strategy,
    save_strategy_version, init_results_tsv, STRATEGY_PATH
)

# ─── Configuration ──────────────────────────────────────────────────────

POLL_INTERVAL_SECS = 30      # Poll every 30 seconds
PHASE_DURATION_MINS = 30     # 30 min per phase (baseline or test)
COOLDOWN_MINS = 5            # 5 min between experiments
OBSERVE_MINS = 5             # Initial observation (short, just to verify APIs)
MIN_TRADES_TO_EVALUATE = 3   # Minimum trades per arm for evaluation


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


# ─── Dashboard data export ──────────────────────────────────────────────

def export_dashboard_data():
    """Export all data as JSON for the dashboard."""
    conn = get_db()

    # Recent polls
    polls = [dict(r) for r in conn.execute(
        "SELECT * FROM polls ORDER BY id DESC LIMIT 500"
    ).fetchall()]

    # All trades
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT 200"
    ).fetchall()]

    # Portfolio history
    portfolio = [dict(r) for r in conn.execute(
        "SELECT * FROM portfolio ORDER BY id DESC LIMIT 100"
    ).fetchall()]

    # Experiments
    experiments = [dict(r) for r in conn.execute(
        "SELECT * FROM experiments ORDER BY id DESC LIMIT 50"
    ).fetchall()]

    # Markets
    markets = [dict(r) for r in conn.execute(
        "SELECT * FROM markets WHERE active=1"
    ).fetchall()]

    conn.close()

    data = {
        "generated_at": datetime.now().isoformat(),
        "polls": polls,
        "trades": trades,
        "portfolio": portfolio,
        "experiments": experiments,
        "markets": markets,
    }

    data_path = os.path.join(os.path.dirname(__file__), "data", "dashboard_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)


# ─── Phase runner ───────────────────────────────────────────────────────

def run_phase(phase_name: str, duration_mins: float,
              trader: RealisticPaperTrader,
              experiment_id: int = None) -> list:
    """
    Run a polling phase (baseline or test) for a fixed duration.
    Returns list of all trades executed during this phase.
    """
    log(f"{'=' * 50}")
    log(f"PHASE: {phase_name.upper()} ({duration_mins} min)")
    log(f"{'=' * 50}")

    all_trades = []
    polls_done = 0
    opportunities_seen = 0
    start_time = time.time()
    end_time = start_time + duration_mins * 60

    while time.time() < end_time:
        cycle_start = time.time()

        try:
            # 1. Poll all 5 coins
            observations = market_fetcher.poll_all_coins()
            polls_done += 1

            if not observations:
                log(f"  Poll #{polls_done}: no data")
                time.sleep(POLL_INTERVAL_SECS)
                continue

            # Save polls to DB
            conn = get_db()
            for obs in observations:
                conn.execute("""
                    INSERT INTO polls (coin, condition_id, yes_bid, yes_ask,
                        no_bid, no_ask, yes_mid, no_mid, spread_yes, spread_no,
                        total_ask, total_bid, gap, depth_yes_usd, depth_no_usd,
                        binance_price, volatility_1h, experiment_id, phase)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    obs["coin"], obs["condition_id"],
                    obs["yes_bid"], obs["yes_ask"],
                    obs["no_bid"], obs["no_ask"],
                    obs["yes_mid"], obs["no_mid"],
                    obs["spread_yes"], obs["spread_no"],
                    obs["total_ask"], obs["total_bid"], obs["gap"],
                    obs["depth_yes_usd"], obs["depth_no_usd"],
                    obs["binance_price"], obs["volatility_1h"],
                    experiment_id, phase_name,
                ))
            conn.commit()
            conn.close()

            # 2. Run strategy
            config = {"phase": phase_name, "experiment_id": experiment_id}
            decisions = strategy.decide(observations, all_trades, config)

            # Log opportunities
            for obs in observations:
                if obs["gap"] > 0:
                    opportunities_seen += 1

            # 3. Execute paper trades
            trades_this_poll = 0
            for dec in decisions:
                # Find matching observation
                obs = next((o for o in observations if o["coin"] == dec["coin"]), None)
                if not obs:
                    continue

                trade = trader.execute_binary_arb(
                    dec, obs, experiment_id=experiment_id, phase=phase_name
                )
                if trade:
                    all_trades.append(trade)
                    trades_this_poll += 1
                    status = "FILLED" if trade["filled"] else "NOT FILLED"
                    log(f"  [{status}] {trade['coin']} "
                        f"cost={trade['total_cost']:.4f} "
                        f"pnl=${trade['net_pnl']:+.4f} "
                        f"fees=${trade['fees']:.4f}")

            # 4. Resolve pending trades
            resolved = trader.resolve_trades()

            # Status update every 5 polls
            if polls_done % 5 == 0:
                elapsed = (time.time() - start_time) / 60
                remaining = duration_mins - elapsed
                filled = sum(1 for t in all_trades if t.get("filled", True))
                total_pnl = sum(t.get("net_pnl", 0) for t in all_trades if t.get("filled"))
                log(f"  --- Poll #{polls_done} | {elapsed:.1f}m elapsed, {remaining:.1f}m left | "
                    f"Trades: {filled} | PnL: ${total_pnl:+.4f} | "
                    f"Opportunities: {opportunities_seen} ---")

            # Export dashboard data periodically
            if polls_done % 10 == 0:
                export_dashboard_data()

        except Exception as e:
            log(f"  [ERROR] Poll #{polls_done}: {e}")
            traceback.print_exc()

        # Wait for next poll
        elapsed = time.time() - cycle_start
        sleep_time = max(0, POLL_INTERVAL_SECS - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Phase complete
    filled_trades = [t for t in all_trades if t.get("filled", True)]
    total_pnl = sum(t.get("net_pnl", 0) for t in filled_trades)
    hours = duration_mins / 60
    rapr = calculate_rapr(all_trades, hours)

    log(f"\n{'─' * 50}")
    log(f"PHASE {phase_name.upper()} COMPLETE")
    log(f"  Polls: {polls_done} | Opportunities: {opportunities_seen}")
    log(f"  Trades: {len(filled_trades)} filled / {len(all_trades)} total")
    log(f"  PnL: ${total_pnl:+.6f} | RAPR: {rapr:.6f}")
    log(f"{'─' * 50}\n")

    return all_trades


# ─── Main loop ──────────────────────────────────────────────────────────

def main():
    log("=" * 60)
    log("  AUTORESEARCH POLYMARKET - Binary Arbitrage")
    log("  Karpathy-style autonomous research loop")
    log("=" * 60)

    # Initialize
    init_db()
    init_results_tsv()

    trader = RealisticPaperTrader()
    manager = ExperimentManager()

    # Discover markets
    log("\n[INIT] Discovering 5-min markets...")
    markets = market_fetcher.discover_markets()
    if not markets:
        log("[FATAL] No markets found! Check internet connection and Polymarket API.")
        return

    # Save discovered markets to DB
    conn = get_db()
    for coin, mkt in markets.items():
        conn.execute("""
            INSERT OR REPLACE INTO markets (coin, condition_id, question,
                token_id_yes, token_id_no, end_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (coin, mkt["condition_id"], mkt["question"],
              mkt["token_yes"], mkt["token_no"], mkt["end_date"]))
    conn.commit()
    conn.close()

    log(f"  Markets ready: {list(markets.keys())}")

    # Phase 0: Quick observation (verify everything works)
    log(f"\n[PHASE 0] Quick observation ({OBSERVE_MINS} min)...")
    observe_trades = run_phase("observe", OBSERVE_MINS, trader)

    export_dashboard_data()
    log(f"  Observation complete. System verified.")

    # Main experiment loop
    experiment_num = 0
    log("\n" + "=" * 60)
    log("  STARTING AUTONOMOUS RESEARCH LOOP")
    log("=" * 60)

    while True:
        experiment_num += 1
        log(f"\n{'#' * 60}")
        log(f"  EXPERIMENT #{experiment_num}")
        log(f"{'#' * 60}")

        try:
            # --- Step 1: Baseline (run current strategy) ---
            log(f"\n[1/5] Running BASELINE ({PHASE_DURATION_MINS} min)...")
            baseline_trades = run_phase(
                "baseline", PHASE_DURATION_MINS, trader,
                experiment_id=experiment_num
            )

            # --- Step 2: The agent proposes a change ---
            # In the full Karpathy setup, this is where Claude Code / Codex
            # would modify strategy.py. For autonomous operation, we use a
            # simple parameter mutation system.
            hypothesis = _propose_mutation(experiment_num)
            exp = manager.create_experiment(hypothesis)
            manager.start_experiment(exp)

            # --- Step 3: Test (run modified strategy) ---
            log(f"\n[3/5] Running TEST ({PHASE_DURATION_MINS} min)...")
            success = manager.transition_to_test(exp)
            if not success:
                log("  Strategy crashed - skipping to next experiment")
                time.sleep(COOLDOWN_MINS * 60)
                continue

            test_trades = run_phase(
                "test", PHASE_DURATION_MINS, trader,
                experiment_id=experiment_num
            )

            # --- Step 4: Evaluate ---
            log(f"\n[4/5] Evaluating experiment #{experiment_num}...")
            hours = PHASE_DURATION_MINS / 60
            result = manager.evaluate_experiment(
                exp, baseline_trades, test_trades, hours, hours
            )

            # --- Step 5: Decide ---
            keep = result.get("keep", False)

            if result["result"] == "confirm_needed":
                log(f"\n[CONFIRM] Result too good - running confirmation...")
                confirm_trades = run_phase(
                    "confirm", PHASE_DURATION_MINS, trader,
                    experiment_id=experiment_num
                )
                confirm_result = manager.evaluate_experiment(
                    exp, baseline_trades, confirm_trades, hours, hours
                )
                keep = confirm_result.get("keep", False)

            manager.finalize(exp, keep)

            status_emoji = "KEPT" if keep else "DISCARDED"
            log(f"\n  >>> Experiment #{experiment_num}: {status_emoji}")
            log(f"  >>> RAPR: {result['rapr_baseline']:.6f} -> {result['rapr_test']:.6f} "
                f"({result.get('improvement_pct', 0):+.1f}%)")

            # Export dashboard
            export_dashboard_data()

            # --- Cooldown ---
            log(f"\n[COOLDOWN] Waiting {COOLDOWN_MINS} min before next experiment...")
            time.sleep(COOLDOWN_MINS * 60)

        except KeyboardInterrupt:
            log("\n[STOP] Interrupted by user")
            export_dashboard_data()
            break
        except Exception as e:
            log(f"\n[ERROR] Experiment #{experiment_num} failed: {e}")
            traceback.print_exc()
            revert_strategy()
            reload_strategy()
            time.sleep(60)

    # Final stats
    stats = manager.get_stats()
    portfolio = trader.get_portfolio_summary()
    log(f"\n{'=' * 60}")
    log(f"  RESEARCH SESSION COMPLETE")
    log(f"  Experiments: {stats['total']} (kept={stats['kept']}, "
        f"discarded={stats['reverted']}, crashed={stats['crashed']})")
    log(f"  Portfolio: ${portfolio['balance']:.2f} (PnL: ${portfolio['total_pnl']:+.4f})")
    log(f"  Win Rate: {portfolio['win_rate']:.1f}%")
    log(f"{'=' * 60}")


# ─── Simple mutation system ─────────────────────────────────────────────
# In the full setup, Claude Code modifies strategy.py directly.
# This fallback provides basic parameter mutations for standalone operation.

import random

MUTATIONS = [
    ("MIN_GAP_CENTS", [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 5.0]),
    ("MAX_SPREAD", [0.02, 0.03, 0.04, 0.05, 0.08, 0.10]),
    ("MIN_DEPTH_USD", [10, 25, 50, 100, 200]),
    ("ORDER_SIZE_USD", [5, 10, 15, 20, 50]),
    ("MAX_TOTAL_COST", [0.98, 0.985, 0.99, 0.995, 0.998]),
    ("MAX_TRADES_PER_POLL", [1, 2, 3, 5]),
]

_mutation_history = set()


def _propose_mutation(experiment_num: int) -> str:
    """
    Propose a simple parameter mutation.
    Reads current strategy.py, changes one parameter, writes back.
    Returns hypothesis string.
    """
    code = open(STRATEGY_PATH, "r", encoding="utf-8").read()

    # Pick a random parameter to mutate
    attempts = 0
    while attempts < 20:
        param_name, values = random.choice(MUTATIONS)
        new_value = random.choice(values)
        key = (param_name, new_value)
        if key not in _mutation_history:
            _mutation_history.add(key)
            break
        attempts += 1

    # Find and replace the parameter in strategy.py
    lines = code.split("\n")
    old_value = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{param_name}") and "=" in stripped:
            # Extract old value
            parts = stripped.split("=", 1)
            if len(parts) == 2:
                old_val_str = parts[1].split("#")[0].strip()
                try:
                    old_value = eval(old_val_str)
                except Exception:
                    old_value = old_val_str

                # Replace with new value
                indent = line[:len(line) - len(line.lstrip())]
                comment = ""
                if "#" in parts[1]:
                    comment = "  #" + parts[1].split("#", 1)[1]
                lines[i] = f"{indent}{param_name} = {new_value}{comment}"
                break

    new_code = "\n".join(lines)
    with open(STRATEGY_PATH, "w", encoding="utf-8") as f:
        f.write(new_code)

    hypothesis = f"Change {param_name} from {old_value} to {new_value}"
    log(f"\n[MUTATION] {hypothesis}")
    return hypothesis


if __name__ == "__main__":
    main()
