"""
AutoResearch Polymarket - Strategy v3
Dynamic BID_SPREAD based on volatility and orderbook depth.
Position sizing proportional to edge quality.
Partial fills are cancelled (no directional risk).
"""

# ─── PRICING ──────────────────────────────────────────────────────
MAX_TOTAL_COST = 0.99       # Max combined bid price for Up+Down
BID_SPREAD_BASE = 1.5  # Base spread in cents below implied price
ASYMMETRY = 0.0             # Shift between Up/Down bids (cents) [KEPT #177 by Gemini]
VOL_REFERENCE = 0.03        # Reference volatility for spread calc
DEPTH_DIVISOR = 100.0       # Divisor to convert depth to factor
SPREAD_CLAMP_MIN = 0.5      # Min dynamic spread allowed (cents)
SPREAD_CLAMP_MAX = 5.0      # Max dynamic spread allowed (cents)

# ─── FILTROS ──────────────────────────────────────────────────────
MIN_EDGE_CENTS = 0.2        # Min profit per trade after fees (cents)
MIN_SECS_LEFT = 60          # Min seconds remaining to place order
DEPTH_MIN = 5.0             # Min orderbook depth (USD) per side
MAX_IMPLIED_SKEW = 0.30     # Max |Up - Down| implied price diff
MIN_VOLATILITY = 0.005      # Min volatility to trade (skip dead markets)

# ─── SIZING ───────────────────────────────────────────────────────
ORDER_SIZE_USD = 15  # Base USD per side [KEPT #178 by Gemini]
EDGE_SCALE_BASE = 0.003  # Edge divisor for position scaling
MAX_SIZE_MULTIPLIER = 3.0   # Cap for edge scaling multiplier
MAX_EXPOSURE_PCT = 0.5      # Max % of balance as total exposure

# ─── TIMING ───────────────────────────────────────────────────────
SKIP_FIRST_N_POLLS = 0      # Skip first N polls per window
POLL_DELAY_SECS = 0         # Extra delay (secs) before each decision

# ─── SYSTEM ───────────────────────────────────────────────────────
MAX_ORDERS_PER_POLL = 5     # Max order pairs per poll cycle
VOL_ADJUSTMENT = True       # Enable dynamic spread by volatility
EDGE_SCALING = True         # Scale position size by edge quality
COINS_TO_TRADE = None       # None = all coins
USE_LLM = True              # True = Gemini agentic, False = random

# Legacy alias
BID_SPREAD = BID_SPREAD_BASE


def estimate_fee(price: float) -> float:
    """Polymarket fee: price * (1 - price) * 2.2%"""
    if price <= 0 or price >= 1:
        return 0.0
    return price * (1 - price) * 0.022


def _dynamic_spread(base_spread: float, volatility: float,
                    up_depth: float, down_depth: float) -> float:
    """
    Calculate dynamic BID_SPREAD based on market conditions.

    Higher volatility -> tighter spread (more fills expected from price swings)
    Higher depth -> tighter spread (more liquidity = safer to be aggressive)
    """
    if not VOL_ADJUSTMENT:
        return base_spread

    # Volatility factor: BTC ~3% daily -> 1.0x, ETH ~4% -> 0.75x (more aggressive)
    vol_factor = VOL_REFERENCE / max(volatility, 0.005)

    # Depth factor: more liquidity = safer to be closer to mid
    total_depth = up_depth + down_depth
    depth_factor = min(1.5, max(0.3, total_depth / DEPTH_DIVISOR))

    dynamic = base_spread * vol_factor * (1.0 / depth_factor)
    return max(SPREAD_CLAMP_MIN, min(SPREAD_CLAMP_MAX, dynamic))


def decide(observations: list, history: list, config: dict) -> list:
    """
    Decide which limit order pairs to place.
    Returns list of order dicts for the paper trader.
    """
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

        # ─── Orderbook depth filter ─────────────────────────────────
        up_depth = obs.get("up_depth", 0)
        down_depth = obs.get("down_depth", 0)
        if up_depth < DEPTH_MIN or down_depth < DEPTH_MIN:
            continue  # Skip illiquid markets

        # ─── Volatility + skew filters ─────────────────────────────
        volatility = obs.get("volatility", 0.03)
        if abs(implied_up - implied_down) > MAX_IMPLIED_SKEW:
            continue  # Market too skewed
        if volatility < MIN_VOLATILITY:
            continue  # Market too dead

        # ─── Dynamic spread calculation ─────────────────────────────
        spread = _dynamic_spread(BID_SPREAD_BASE, volatility, up_depth, down_depth)

        spread_up = (spread + ASYMMETRY) / 100.0
        spread_down = (spread - ASYMMETRY) / 100.0

        bid_up = round(max(0.01, min(0.99, implied_up - spread_up)), 2)
        bid_down = round(max(0.01, min(0.99, implied_down - spread_down)), 2)

        # Enforce MAX_TOTAL_COST
        total_cost = bid_up + bid_down
        if total_cost > MAX_TOTAL_COST:
            excess = total_cost - MAX_TOTAL_COST
            bid_up = round(bid_up - excess / 2, 2)
            bid_down = round(bid_down - excess / 2, 2)
            total_cost = bid_up + bid_down

        if total_cost >= 1.0:
            continue

        # ─── Edge calculation ───────────────────────────────────────
        fee_up = estimate_fee(bid_up)
        fee_down = estimate_fee(bid_down)
        total_fees = fee_up + fee_down
        edge = 1.0 - total_cost - total_fees

        if edge < MIN_EDGE_CENTS / 100.0:
            continue

        # ─── Position sizing proportional to edge ───────────────────
        if EDGE_SCALING:
            edge_quality = min(MAX_SIZE_MULTIPLIER, edge / EDGE_SCALE_BASE)
            size_usd = ORDER_SIZE_USD * max(0.5, edge_quality)
        else:
            size_usd = ORDER_SIZE_USD

        orders.append({
            "coin": coin,
            "action": "LIMIT_BOTH",
            "bid_up": bid_up,
            "bid_down": bid_down,
            "size_usd": size_usd,
            "total_cost": total_cost,
            "fees": total_fees,
            "edge": edge,
            "implied_up": implied_up,
            "implied_down": implied_down,
            "secs_left": secs_left,
            "spread_used": spread,
            "reason": f"Up:{bid_up:.2f}+Down:{bid_down:.2f}={total_cost:.2f} "
                      f"edge={edge:.4f} spread={spread:.1f}c "
                      f"depth={up_depth:.0f}/{down_depth:.0f}",
        })

        if len(orders) >= MAX_ORDERS_PER_POLL:
            break

    orders.sort(key=lambda x: -x["edge"])
    return orders
