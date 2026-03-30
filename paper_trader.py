"""
AutoResearch Polymarket - Paper Trader v2 (Limit Order Simulation)

Simulates LIMIT ORDER arbitrage on 5-minute "Up or Down" markets.
Key difference from v1: orders don't fill instantly. They sit in the book
and fill when the market price moves to our level.

Fill probability depends on:
- Distance from implied price (closer = more likely to fill)
- Time remaining in window (more time = more chances)
- Volatility (higher vol = more price movement = more fills)
"""

import math
import random
import time
from datetime import datetime
from db import get_db


# Polymarket fee formula
def estimate_fee(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return price * (1 - price) * 0.022


GAS_COST = 0.008  # ~$0.008 per tx on Polygon (conservative estimate)
EXECUTION_DELAY_SECS = 0.5  # Latency: observation -> order on book


def limit_fill_probability(bid_price: float, implied_price: float,
                           secs_left: float, volatility: float) -> float:
    """
    Estimate probability that a limit buy order fills during the window.

    A limit buy at $0.48 fills when someone SELLS at $0.48 (market sell).
    The closer our bid is to the implied price, the more likely it fills.

    Factors:
    - distance: how far our bid is from implied (closer = more likely)
    - time: more time remaining = more chances for price to hit our level
    - volatility: higher vol = more price swings = more fills

    Returns probability [0.0 - 0.95]
    """
    if implied_price <= 0 or bid_price <= 0:
        return 0.0

    # Account for execution latency: we observe now but order hits book later
    effective_secs_left = secs_left - EXECUTION_DELAY_SECS
    if effective_secs_left <= 0:
        return 0.0  # No time for fill after latency

    # Distance from midmarket (in cents)
    distance = (implied_price - bid_price) * 100  # positive = bid below mid

    if distance <= 0:
        # Bid is AT or ABOVE implied → very high fill chance
        return 0.90

    # Base fill probability based on distance
    # 0 cents away → ~90% fill
    # 1 cent away → ~70% fill
    # 2 cents away → ~50% fill
    # 3 cents away → ~30% fill
    # 5+ cents away → very low
    base_prob = math.exp(-0.35 * distance)

    # Time factor: more time = more price movement = more fills
    # 300 sec (full window) = 1.0x, 60 sec = 0.5x, 10 sec = 0.1x
    time_factor = min(1.0, effective_secs_left / 300.0)
    time_factor = max(0.1, time_factor ** 0.5)  # sqrt for diminishing returns

    # Volatility factor: higher vol = more movement
    # BTC daily vol ~3% → 5-min vol ~0.2%
    # Scale: 3% daily → 1.0x, 5% → 1.3x, 1% → 0.6x
    vol_factor = min(1.5, max(0.5, volatility / 0.03))

    prob = base_prob * time_factor * vol_factor
    return max(0.02, min(0.95, prob))


class RealisticPaperTrader:
    def __init__(self, starting_balance: float = 1000.0):
        conn = get_db()
        row = conn.execute("SELECT * FROM portfolio ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            self.balance = row["balance_usd"]
            self.total_pnl = row["total_pnl"]
            self.total_trades = row["total_trades"]
            self.total_fees = row["total_fees"]
            self.winning = row["winning_trades"]
            self.losing = row["losing_trades"]
        else:
            self.balance = starting_balance
            self.total_pnl = 0.0
            self.total_trades = 0
            self.total_fees = 0.0
            self.winning = 0
            self.losing = 0
        conn.close()

        self.open_orders = []  # Orders waiting to fill

    def execute_limit_arb(self, decision: dict, observation: dict,
                          experiment_id: int = None, phase: str = None) -> dict:
        """
        Place a LIMIT ORDER pair (buy Up + buy Down).
        Simulates whether each side fills based on fill probability.
        Returns trade result dict.
        """
        coin = decision["coin"]
        bid_up = decision["bid_up"]
        bid_down = decision["bid_down"]
        size_usd = decision.get("size_usd", 5.0)

        # Safety: don't exceed balance
        total_exposure = size_usd * 2  # Both sides
        if total_exposure > self.balance * 0.5:
            size_usd = self.balance * 0.2
        if size_usd < 1.0:
            return None

        # Calculate fill probabilities for each side
        implied_up = observation.get("implied_up", 0.5)
        implied_down = observation.get("implied_down", 0.5)
        secs_left = observation.get("secs_left", 300)
        volatility = observation.get("volatility", 0.03)

        prob_up = limit_fill_probability(bid_up, implied_up, secs_left, volatility)
        prob_down = limit_fill_probability(bid_down, implied_down, secs_left, volatility)

        # Simulate fills (independent for each side)
        filled_up = random.random() < prob_up
        filled_down = random.random() < prob_down

        # Both must fill for arbitrage
        both_filled = filled_up and filled_down

        # Calculate costs and edge
        total_cost = bid_up + bid_down
        fee_up = estimate_fee(bid_up)
        fee_down = estimate_fee(bid_down)
        total_fees = (fee_up + fee_down) + GAS_COST * 2

        if both_filled:
            # ARBITRAGE: we own both sides → guaranteed $1.00 payout
            payout = 1.0
            shares = size_usd / total_cost  # shares of each token
            gross = shares * payout
            net_pnl = gross - size_usd * 2 - total_fees * (size_usd / total_cost)
            # Simpler: net = (1/total_cost - 1) * size_usd * 2 - fees
            net_pnl = (1.0 - total_cost) / total_cost * size_usd * 2 - total_fees * size_usd / total_cost

        elif filled_up and not filled_down:
            # PARTIAL: cancel opposite side immediately, no directional risk
            # Only cost is gas for the one filled order
            net_pnl = -GAS_COST

        elif filled_down and not filled_up:
            # PARTIAL: cancel opposite side immediately, no directional risk
            net_pnl = -GAS_COST
        else:
            # Neither filled → no trade, no cost
            net_pnl = 0.0

        filled = filled_up or filled_down  # At least one side filled
        arb_filled = both_filled

        trade = {
            "coin": coin,
            "size_usd": size_usd,
            "bid_up": bid_up,
            "bid_down": bid_down,
            "total_cost": total_cost,
            "fees": total_fees,
            "net_pnl": net_pnl,
            "filled": filled,
            "arb_filled": arb_filled,
            "filled_up": filled_up,
            "filled_down": filled_down,
            "fill_prob_up": prob_up,
            "fill_prob_down": prob_down,
            "implied_up": implied_up,
            "implied_down": implied_down,
            "edge": decision.get("edge", 0),
            "reason": decision.get("reason", ""),
            "window_end": observation.get("end_date", ""),
            "experiment_id": experiment_id,
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
        }

        # Update balance
        if both_filled:
            self.balance -= size_usd * 2  # Lock both sides
            self.balance += size_usd * 2 + net_pnl  # Immediate resolution (paper)
            self.total_pnl += net_pnl
            self.total_trades += 1
            self.total_fees += total_fees * size_usd / total_cost
            self.winning += 1  # Arb is always a win
        elif filled_up or filled_down:
            # PARTIAL: only gas cost lost (opposite side cancelled)
            self.balance += net_pnl  # net_pnl = -GAS_COST = -$0.008
            self.total_pnl += net_pnl
            self.total_trades += 1
            self.losing += 1  # Minor loss (gas only)

        # Save to DB
        conn = get_db()
        conn.execute("""
            INSERT INTO trades (experiment_id, phase, coin, size_usd, fill_yes, fill_no,
                total_cost, fees, slippage, net_pnl, filled, reason, window_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            experiment_id, phase, coin, size_usd, bid_up, bid_down,
            total_cost, total_fees, 0,  # no slippage concept for limit orders
            net_pnl, 1 if arb_filled else 0,
            f"{'ARB' if arb_filled else 'PARTIAL' if filled else 'MISS'}: {trade['reason']}",
            trade["window_end"],
        ))
        conn.commit()

        # Update portfolio
        if filled:
            conn.execute("""
                INSERT INTO portfolio (balance_usd, total_pnl, total_trades,
                    total_fees, winning_trades, losing_trades)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (self.balance, self.total_pnl, self.total_trades,
                  self.total_fees, self.winning, self.losing))
            conn.commit()

        conn.close()
        return trade

    # Legacy compatibility
    def execute_binary_arb(self, decision: dict, observation: dict,
                           experiment_id: int = None, phase: str = None) -> dict:
        """Legacy wrapper - converts old-style decisions to new limit order format."""
        if decision.get("action") == "LIMIT_BOTH":
            return self.execute_limit_arb(decision, observation, experiment_id, phase)
        # Old BUY_BOTH format - convert
        decision["bid_up"] = observation.get("implied_up", 0.5) - 0.02
        decision["bid_down"] = observation.get("implied_down", 0.5) - 0.02
        return self.execute_limit_arb(decision, observation, experiment_id, phase)

    def resolve_trades(self):
        """No-op for v2 (trades resolve immediately in paper mode)."""
        return []

    def get_portfolio_summary(self) -> dict:
        win_rate = (self.winning / self.total_trades * 100) if self.total_trades > 0 else 0
        return {
            "balance": self.balance,
            "total_pnl": self.total_pnl,
            "total_trades": self.total_trades,
            "total_fees": self.total_fees,
            "winning": self.winning,
            "losing": self.losing,
            "win_rate": win_rate,
        }
