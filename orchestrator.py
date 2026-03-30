"""
AutoResearch Polymarket - Orchestrator v2
Limit order arbitrage edition. Polls 5-min crypto markets, places simulated
limit orders on both sides, and runs Karpathy-style AutoResearch experiments.
"""

import sys
import os
import io
import json
import time
import importlib
import traceback
import random
from datetime import datetime, timezone

# Fix Windows encoding (safe for TeeLogger)
if hasattr(sys.stdout, 'buffer') and not isinstance(sys.stdout, io.TextIOWrapper):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer') and not isinstance(sys.stderr, io.TextIOWrapper):
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
from llm_advisor import propose_mutation_llm, apply_mutation

# ─── Configuration ──────────────────────────────────────────────────────

POLL_INTERVAL_SECS = 30      # Poll every 30 seconds
PHASE_DURATION_MINS = 60     # 60 min per phase (baseline or test)
COOLDOWN_MINS = 5            # 5 min between experiments
OBSERVE_MINS = 15            # 15 min initial observation (warmup)
MIN_TRADES_TO_EVALUATE = 3   # Minimum trades per arm


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}")


# ─── Dashboard data export ──────────────────────────────────────────────

def export_dashboard_data():
    conn = get_db()
    polls = [dict(r) for r in conn.execute(
        "SELECT * FROM polls ORDER BY id DESC LIMIT 500").fetchall()]
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT 200").fetchall()]
    portfolio = [dict(r) for r in conn.execute(
        "SELECT * FROM portfolio ORDER BY id DESC LIMIT 100").fetchall()]
    experiments = [dict(r) for r in conn.execute(
        "SELECT * FROM experiments ORDER BY id DESC LIMIT 50").fetchall()]
    markets = [dict(r) for r in conn.execute(
        "SELECT * FROM markets WHERE active=1").fetchall()]
    conn.close()

    data = {
        "generated_at": datetime.now().isoformat(),
        "polls": polls, "trades": trades, "portfolio": portfolio,
        "experiments": experiments, "markets": markets,
    }
    data_path = os.path.join(os.path.dirname(__file__), "data", "dashboard_data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, default=str)


# ─── Phase runner ───────────────────────────────────────────────────────

def run_phase(phase_name: str, duration_mins: float,
              trader: RealisticPaperTrader,
              experiment_id: int = None) -> list:
    """
    Run a polling phase for a fixed duration.
    Polls markets, runs strategy, simulates limit order fills.
    Returns list of all trades.
    """
    log(f"{'=' * 50}")
    log(f"PHASE: {phase_name.upper()} ({duration_mins} min)")
    log(f"{'=' * 50}")

    all_trades = []
    polls_done = 0
    orders_placed = 0
    arb_fills = 0
    partial_fills = 0
    start_time = time.time()
    end_time = start_time + duration_mins * 60

    while time.time() < end_time:
        cycle_start = time.time()

        try:
            # 1. Poll all coins
            observations = market_fetcher.poll_all_coins()
            polls_done += 1

            if not observations:
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
                    obs.get("up_best_bid", 0), obs.get("up_best_ask", 0),
                    obs.get("down_best_bid", 0), obs.get("down_best_ask", 0),
                    obs.get("implied_up", 0), obs.get("implied_down", 0),
                    obs.get("spread_yes", 0), obs.get("spread_no", 0),
                    obs.get("total_ask", 0), obs.get("total_bid", 0),
                    obs.get("gap", 0),
                    obs.get("up_depth", 0), obs.get("down_depth", 0),
                    obs.get("binance_price", 0), obs.get("volatility", 0),
                    experiment_id, phase_name,
                ))
            conn.commit()
            conn.close()

            # 2. Run strategy (get limit order decisions)
            config = {"phase": phase_name, "experiment_id": experiment_id}
            decisions = strategy.decide(observations, all_trades, config)

            # 3. Execute paper trades (simulate limit fills)
            for dec in decisions:
                obs = next((o for o in observations if o["coin"] == dec["coin"]), None)
                if not obs:
                    continue

                trade = trader.execute_limit_arb(
                    dec, obs, experiment_id=experiment_id, phase=phase_name
                )
                if trade:
                    all_trades.append(trade)
                    orders_placed += 1

                    if trade["arb_filled"]:
                        arb_fills += 1
                        log(f"  [ARB FILL] {trade['coin']} "
                            f"Up@{trade['bid_up']:.2f}+Down@{trade['bid_down']:.2f}"
                            f"={trade['total_cost']:.2f} "
                            f"pnl=${trade['net_pnl']:+.4f} "
                            f"(probs: {trade['fill_prob_up']:.0%}/{trade['fill_prob_down']:.0%})")
                    elif trade["filled_up"] or trade["filled_down"]:
                        partial_fills += 1
                        side = "Up" if trade["filled_up"] else "Down"
                        log(f"  [PARTIAL] {trade['coin']} only {side} filled "
                            f"pnl=${trade['net_pnl']:+.4f}")
                    else:
                        log(f"  [MISS] {trade['coin']} neither side filled "
                            f"(probs: {trade['fill_prob_up']:.0%}/{trade['fill_prob_down']:.0%})")

            # 4. Resolve pending (no-op in v2)
            trader.resolve_trades()

            # Status update every 5 polls
            if polls_done % 5 == 0:
                elapsed = (time.time() - start_time) / 60
                remaining = duration_mins - elapsed
                total_pnl = sum(t.get("net_pnl", 0) for t in all_trades)
                log(f"  --- Poll #{polls_done} | {elapsed:.1f}m elapsed, {remaining:.1f}m left | "
                    f"Orders: {orders_placed} | ARBs: {arb_fills} | Partial: {partial_fills} | "
                    f"PnL: ${total_pnl:+.4f} ---")

            if polls_done % 10 == 0:
                export_dashboard_data()

        except Exception as e:
            log(f"  [ERROR] Poll #{polls_done}: {e}")
            traceback.print_exc()

        elapsed = time.time() - cycle_start
        sleep_time = max(0, POLL_INTERVAL_SECS - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)

    # Phase complete
    total_pnl = sum(t.get("net_pnl", 0) for t in all_trades)
    hours = duration_mins / 60
    rapr = calculate_rapr(all_trades, hours)
    fill_rate = arb_fills / orders_placed * 100 if orders_placed > 0 else 0

    log(f"\n{'=' * 50}")
    log(f"PHASE {phase_name.upper()} COMPLETE")
    log(f"  Polls: {polls_done} | Orders placed: {orders_placed}")
    log(f"  ARB fills: {arb_fills} | Partial: {partial_fills} | Fill rate: {fill_rate:.0f}%")
    log(f"  PnL: ${total_pnl:+.6f} | RAPR: {rapr:.6f}")
    log(f"{'=' * 50}\n")

    return all_trades


# ─── Mutation system for AutoResearch (LLM-guided) ───────────────────

def _propose_mutation(experiment_num: int) -> str:
    """
    Propose a parameter mutation for the strategy using LLM advisor.
    Falls back to random if LLM is unavailable.
    """
    proposal = propose_mutation_llm(STRATEGY_PATH)

    old_value, new_value, hypothesis = apply_mutation(
        STRATEGY_PATH, proposal["param"], proposal["value"]
    )

    source = proposal["source"]  # "llm" or "fallback"
    reasoning = proposal.get("reasoning", "")

    if reasoning and source == "llm":
        hypothesis = f"[LLM] {hypothesis} | {reasoning[:150]}"
    else:
        hypothesis = f"[RANDOM] {hypothesis}"

    log(f"\n[MUTATION] ({source.upper()}) {hypothesis[:120]}")
    return hypothesis


# ─── Main loop ──────────────────────────────────────────────────────────

def main():
    log("=" * 60)
    log("  AUTORESEARCH POLYMARKET v2 - Limit Order Arbitrage")
    log("  5-min crypto markets | Karpathy AutoResearch loop")
    log("=" * 60)

    init_db()
    init_results_tsv()

    trader = RealisticPaperTrader()
    manager = ExperimentManager()

    log("\n[INIT] Discovering current 5-min markets...")
    markets = market_fetcher.discover_markets()
    if not markets:
        log("[FATAL] No markets found!")
        return

    conn = get_db()
    for coin, mkt in markets.items():
        conn.execute("""
            INSERT OR REPLACE INTO markets (coin, condition_id, question,
                token_id_yes, token_id_no, end_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (coin, mkt["condition_id"], mkt["question"],
              mkt["token_up"], mkt["token_down"], mkt["end_date"]))
    conn.commit()
    conn.close()

    log(f"\n[PHASE 0] Quick observation ({OBSERVE_MINS} min)...")
    run_phase("observe", OBSERVE_MINS, trader)
    export_dashboard_data()

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
            # Refresh markets before each experiment
            market_fetcher._market_cache = {}
            market_fetcher._cache_ts = 0

            log(f"\n[1/5] Running BASELINE ({PHASE_DURATION_MINS} min)...")
            baseline_trades = run_phase("baseline", PHASE_DURATION_MINS, trader,
                                         experiment_id=experiment_num)

            hypothesis = _propose_mutation(experiment_num)
            exp = manager.create_experiment(hypothesis)
            manager.start_experiment(exp)

            log(f"\n[3/5] Running TEST ({PHASE_DURATION_MINS} min)...")
            success = manager.transition_to_test(exp)
            if not success:
                time.sleep(COOLDOWN_MINS * 60)
                continue

            test_trades = run_phase("test", PHASE_DURATION_MINS, trader,
                                     experiment_id=experiment_num)

            log(f"\n[4/5] Evaluating...")
            hours = PHASE_DURATION_MINS / 60
            result = manager.evaluate_experiment(exp, baseline_trades, test_trades, hours, hours)

            keep = result.get("keep", False)
            if result["result"] == "confirm_needed":
                log(f"\n[CONFIRM] Running confirmation...")
                confirm_trades = run_phase("confirm", PHASE_DURATION_MINS, trader,
                                            experiment_id=experiment_num)
                confirm_result = manager.evaluate_experiment(exp, baseline_trades, confirm_trades, hours, hours)
                keep = confirm_result.get("keep", False)

            manager.finalize(exp, keep)
            log(f"\n  >>> Experiment #{experiment_num}: {'KEPT' if keep else 'DISCARDED'}")
            export_dashboard_data()

            log(f"\n[COOLDOWN] {COOLDOWN_MINS} min...")
            time.sleep(COOLDOWN_MINS * 60)

        except KeyboardInterrupt:
            log("\n[STOP] Interrupted")
            export_dashboard_data()
            break
        except Exception as e:
            log(f"\n[ERROR] Experiment #{experiment_num}: {e}")
            traceback.print_exc()
            revert_strategy()
            reload_strategy()
            time.sleep(60)

    stats = manager.get_stats()
    portfolio = trader.get_portfolio_summary()
    log(f"\n{'=' * 60}")
    log(f"  SESSION COMPLETE")
    log(f"  Experiments: {stats['total']} (kept={stats['kept']}, discarded={stats['reverted']})")
    log(f"  Portfolio: ${portfolio['balance']:.2f} (PnL: ${portfolio['total_pnl']:+.4f})")
    log(f"  Win Rate: {portfolio['win_rate']:.1f}%")
    log(f"{'=' * 60}")


if __name__ == "__main__":
    main()
