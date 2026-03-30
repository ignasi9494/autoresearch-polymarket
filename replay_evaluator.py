"""
AutoResearch Polymarket - Replay Evaluator

Instead of running baseline and test on different time windows (introducing
temporal bias), this module replays BOTH strategies on the SAME market data.

Flow:
1. Collect observations for N minutes (single data collection phase)
2. Replay strategy_default over those observations -> simulated trades
3. Replay strategy_mutated over those observations -> simulated trades
4. Compare both on identical data -> fair comparison

This eliminates the #1 source of noise: market conditions changing between
baseline and test phases.
"""

import importlib
import math
import random
from datetime import datetime
from paper_trader import limit_fill_probability, estimate_fee, GAS_COST


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] [REPLAY] {msg}")


def replay_strategy(strategy_module, observations_by_poll: list,
                    seed: int = 42) -> list:
    """
    Replay a strategy module over a sequence of observation snapshots.

    Args:
        strategy_module: The strategy module (must have .decide())
        observations_by_poll: List of lists. Each inner list is one poll's
                              observations (one per coin).
        seed: Random seed for reproducible fill simulation.

    Returns:
        List of simulated trade dicts.
    """
    rng = random.Random(seed)
    all_trades = []
    history = []

    for poll_idx, observations in enumerate(observations_by_poll):
        if not observations:
            continue

        config = {"phase": "replay", "experiment_id": None}
        try:
            decisions = strategy_module.decide(observations, history, config)
        except Exception as e:
            log(f"  Strategy error at poll {poll_idx}: {e}")
            continue

        for dec in decisions:
            obs = next((o for o in observations if o["coin"] == dec["coin"]), None)
            if not obs:
                continue

            trade = _simulate_trade(dec, obs, rng)
            if trade:
                all_trades.append(trade)
                history.append(trade)

    return all_trades


def _simulate_trade(decision: dict, observation: dict,
                    rng: random.Random) -> dict:
    """
    Simulate a single limit order arb trade using the fill probability model.
    Same logic as RealisticPaperTrader but uses provided RNG for reproducibility.
    """
    coin = decision["coin"]
    bid_up = decision["bid_up"]
    bid_down = decision["bid_down"]
    size_usd = decision.get("size_usd", 5.0)

    if size_usd < 1.0:
        return None

    implied_up = observation.get("implied_up", 0.5)
    implied_down = observation.get("implied_down", 0.5)
    secs_left = observation.get("secs_left", 300)
    volatility = observation.get("volatility", 0.03)

    prob_up = limit_fill_probability(bid_up, implied_up, secs_left, volatility)
    prob_down = limit_fill_probability(bid_down, implied_down, secs_left, volatility)

    filled_up = rng.random() < prob_up
    filled_down = rng.random() < prob_down
    both_filled = filled_up and filled_down

    total_cost = bid_up + bid_down
    fee_up = estimate_fee(bid_up)
    fee_down = estimate_fee(bid_down)
    total_fees = (fee_up + fee_down) + GAS_COST * 2

    if both_filled:
        net_pnl = (1.0 - total_cost) / total_cost * size_usd * 2 - total_fees * size_usd / total_cost
    elif filled_up and not filled_down:
        if rng.random() < implied_up:
            net_pnl = (1.0 - bid_up) / bid_up * size_usd - fee_up * size_usd / bid_up
        else:
            net_pnl = -size_usd - fee_up * size_usd / bid_up
    elif filled_down and not filled_up:
        if rng.random() < implied_down:
            net_pnl = (1.0 - bid_down) / bid_down * size_usd - fee_down * size_usd / bid_down
        else:
            net_pnl = -size_usd - fee_down * size_usd / bid_down
    else:
        net_pnl = 0.0

    return {
        "coin": coin,
        "size_usd": size_usd,
        "bid_up": bid_up,
        "bid_down": bid_down,
        "total_cost": total_cost,
        "fees": total_fees,
        "net_pnl": net_pnl,
        "filled": filled_up or filled_down,
        "arb_filled": both_filled,
        "filled_up": filled_up,
        "filled_down": filled_down,
        "fill_prob_up": prob_up,
        "fill_prob_down": prob_down,
        "implied_up": implied_up,
        "implied_down": implied_down,
        "edge": decision.get("edge", 0),
        "reason": decision.get("reason", ""),
    }


def compare_replay(observations_by_poll: list,
                   baseline_module, test_module,
                   seed: int = 42) -> dict:
    """
    Run both strategies on the same data and compare.

    Returns dict with baseline_trades, test_trades, and comparison stats.
    """
    log(f"Replaying baseline strategy over {len(observations_by_poll)} polls...")
    baseline_trades = replay_strategy(baseline_module, observations_by_poll, seed=seed)

    log(f"Replaying test strategy over {len(observations_by_poll)} polls...")
    test_trades = replay_strategy(test_module, observations_by_poll, seed=seed)

    # Summary
    b_filled = [t for t in baseline_trades if t.get("filled")]
    t_filled = [t for t in test_trades if t.get("filled")]
    b_arb = [t for t in baseline_trades if t.get("arb_filled")]
    t_arb = [t for t in test_trades if t.get("arb_filled")]

    b_pnl = sum(t["net_pnl"] for t in baseline_trades)
    t_pnl = sum(t["net_pnl"] for t in test_trades)

    log(f"  Baseline: {len(b_filled)} filled ({len(b_arb)} arb), PnL=${b_pnl:+.4f}")
    log(f"  Test:     {len(t_filled)} filled ({len(t_arb)} arb), PnL=${t_pnl:+.4f}")

    return {
        "baseline_trades": baseline_trades,
        "test_trades": test_trades,
        "baseline_filled": len(b_filled),
        "test_filled": len(t_filled),
        "baseline_arb": len(b_arb),
        "test_arb": len(t_arb),
        "baseline_pnl": b_pnl,
        "test_pnl": t_pnl,
    }
