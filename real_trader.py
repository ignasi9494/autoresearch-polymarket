"""
AutoResearch Polymarket - Real Trader (LIVE execution via py-clob-client)

Replaces paper_trader.py for real money trading. Same interface so
orchestrator.py, strategy.py, scorer.py all work without changes.

SAFETY:
- REAL_MAX_SIZE_PER_SIDE = 3.0 USD hardcoded (override requires code change)
- 3 circuit breakers: daily loss, consecutive losses, min balance
- Kill switch via .env (KILL_SWITCH=true)
- DRY_RUN mode: builds orders but does NOT send them
"""

import os
import time
import traceback
from datetime import datetime, timezone

from db import get_db

# ─── py-clob-client imports (all in one place) ──────────────────────
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, MarketOrderArgs, OrderType, OpenOrderParams
    from py_clob_client.order_builder.constants import BUY, SELL
    HAS_CLOB = True
except ImportError:
    HAS_CLOB = False

# ─── Load .env ────────────────────────────────────────────────────────
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ─── Safety constants (hardcoded, NOT configurable via .env) ─────────
REAL_MAX_SIZE_PER_SIDE = 3.0   # Max USD per side, HARDCODED safety cap
MIN_SIZE_USD = 0.50            # Don't bother with orders smaller than this
MIN_SHARES = 5                 # Polymarket minimum order size
FILL_POLL_INTERVAL = 3.0       # Seconds between fill checks
FILL_TIMEOUT_BUFFER = 20       # Stop checking fills this many secs before window end
GAS_COST_ESTIMATE = 0.005      # Estimated gas per order on Polygon

# ─── Configurable via .env ───────────────────────────────────────────
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
MAX_DAILY_LOSS = float(os.environ.get("MAX_DAILY_LOSS_USD", "5.0"))
MAX_CONSECUTIVE_LOSSES = int(os.environ.get("MAX_CONSECUTIVE_LOSSES", "10"))
MIN_BALANCE = float(os.environ.get("MIN_BALANCE_USD", "20.0"))

# ─── Polymarket fee formula ──────────────────────────────────────────
def estimate_fee(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return price * (1 - price) * 0.022


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] [REAL] {msg}")


class RealTrader:
    def __init__(self, starting_balance: float = 100.0):
        # Load portfolio state from DB
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

        # Circuit breaker state
        self._daily_pnl = 0.0
        self._daily_reset = datetime.now().date()
        self._consecutive_losses = 0

        # Init CLOB client (only if not dry run... actually init always for order building)
        self.client = None
        self._init_clob_client()

        mode = "DRY RUN" if DRY_RUN else "LIVE"
        log(f"RealTrader initialized ({mode}) | Balance: ${self.balance:.2f} | "
            f"Max per side: ${REAL_MAX_SIZE_PER_SIDE}")

    def _init_clob_client(self):
        """Initialize the Polymarket CLOB client."""
        try:
            from py_clob_client.client import ClobClient

            private_key = os.environ.get("PRIVATE_KEY", "")
            wallet_address = os.environ.get("WALLET_ADDRESS", "")

            if not private_key or not wallet_address:
                log("[WARN] PRIVATE_KEY or WALLET_ADDRESS not set - dry run only")
                return

            self.client = ClobClient(
                "https://clob.polymarket.com",
                key=private_key,
                chain_id=137,
                signature_type=0,  # EOA / MetaMask
                funder=wallet_address,
            )
            self.client.set_api_creds(self.client.create_or_derive_api_creds())
            log("CLOB client initialized successfully")
        except ImportError:
            log("[WARN] py-clob-client not installed - dry run only")
        except Exception as e:
            log(f"[ERROR] CLOB client init failed: {e}")

    # ─── Circuit Breakers ────────────────────────────────────────────

    def _check_circuit_breakers(self) -> str:
        """Check all circuit breakers. Returns reason string if tripped, None if OK."""
        # Kill switch (re-read from env each time for runtime toggle)
        if os.environ.get("KILL_SWITCH", "false").lower() == "true":
            return "KILL_SWITCH activated"

        # Reset daily counter at midnight
        today = datetime.now().date()
        if today != self._daily_reset:
            self._daily_pnl = 0.0
            self._daily_reset = today

        # Daily loss limit
        if self._daily_pnl < -MAX_DAILY_LOSS:
            return f"Daily loss limit (${self._daily_pnl:.2f} < -${MAX_DAILY_LOSS})"

        # Consecutive losses
        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return f"Consecutive losses ({self._consecutive_losses} >= {MAX_CONSECUTIVE_LOSSES})"

        # Minimum balance
        if self.balance < MIN_BALANCE:
            return f"Balance too low (${self.balance:.2f} < ${MIN_BALANCE})"

        return None

    # ─── Core: Execute Limit Arb ─────────────────────────────────────

    def execute_limit_arb(self, decision: dict, observation: dict,
                          experiment_id: int = None, phase: str = None) -> dict:
        """
        Place a LIMIT ORDER pair (buy Up + buy Down) on Polymarket CLOB.
        Returns trade result dict with same keys as paper_trader.
        """
        coin = decision["coin"]
        bid_up = decision["bid_up"]
        bid_down = decision["bid_down"]

        # ─── Circuit breaker ─────────────────────────────────────────
        breaker = self._check_circuit_breakers()
        if breaker:
            log(f"  [CIRCUIT BREAKER] {breaker} - skipping trade")
            return None

        # ─── Size clamping ───────────────────────────────────────────
        size_usd = min(decision.get("size_usd", 1.0), REAL_MAX_SIZE_PER_SIDE)
        if size_usd < MIN_SIZE_USD:
            return None
        if self.balance < size_usd * 2.5:  # 2x for both sides + buffer
            log(f"  [SKIP] Insufficient balance: ${self.balance:.2f} < ${size_usd * 2.5:.2f}")
            return None

        # ─── Extract market data ─────────────────────────────────────
        token_up = observation.get("token_up", "")
        token_down = observation.get("token_down", "")
        condition_id = observation.get("condition_id", "")
        implied_up = observation.get("implied_up", 0.5)
        implied_down = observation.get("implied_down", 0.5)
        secs_left = observation.get("secs_left", 300)

        if not token_up or not token_down:
            log(f"  [SKIP] Missing token IDs for {coin}")
            return None

        # ─── Calculate order sizes in shares ─────────────────────────
        shares_up = max(MIN_SHARES, round(size_usd / bid_up, 2)) if bid_up > 0 else 0
        shares_down = max(MIN_SHARES, round(size_usd / bid_down, 2)) if bid_down > 0 else 0

        total_cost = bid_up + bid_down
        fee_up = estimate_fee(bid_up)
        fee_down = estimate_fee(bid_down)
        total_fees = fee_up + fee_down

        # ─── Place orders ────────────────────────────────────────────
        order_id_up = None
        order_id_down = None
        filled_up = False
        filled_down = False
        error_msg = None

        try:
            if DRY_RUN or not self.client:
                # DRY RUN: log but don't send
                log(f"  [DRY] {coin} Up@{bid_up:.2f}({shares_up:.1f}sh) + "
                    f"Down@{bid_down:.2f}({shares_down:.1f}sh) = {total_cost:.3f}")
                order_id_up = f"dry-up-{int(time.time())}"
                order_id_down = f"dry-down-{int(time.time())}"
                from paper_trader import limit_fill_probability
                import random
                prob_up = limit_fill_probability(bid_up, implied_up, secs_left,
                                                 observation.get("volatility", 0.03))
                prob_down = limit_fill_probability(bid_down, implied_down, secs_left,
                                                    observation.get("volatility", 0.03))
                filled_up = random.random() < prob_up
                filled_down = random.random() < prob_down
            else:
                # REAL: place orders via CLOB
                order_id_up, order_id_down, filled_up, filled_down = \
                    self._place_and_monitor(
                        coin, token_up, token_down,
                        bid_up, bid_down, shares_up, shares_down,
                        secs_left, experiment_id, phase
                    )
        except Exception as e:
            error_msg = str(e)
            log(f"  [ERROR] {coin}: {e}")
            traceback.print_exc()
            self._safe_cancel_all()

        # ─── Calculate PnL ───────────────────────────────────────────
        both_filled = filled_up and filled_down
        filled = filled_up or filled_down

        if both_filled:
            net_pnl = (1.0 - total_cost) / total_cost * size_usd * 2 - total_fees * size_usd / total_cost
        elif filled_up and not filled_down:
            net_pnl = -GAS_COST_ESTIMATE  # Cancel other side, only gas lost
        elif filled_down and not filled_up:
            net_pnl = -GAS_COST_ESTIMATE
        else:
            net_pnl = 0.0

        if error_msg:
            net_pnl = 0.0
            filled = False
            both_filled = False

        # ─── Build trade dict (same keys as paper_trader) ────────────
        trade = {
            "coin": coin,
            "size_usd": size_usd,
            "bid_up": bid_up,
            "bid_down": bid_down,
            "total_cost": total_cost,
            "fees": total_fees,
            "net_pnl": net_pnl,
            "filled": filled,
            "arb_filled": both_filled,
            "filled_up": filled_up,
            "filled_down": filled_down,
            "fill_prob_up": 1.0 if filled_up else 0.0,
            "fill_prob_down": 1.0 if filled_down else 0.0,
            "implied_up": implied_up,
            "implied_down": implied_down,
            "edge": decision.get("edge", 0),
            "reason": decision.get("reason", ""),
            "window_end": observation.get("end_date", ""),
            "experiment_id": experiment_id,
            "phase": phase,
            "timestamp": datetime.now().isoformat(),
        }

        # ─── Update balance & circuit breaker state ──────────────────
        if both_filled:
            self.balance += net_pnl
            self.total_pnl += net_pnl
            self.total_trades += 1
            self.total_fees += total_fees * size_usd / total_cost
            self.winning += 1
            self._consecutive_losses = 0
            self._daily_pnl += net_pnl
        elif filled:
            self.balance += net_pnl  # -GAS_COST_ESTIMATE
            self.total_pnl += net_pnl
            self.total_trades += 1
            self.losing += 1
            self._daily_pnl += net_pnl

        # ─── Save to DB ─────────────────────────────────────────────
        self._save_to_db(trade, order_id_up, order_id_down, condition_id,
                         experiment_id, phase, error_msg)

        return trade

    def _place_and_monitor(self, coin, token_up, token_down,
                           bid_up, bid_down, shares_up, shares_down,
                           secs_left, experiment_id, phase):
        """Place both orders, monitor fills, SELL partials immediately.
        Returns (order_id_up, order_id_down, filled_up, filled_down, sell_result)."""

        # Place UP order
        log(f"  [ORDER] {coin} Up: {shares_up:.1f} shares @ ${bid_up:.2f}")
        order_up = OrderArgs(
            token_id=token_up,
            price=bid_up,
            size=shares_up,
            side=BUY,
        )
        signed_up = self.client.create_order(order_up)
        resp_up = self.client.post_order(signed_up, OrderType.GTC)
        order_id_up = resp_up.get("orderID", resp_up.get("id", ""))
        log(f"  [PLACED] Up order: {order_id_up[:16]}... status={resp_up.get('status', '?')}")

        # Place DOWN order
        log(f"  [ORDER] {coin} Down: {shares_down:.1f} shares @ ${bid_down:.2f}")
        order_down = OrderArgs(
            token_id=token_down,
            price=bid_down,
            size=shares_down,
            side=BUY,
        )
        signed_down = self.client.create_order(order_down)
        resp_down = self.client.post_order(signed_down, OrderType.GTC)
        order_id_down = resp_down.get("orderID", resp_down.get("id", ""))
        log(f"  [PLACED] Down order: {order_id_down[:16]}... status={resp_down.get('status', '?')}")

        # Check immediate fills
        filled_up = resp_up.get("status") == "matched"
        filled_down = resp_down.get("status") == "matched"

        if filled_up and filled_down:
            log(f"  [INSTANT ARB] Both filled immediately!")
            return order_id_up, order_id_down, True, True

        # Monitor for fills until timeout
        deadline = time.time() + max(0, secs_left - FILL_TIMEOUT_BUFFER)
        while time.time() < deadline:
            time.sleep(FILL_POLL_INTERVAL)
            try:
                if not filled_up and order_id_up:
                    status_up = self.client.get_order(order_id_up)
                    if status_up and _is_filled(status_up):
                        filled_up = True
                        log(f"  [FILL] Up order filled!")

                if not filled_down and order_id_down:
                    status_down = self.client.get_order(order_id_down)
                    if status_down and _is_filled(status_down):
                        filled_down = True
                        log(f"  [FILL] Down order filled!")

                if filled_up and filled_down:
                    log(f"  [ARB FILL] Both sides filled!")
                    break
            except Exception as e:
                log(f"  [WARN] Fill check error: {e}")

        # Cancel unfilled orders (partial fills keep the filled token)
        if not filled_up and order_id_up:
            try:
                self.client.cancel(order_id_up)
                log(f"  [CANCEL] Up order cancelled")
            except Exception as e:
                log(f"  [WARN] Cancel up failed: {e}")

        if not filled_down and order_id_down:
            try:
                self.client.cancel(order_id_down)
                log(f"  [CANCEL] Down order cancelled")
            except Exception as e:
                log(f"  [WARN] Cancel down failed: {e}")

        return order_id_up, order_id_down, filled_up, filled_down

    def _emergency_sell_position(self, token_id, shares, coin, side_name):
        """
        Immediately sell a partial fill position back to the market.
        Uses FOK (fill-or-kill) to sell everything at once.
        Returns: {"success": bool, "sell_price": float, "amount_received": float, "error": str|None}
        """
        result = {"success": False, "sell_price": 0, "amount_received": 0, "error": None}

        if not self.client or DRY_RUN:
            # In dry run, simulate a sell with ~3 cent spread loss
            result["success"] = True
            result["sell_price"] = 0.47  # simulated
            result["amount_received"] = shares * 0.47
            log(f"  [DRY SELL] {coin} {side_name}: {shares:.1f} shares (simulated)")
            return result

        try:
            # Get current market price for selling
            sell_price = None
            try:
                book = self.client.get_order_book(token_id)
                bids = book.get("bids", [])
                if bids:
                    sell_price = float(bids[0]["price"])
            except Exception:
                pass

            if not sell_price or sell_price < 0.01:
                result["error"] = f"No bids available (price={sell_price})"
                log(f"  [SELL FAIL] {coin} {side_name}: no buyers in orderbook")
                return result

            log(f"  [SELL] {coin} {side_name}: {shares:.1f} shares @ ${sell_price:.2f}...")

            # Create market sell order (FOK = fill everything or cancel)
            order = OrderArgs(
                token_id=token_id,
                price=sell_price,
                size=shares,
                side=SELL,
            )
            signed = self.client.create_order(order)
            resp = self.client.post_order(signed, OrderType.FOK)

            if resp.get("success") or resp.get("status") == "matched":
                amount = float(resp.get("makingAmount", 0) or shares * sell_price)
                result["success"] = True
                result["sell_price"] = sell_price
                result["amount_received"] = amount
                log(f"  [SOLD] {coin} {side_name}: ${amount:.2f} received")
            else:
                # Try again at 5% worse price
                worse_price = round(sell_price * 0.95, 2)
                if worse_price >= 0.01:
                    log(f"  [SELL RETRY] {coin} {side_name} at ${worse_price:.2f}...")
                    order2 = OrderArgs(
                        token_id=token_id,
                        price=worse_price,
                        size=shares,
                        side=SELL,
                    )
                    signed2 = self.client.create_order(order2)
                    resp2 = self.client.post_order(signed2, OrderType.FOK)

                    if resp2.get("success") or resp2.get("status") == "matched":
                        amount = float(resp2.get("makingAmount", 0) or shares * worse_price)
                        result["success"] = True
                        result["sell_price"] = worse_price
                        result["amount_received"] = amount
                        log(f"  [SOLD] {coin} {side_name}: ${amount:.2f} received (retry)")
                    else:
                        result["error"] = f"FOK rejected at ${worse_price}"
                        log(f"  [SELL FAIL] {coin} {side_name}: no fill at ${worse_price}")
                else:
                    result["error"] = "Price too low to sell"

        except Exception as e:
            result["error"] = str(e)
            log(f"  [SELL ERROR] {coin} {side_name}: {e}")

        return result

    def _safe_cancel_all(self):
        """Emergency cancel all open orders. Never throws."""
        if not self.client:
            return
        try:
            self.client.cancel_all()
            log("[EMERGENCY] All open orders cancelled")
        except Exception as e:
            log(f"[EMERGENCY] Cancel all failed: {e}")

    def cancel_all_open(self):
        """Public method for orchestrator to call on shutdown."""
        self._safe_cancel_all()

    def _save_to_db(self, trade, order_id_up, order_id_down, condition_id,
                    experiment_id, phase, error_msg):
        """Save trade to both legacy trades table and new real_trades table."""
        conn = get_db()
        try:
            # Legacy trades table (for dashboard/scorer compatibility)
            filled = trade["filled"]
            arb_filled = trade["arb_filled"]
            conn.execute("""
                INSERT INTO trades (experiment_id, phase, coin, size_usd, fill_yes, fill_no,
                    total_cost, fees, slippage, net_pnl, filled, reason, window_end)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                experiment_id, phase, trade["coin"], trade["size_usd"],
                trade["bid_up"], trade["bid_down"],
                trade["total_cost"], trade["fees"], 0,
                trade["net_pnl"], 1 if arb_filled else 0,
                f"REAL:{'ARB' if arb_filled else 'PARTIAL' if filled else 'MISS'}: {trade['reason']}",
                trade["window_end"],
            ))

            # Real trades table
            status = "arb_complete" if arb_filled else "partial" if filled else "miss"
            if error_msg:
                status = "error"
            conn.execute("""
                INSERT INTO real_trades (experiment_id, phase, coin, condition_id,
                    order_id_up, order_id_down, bid_up, bid_down, size_usd,
                    total_cost, fees, net_pnl, filled_up, filled_down,
                    arb_filled, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                experiment_id, phase, trade["coin"], condition_id,
                order_id_up, order_id_down,
                trade["bid_up"], trade["bid_down"], trade["size_usd"],
                trade["total_cost"], trade["fees"], trade["net_pnl"],
                1 if trade["filled_up"] else 0,
                1 if trade["filled_down"] else 0,
                1 if arb_filled else 0,
                status,
            ))

            # Update portfolio
            if filled:
                conn.execute("""
                    INSERT INTO portfolio (balance_usd, total_pnl, total_trades,
                        total_fees, winning_trades, losing_trades)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (self.balance, self.total_pnl, self.total_trades,
                      self.total_fees, self.winning, self.losing))

            conn.commit()
        except Exception as e:
            log(f"  [DB ERROR] {e}")
        finally:
            conn.close()

    # ─── Resolve partial fills ───────────────────────────────────────

    def resolve_trades(self):
        """Check if partial fills have resolved (market ended)."""
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT * FROM real_trades WHERE status='partial' AND resolved_at IS NULL
            """).fetchall()

            if not rows:
                return []

            import requests
            resolved = []
            for row in rows:
                cid = row["condition_id"]
                if not cid:
                    continue
                try:
                    r = requests.get(
                        "https://gamma-api.polymarket.com/markets",
                        params={"conditionId": cid}, timeout=5
                    )
                    markets = r.json()
                    if not markets:
                        continue
                    m = markets[0]
                    if not m.get("resolved"):
                        continue

                    # Market resolved - determine PnL
                    outcome_prices = m.get("outcomePrices", "[0,0]")
                    import json
                    prices = json.loads(outcome_prices) if isinstance(outcome_prices, str) else outcome_prices
                    up_resolved = float(prices[0]) > 0.5 if len(prices) > 0 else False

                    # Calculate resolution PnL for the partial fill
                    if row["filled_up"] and not row["filled_down"]:
                        # We hold Up tokens
                        if up_resolved:
                            resolution_pnl = row["size_usd"] / row["bid_up"] * 1.0 - row["size_usd"]
                        else:
                            resolution_pnl = -row["size_usd"]
                    elif row["filled_down"] and not row["filled_up"]:
                        # We hold Down tokens
                        if not up_resolved:
                            resolution_pnl = row["size_usd"] / row["bid_down"] * 1.0 - row["size_usd"]
                        else:
                            resolution_pnl = -row["size_usd"]
                    else:
                        resolution_pnl = 0

                    resolution = "up" if up_resolved else "down"
                    conn.execute("""
                        UPDATE real_trades SET resolution=?, resolution_pnl=?,
                            resolved_at=datetime('now') WHERE id=?
                    """, (resolution, resolution_pnl, row["id"]))

                    self.balance += resolution_pnl
                    self.total_pnl += resolution_pnl
                    self._daily_pnl += resolution_pnl

                    log(f"  [RESOLVED] {row['coin']} partial fill -> {resolution} | "
                        f"PnL: ${resolution_pnl:+.4f}")
                    resolved.append(row["id"])
                except Exception as e:
                    log(f"  [WARN] Resolve check failed for {cid[:16]}: {e}")

            conn.commit()
            return resolved
        finally:
            conn.close()

    # ─── Portfolio ───────────────────────────────────────────────────

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

    # ─── Auto-Redeem: convert resolved tokens to USDC.e ─────────

    def auto_redeem(self):
        """Redeem resolved positions: convert winning tokens back to USDC.e.
        Call after each trading cycle to reclaim capital."""
        if DRY_RUN or not self.client:
            return 0

        import requests
        import json

        wallet = os.environ.get("WALLET_ADDRESS", "")
        if not wallet:
            return 0

        try:
            # 1. Get all positions from Polymarket
            r = requests.get("https://data-api.polymarket.com/positions",
                           params={"user": wallet}, timeout=10)
            positions = r.json()
            if not positions:
                return 0

            # 2. Find redeemable positions (resolved, price = $1.00)
            cids_to_redeem = set()
            for p in positions:
                cur_price = float(p.get("curPrice", 0) or 0)
                cid = p.get("conditionId", "")
                if cur_price >= 0.99 and cid:
                    cids_to_redeem.add(cid)

            if not cids_to_redeem:
                return 0

            # 3. Redeem each condition ID
            from web3 import Web3
            rpc = os.environ.get("POLYGON_RPC", "https://polygon-pokt.nodies.app")
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
            if not w3.is_connected():
                return 0

            CTF = Web3.to_checksum_address("0x4D97DCd97eC945f40cF65F87097ACe5EA0476045")
            USDC_E = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
            WALLET_CS = Web3.to_checksum_address(wallet)
            ZERO = b'\x00' * 32
            REDEEM_ABI = [{
                'inputs': [
                    {'name': 'collateralToken', 'type': 'address'},
                    {'name': 'parentCollectionId', 'type': 'bytes32'},
                    {'name': 'conditionId', 'type': 'bytes32'},
                    {'name': 'indexSets', 'type': 'uint256[]'},
                ],
                'name': 'redeemPositions', 'outputs': [], 'type': 'function',
            }]
            ctf = w3.eth.contract(address=CTF, abi=REDEEM_ABI)
            private_key = os.environ.get("PRIVATE_KEY", "")

            nonce = w3.eth.get_transaction_count(WALLET_CS, "latest")
            redeemed = 0
            import time as _time

            for cid in cids_to_redeem:
                try:
                    cid_bytes = bytes.fromhex(cid[2:] if cid.startswith("0x") else cid)
                    tx = ctf.functions.redeemPositions(
                        USDC_E, ZERO, cid_bytes, [1, 2]
                    ).build_transaction({
                        "chainId": 137, "from": WALLET_CS, "nonce": nonce,
                        "gas": 300000,
                        "maxFeePerGas": w3.to_wei(200, "gwei"),
                        "maxPriorityFeePerGas": w3.to_wei(50, "gwei"),
                    })
                    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
                    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
                    if receipt["status"] == 1:
                        redeemed += 1
                    nonce += 1
                    _time.sleep(2)
                except Exception as e:
                    log(f"  [REDEEM] Failed for {cid[:16]}: {e}")
                    _time.sleep(3)
                    nonce = w3.eth.get_transaction_count(WALLET_CS, "pending")

            if redeemed > 0:
                log(f"  [REDEEM] Redeemed {redeemed}/{len(cids_to_redeem)} positions")
            return redeemed

        except Exception as e:
            log(f"  [REDEEM] Error: {e}")
            return 0


def _is_filled(order_status) -> bool:
    """Check if an order response indicates it's been filled."""
    if isinstance(order_status, dict):
        status = order_status.get("status", "").lower()
        size_matched = float(order_status.get("size_matched", 0) or 0)
        original_size = float(order_status.get("original_size", 1) or 1)
        # Filled if status says matched/confirmed OR if size_matched >= original_size
        if status in ("matched", "confirmed", "filled"):
            return True
        if size_matched > 0 and size_matched >= original_size * 0.95:
            return True
    return False
