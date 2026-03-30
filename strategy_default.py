"""
AutoResearch Polymarket - Default Strategy (BACKUP)
This file is NEVER modified directly. Used to restore strategy.py when an experiment is discarded.
"""

MAX_TOTAL_COST = 0.98
BID_SPREAD = 2.0
MIN_EDGE_CENTS = 0.5
ORDER_SIZE_USD = 5.0
MAX_ORDERS_PER_POLL = 2
MIN_SECS_LEFT = 30
COINS_TO_TRADE = None
ASYMMETRY = 0.0


def estimate_fee(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return price * (1 - price) * 0.022


def decide(observations: list, history: list, config: dict) -> list:
    orders = []
    for obs in observations:
        coin = obs["coin"]
        if COINS_TO_TRADE and coin not in COINS_TO_TRADE:
            continue
        secs_left = obs.get("secs_left", 300)
        if secs_left < MIN_SECS_LEFT:
            continue
        implied_up = obs.get("implied_up", 0.5)
        implied_down = obs.get("implied_down", 0.5)
        if implied_up <= 0.01 or implied_down <= 0.01:
            continue
        spread_up = (BID_SPREAD + ASYMMETRY) / 100.0
        spread_down = (BID_SPREAD - ASYMMETRY) / 100.0
        bid_up = round(max(0.01, min(0.99, implied_up - spread_up)), 2)
        bid_down = round(max(0.01, min(0.99, implied_down - spread_down)), 2)
        total_cost = bid_up + bid_down
        if total_cost > MAX_TOTAL_COST:
            excess = total_cost - MAX_TOTAL_COST
            bid_up = round(bid_up - excess / 2, 2)
            bid_down = round(bid_down - excess / 2, 2)
            total_cost = bid_up + bid_down
        if total_cost >= 1.0:
            continue
        fee_up = estimate_fee(bid_up)
        fee_down = estimate_fee(bid_down)
        total_fees = fee_up + fee_down
        edge = 1.0 - total_cost - total_fees
        if edge < MIN_EDGE_CENTS / 100.0:
            continue
        orders.append({
            "coin": coin, "action": "LIMIT_BOTH",
            "bid_up": bid_up, "bid_down": bid_down,
            "size_usd": ORDER_SIZE_USD, "total_cost": total_cost,
            "fees": total_fees, "edge": edge,
            "implied_up": implied_up, "implied_down": implied_down,
            "secs_left": secs_left,
            "reason": f"Up:{bid_up:.2f}+Down:{bid_down:.2f}={total_cost:.2f} edge={edge:.4f}",
        })
        if len(orders) >= MAX_ORDERS_PER_POLL:
            break
    orders.sort(key=lambda x: -x["edge"])
    return orders
