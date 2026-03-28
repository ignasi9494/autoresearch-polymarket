"""
AutoResearch Polymarket - Realistic Paper Trader
Simulates trades as if real money were at stake.
Accounts for: orderbook fills, slippage, exact fees, fill probability, latency.
"""

import math
import random
import time
from datetime import datetime
from db import get_db


# Polymarket fee formula: fee_rate = price * (1 - price) * 0.022 per side
GAS_COST = 0.005  # ~$0.005 per tx on Polygon


def estimate_fee(price: float) -> float:
    """Exact Polymarket fee for one side."""
    if price <= 0 or price >= 1:
        return 0.0
    return price * (1 - price) * 0.022


def walk_orderbook(asks: list, size_usd: float) -> float:
    """
    Walk the orderbook to find the real fill price for a given order size.
    Returns the volume-weighted average fill price.
    More realistic than using best_ask - large orders eat into deeper levels.
    """
    if not asks:
        return 0.0

    remaining = size_usd
    total_cost = 0.0
    total_shares = 0.0

    for level in asks:
        price = float(level.get("price", 0))
        size = float(level.get("size", 0))
        if price <= 0 or size <= 0:
            continue

        level_value = size * price
        if level_value >= remaining:
            # This level can fill the rest
            shares = remaining / price
            total_cost += remaining
            total_shares += shares
            remaining = 0
            break
        else:
            # Consume entire level
            total_cost += level_value
            total_shares += size
            remaining -= level_value

    if total_shares <= 0:
        return 0.0

    # VWAP = volume-weighted average price
    vwap = total_cost / total_shares
    return vwap


def estimate_slippage(depth_usd: float, order_size: float) -> float:
    """
    Estimate slippage based on order size relative to orderbook depth.
    Small orders in deep books = tiny slippage.
    Large orders in thin books = significant slippage.
    """
    if depth_usd <= 0:
        return 0.005  # 0.5% default if no depth data

    ratio = order_size / depth_usd
    # Slippage model: quadratic in ratio, capped at 2%
    slippage = min(ratio * ratio * 0.5, 0.02)
    return max(slippage, 0.001)  # Minimum 0.1% (market impact always exists)


def fill_probability(spread: float, depth_usd: float, size_usd: float) -> float:
    """
    Probability that a market order actually fills at the expected price.
    Wide spreads and thin books = lower fill probability.
    """
    if spread <= 0 or depth_usd <= 0:
        return 0.5

    # Base probability from spread (tighter = better)
    spread_factor = max(0.3, 1.0 - spread * 10)  # spread=0.01 -> 0.9, spread=0.05 -> 0.5

    # Depth factor (deeper = better)
    depth_factor = min(1.0, depth_usd / max(size_usd * 3, 50))

    prob = spread_factor * depth_factor
    return max(0.3, min(0.98, prob))


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

        self.pending_trades = []  # Trades waiting for window resolution

    def execute_binary_arb(self, decision: dict, observation: dict,
                           experiment_id: int = None, phase: str = None) -> dict:
        """
        Execute a binary arbitrage trade (buy both YES and NO).
        Uses real orderbook data for realistic simulation.
        Returns trade dict or None if not filled.
        """
        coin = decision["coin"]
        size_usd = decision.get("size_usd", 10.0)
        max_total_cost = decision.get("max_total_cost", 0.995)

        # Safety: don't exceed balance
        if size_usd > self.balance * 0.5:
            size_usd = self.balance * 0.3
        if size_usd < 1.0:
            return None

        half_size = size_usd / 2

        # 1. Walk orderbook for YES side
        yes_ob = observation.get("orderbook_yes", {})
        no_ob = observation.get("orderbook_no", {})

        yes_asks = yes_ob.get("asks", [])
        no_asks = no_ob.get("asks", [])

        if yes_asks:
            fill_yes = walk_orderbook(yes_asks, half_size)
        else:
            fill_yes = observation.get("yes_ask", 0)

        if no_asks:
            fill_no = walk_orderbook(no_asks, half_size)
        else:
            fill_no = observation.get("no_ask", 0)

        if fill_yes <= 0 or fill_no <= 0:
            return None

        # 2. Add slippage
        depth_yes = observation.get("depth_yes_usd", 0)
        depth_no = observation.get("depth_no_usd", 0)
        slip_yes = estimate_slippage(depth_yes, half_size)
        slip_no = estimate_slippage(depth_no, half_size)
        fill_yes += fill_yes * slip_yes
        fill_no += fill_no * slip_no
        total_slippage = (slip_yes + slip_no) * half_size

        # 3. Check fill probability
        spread_yes = observation.get("spread_yes", 0)
        spread_no = observation.get("spread_no", 0)
        avg_spread = (spread_yes + spread_no) / 2
        avg_depth = (depth_yes + depth_no) / 2
        fill_prob = fill_probability(avg_spread, avg_depth, size_usd)

        # Simulate latency (price might move 200-500ms)
        latency_impact = random.uniform(0, 0.002)  # 0-0.2% price movement
        fill_yes += fill_yes * latency_impact * random.choice([-1, 1])
        fill_no += fill_no * latency_impact * random.choice([-1, 1])

        # Clamp to valid range
        fill_yes = max(0.01, min(0.99, fill_yes))
        fill_no = max(0.01, min(0.99, fill_no))

        # 4. Calculate fees (EXACT Polymarket formula)
        fee_yes = estimate_fee(fill_yes) * (half_size / fill_yes) if fill_yes > 0 else 0
        fee_no = estimate_fee(fill_no) * (half_size / fill_no) if fill_no > 0 else 0
        total_fees = fee_yes + fee_no + GAS_COST * 2

        # 5. Total cost
        total_cost = fill_yes + fill_no  # Per-share cost
        total_outlay = size_usd + total_fees + total_slippage  # Total $ spent

        # 6. Check if arb still exists after ALL costs
        payout_per_share = 1.0
        shares = size_usd / total_cost if total_cost > 0 else 0
        gross_payout = shares * payout_per_share
        net_pnl = gross_payout - total_outlay

        # Check max cost threshold
        if total_cost > max_total_cost:
            return None

        # 7. Simulate fill (random based on probability)
        filled = random.random() < fill_prob

        trade = {
            "coin": coin,
            "size_usd": size_usd,
            "fill_yes": fill_yes,
            "fill_no": fill_no,
            "total_cost": total_cost,
            "fees": total_fees,
            "slippage": total_slippage,
            "net_pnl": net_pnl if filled else 0,
            "filled": filled,
            "fill_probability": fill_prob,
            "reason": decision.get("reason", ""),
            "window_end": observation.get("end_date", ""),
            "experiment_id": experiment_id,
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
        }

        if filled:
            # Deduct from balance (money is locked until window resolves)
            self.balance -= total_outlay
            self.pending_trades.append(trade)

        # Save to DB
        conn = get_db()
        conn.execute("""
            INSERT INTO trades (experiment_id, phase, coin, size_usd, fill_yes, fill_no,
                total_cost, fees, slippage, net_pnl, filled, reason, window_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            experiment_id, phase, coin, size_usd, fill_yes, fill_no,
            total_cost, total_fees, total_slippage, trade["net_pnl"],
            1 if filled else 0, trade["reason"], trade["window_end"],
        ))
        conn.commit()
        conn.close()

        return trade

    def resolve_trades(self):
        """
        Resolve pending trades (window has ended, payout = $1.00).
        In binary arb, ALL trades that filled are winners (payout = $1 guaranteed).
        """
        resolved = []
        still_pending = []

        for trade in self.pending_trades:
            # In real system, we'd check if the window has ended.
            # For simulation, we resolve after recording.
            # The P&L was already calculated at entry time.
            self.balance += trade["size_usd"] + trade["net_pnl"]
            self.total_pnl += trade["net_pnl"]
            self.total_trades += 1
            self.total_fees += trade["fees"]
            if trade["net_pnl"] > 0:
                self.winning += 1
            else:
                self.losing += 1

            trade["resolved"] = True
            resolved.append(trade)

        self.pending_trades = still_pending

        if resolved:
            # Update portfolio in DB
            conn = get_db()
            conn.execute("""
                INSERT INTO portfolio (balance_usd, total_pnl, total_trades,
                    total_fees, winning_trades, losing_trades)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (self.balance, self.total_pnl, self.total_trades,
                  self.total_fees, self.winning, self.losing))

            # Mark trades as resolved
            for t in resolved:
                conn.execute("""
                    UPDATE trades SET resolved=1, resolved_at=datetime('now')
                    WHERE coin=? AND open_at=? AND resolved=0
                """, (t["coin"], t.get("timestamp", "")))
            conn.commit()
            conn.close()

        return resolved

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
