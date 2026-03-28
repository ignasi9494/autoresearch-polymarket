"""
Default strategy backup. This file is NEVER modified.
Used to restore strategy.py when an experiment is discarded.
"""

MIN_GAP_CENTS = 1.5
MAX_SPREAD = 0.04
MIN_DEPTH_USD = 50
ORDER_SIZE_USD = 10
MAX_TOTAL_COST = 0.99
MAX_TRADES_PER_POLL = 2
COINS_TO_TRADE = None


def decide(observations: list, history: list, config: dict) -> list:
    trades = []
    for obs in observations:
        if COINS_TO_TRADE and obs["coin"] not in COINS_TO_TRADE:
            continue
        gap = obs["gap"]
        if gap < MIN_GAP_CENTS / 100:
            continue
        if obs["spread_yes"] > MAX_SPREAD:
            continue
        if obs["spread_no"] > MAX_SPREAD:
            continue
        if obs["depth_yes_usd"] < MIN_DEPTH_USD:
            continue
        if obs["depth_no_usd"] < MIN_DEPTH_USD:
            continue
        if obs["total_ask"] > MAX_TOTAL_COST:
            continue
        trades.append({
            "coin": obs["coin"],
            "action": "BUY_BOTH",
            "size_usd": ORDER_SIZE_USD,
            "max_total_cost": MAX_TOTAL_COST,
            "reason": f"gap={gap:.4f} total={obs['total_ask']:.4f}",
        })
        if len(trades) >= MAX_TRADES_PER_POLL:
            break
    return trades
