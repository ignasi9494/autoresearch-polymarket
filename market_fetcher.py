"""
AutoResearch Polymarket - Market Data Fetcher v2
Finds the CURRENT active 5-minute "Up or Down" market for each coin.
Uses timestamp-based slug pattern: {coin}-updown-5m-{unix_timestamp}
Gets implied prices (outcomePrices) + orderbook data.
"""

import json
import time
import math
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

COINS = {
    "BTC": {"binance": "BTCUSDT", "slug_prefix": "btc"},
    "ETH": {"binance": "ETHUSDT", "slug_prefix": "eth"},
    "SOL": {"binance": "SOLUSDT", "slug_prefix": "sol"},
    "XRP": {"binance": "XRPUSDT", "slug_prefix": "xrp"},
    "DOGE": {"binance": "DOGEUSDT", "slug_prefix": "doge"},
}

# Cache
_market_cache = {}   # coin -> market_info
_cache_ts = 0
_cache_slot = 0      # The 5-min slot timestamp we cached

# Volatility cache (TTL = 300s, no need to recalculate every 30s poll)
_vol_cache = {}      # symbol -> (volatility, timestamp)
_VOL_CACHE_TTL = 300  # 5 minutes

# Binance price cache (TTL = 30s)
_price_cache = {}    # coin -> (price, timestamp)
_PRICE_CACHE_TTL = 30


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [FETCH] {msg}")


def get_current_slot() -> int:
    """Get the Unix timestamp for the current 5-minute slot start."""
    now = int(time.time())
    return (now // 300) * 300


def get_binance_price(symbol: str) -> float:
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol}, timeout=5
        )
        return float(r.json()["price"])
    except Exception:
        return 0.0


def get_binance_prices() -> dict:
    prices = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(get_binance_price, info["binance"]): coin
                for coin, info in COINS.items()}
        for f in as_completed(futs):
            coin = futs[f]
            try:
                p = f.result()
                if p > 0:
                    prices[coin] = p
            except Exception:
                pass
    return prices


def _find_market_by_slug(coin: str, slot_ts: int) -> dict:
    """Find a 5-min market using the timestamp slug pattern."""
    prefix = COINS[coin]["slug_prefix"]

    # Try current slot and a few nearby ones (market creation may lag)
    for offset in [0, -300, 300, -600, 600]:
        ts = slot_ts + offset
        slug = f"{prefix}-updown-5m-{ts}"
        try:
            r = requests.get(f"{GAMMA_BASE}/markets", params={"slug": slug}, timeout=5)
            markets = r.json()
            if markets and len(markets) > 0:
                m = markets[0]
                # Verify it's still open
                end_str = m.get("endDate", "")
                if end_str:
                    end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if end_dt <= now:
                        continue  # Already expired
                return m
        except Exception:
            continue
    return None


def _parse_market(m: dict, coin: str) -> dict:
    """Parse a Gamma API market into our internal format."""
    token_ids = json.loads(m.get("clobTokenIds", "[]"))
    outcomes = json.loads(m.get("outcomes", '["Up","Down"]'))
    outcome_prices = json.loads(m.get("outcomePrices", "[0.5, 0.5]"))

    token_up, token_down = None, None
    price_up, price_down = 0.5, 0.5

    for i, outcome in enumerate(outcomes):
        ol = outcome.lower()
        if ol in ["yes", "up"]:
            token_up = token_ids[i] if i < len(token_ids) else None
            price_up = float(outcome_prices[i]) if i < len(outcome_prices) else 0.5
        elif ol in ["no", "down"]:
            token_down = token_ids[i] if i < len(token_ids) else None
            price_down = float(outcome_prices[i]) if i < len(outcome_prices) else 0.5

    return {
        "condition_id": m.get("conditionId", ""),
        "token_up": token_up,
        "token_down": token_down,
        "question": m.get("question", ""),
        "end_date": m.get("endDate", ""),
        "slug": m.get("slug", ""),
        "implied_up": price_up,
        "implied_down": price_down,
        "volume": float(m.get("volume", 0)),
        "liquidity": float(m.get("liquidity", 0)),
        # Legacy compatibility
        "token_yes": token_up,
        "token_no": token_down,
    }


def discover_markets() -> dict:
    """Find the CURRENT 5-minute market for each coin.

    Uses timestamp-based slug: {coin}-updown-5m-{slot_timestamp}
    This finds markets that are actively trading NOW.
    """
    global _market_cache, _cache_ts, _cache_slot

    current_slot = get_current_slot()

    # Only refresh if slot changed or cache is stale (>30s)
    if (_market_cache and _cache_slot == current_slot
            and time.time() - _cache_ts < 30):
        return _market_cache

    log(f"Discovering current 5-min markets (slot={current_slot})...")
    found = {}

    # Parallel search for all coins
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(_find_market_by_slug, coin, current_slot): coin
                for coin in COINS}
        for f in as_completed(futs):
            coin = futs[f]
            try:
                m = f.result()
                if m:
                    parsed = _parse_market(m, coin)
                    if parsed["token_up"] and parsed["token_down"]:
                        found[coin] = parsed
                        log(f"  {coin}: {parsed['question'][:55]} "
                            f"(Up={parsed['implied_up']:.3f} Down={parsed['implied_down']:.3f})")
            except Exception as e:
                log(f"  {coin}: error - {e}")

    if found:
        _market_cache = found
        _cache_ts = time.time()
        _cache_slot = current_slot
        log(f"  Found {len(found)}/{len(COINS)} markets")
    else:
        log(f"  WARNING: No markets found for current slot!")

    return found


def _fetch_orderbook(token_id: str) -> dict:
    """Fetch full orderbook for a token."""
    try:
        r = requests.get(f"{CLOB_BASE}/book",
                         params={"token_id": token_id}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0
            best_ask = float(asks[0]["price"]) if asks else 0
            depth_bid = sum(float(b.get("size", 0)) for b in bids[:10])
            depth_ask = sum(float(a.get("size", 0)) for a in asks[:10])
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid if best_ask and best_bid else 0,
                "mid": (best_bid + best_ask) / 2 if best_bid and best_ask else 0,
                "depth_bid": depth_bid,
                "depth_ask": depth_ask,
                "bids": bids[:10],
                "asks": asks[:10],
            }
    except Exception:
        pass
    return {"best_bid": 0, "best_ask": 0, "spread": 0, "mid": 0,
            "depth_bid": 0, "depth_ask": 0, "bids": [], "asks": []}


def get_realized_volatility(symbol: str) -> float:
    """24h realized volatility from Binance 1h klines. Cached for 5 minutes."""
    global _vol_cache

    # Check cache first
    if symbol in _vol_cache:
        cached_vol, cached_ts = _vol_cache[symbol]
        if time.time() - cached_ts < _VOL_CACHE_TTL:
            return cached_vol

    defaults = {"BTCUSDT": 0.03, "ETHUSDT": 0.04, "SOLUSDT": 0.05,
                "XRPUSDT": 0.05, "DOGEUSDT": 0.06}
    try:
        r = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": symbol, "interval": "1h", "limit": 24,
        }, timeout=5)
        klines = r.json()
        if len(klines) < 10:
            vol = defaults.get(symbol, 0.04)
        else:
            closes = [float(k[4]) for k in klines]
            returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
            mean_r = sum(returns) / len(returns)
            var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
            daily_vol = math.sqrt(var) * math.sqrt(24)
            vol = max(0.005, min(0.15, daily_vol))
    except Exception:
        vol = defaults.get(symbol, 0.04)

    _vol_cache[symbol] = (vol, time.time())
    return vol


def poll_all_coins() -> list:
    """
    Poll all coins: find current markets, get orderbooks + implied prices.
    Returns list of observation dicts with all data needed for strategy.
    """
    markets = discover_markets()
    if not markets:
        return []

    observations = []

    # Fetch orderbooks + volatility + binance prices ALL in parallel
    token_tasks = {}
    vol_tasks = {}
    price_tasks = {}
    with ThreadPoolExecutor(max_workers=20) as ex:
        for coin, mkt in markets.items():
            token_tasks[(coin, "up")] = ex.submit(_fetch_orderbook, mkt["token_up"])
            token_tasks[(coin, "down")] = ex.submit(_fetch_orderbook, mkt["token_down"])
            vol_tasks[coin] = ex.submit(get_realized_volatility, COINS[coin]["binance"])
            price_tasks[coin] = ex.submit(get_binance_price, COINS[coin]["binance"])

        results = {}
        for key, fut in token_tasks.items():
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = {"best_bid": 0, "best_ask": 0, "spread": 0,
                                "mid": 0, "depth_bid": 0, "depth_ask": 0,
                                "bids": [], "asks": []}

        vol_results = {}
        for coin, fut in vol_tasks.items():
            try:
                vol_results[coin] = fut.result()
            except Exception:
                vol_results[coin] = 0.04

        price_results = {}
        for coin, fut in price_tasks.items():
            try:
                p = fut.result()
                if p > 0:
                    price_results[coin] = p
            except Exception:
                pass

    now = datetime.now(timezone.utc)

    for coin, mkt in markets.items():
        up_ob = results.get((coin, "up"), {})
        down_ob = results.get((coin, "down"), {})

        # Implied prices from Gamma API (the real midmarket)
        implied_up = mkt["implied_up"]
        implied_down = mkt["implied_down"]
        implied_total = implied_up + implied_down

        # Orderbook data (CLOB limit orders)
        up_best_bid = up_ob.get("best_bid", 0)
        up_best_ask = up_ob.get("best_ask", 0)
        down_best_bid = down_ob.get("best_bid", 0)
        down_best_ask = down_ob.get("best_ask", 0)

        # Time until window ends
        end_str = mkt.get("end_date", "")
        secs_left = 300  # default 5 min
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                secs_left = max(0, (end_dt - now).total_seconds())
            except Exception:
                pass

        volatility = vol_results.get(coin, 0.04)

        obs = {
            "coin": coin,
            "condition_id": mkt["condition_id"],
            "token_up": mkt["token_up"],
            "token_down": mkt["token_down"],
            "question": mkt["question"],
            "end_date": mkt["end_date"],
            "slug": mkt["slug"],

            # Implied prices (from Gamma API - the real market price)
            "implied_up": implied_up,
            "implied_down": implied_down,
            "implied_total": implied_total,

            # Orderbook (CLOB limit orders)
            "up_best_bid": up_best_bid,
            "up_best_ask": up_best_ask,
            "down_best_bid": down_best_bid,
            "down_best_ask": down_best_ask,
            "up_depth": up_ob.get("depth_bid", 0),
            "down_depth": down_ob.get("depth_bid", 0),
            "orderbook_up": up_ob,
            "orderbook_down": down_ob,

            # Time
            "secs_left": secs_left,

            # Binance reference
            "binance_price": price_results.get(coin, 0),
            "volatility": volatility,

            "timestamp": now.isoformat(),

            # Legacy compatibility fields
            "token_yes": mkt["token_up"],
            "token_no": mkt["token_down"],
            "yes_ask": up_best_ask,
            "yes_bid": up_best_bid,
            "no_ask": down_best_ask,
            "no_bid": down_best_bid,
            "yes_mid": implied_up,
            "no_mid": implied_down,
            "spread_yes": up_ob.get("spread", 0),
            "spread_no": down_ob.get("spread", 0),
            "total_ask": up_best_ask + down_best_ask,
            "total_bid": up_best_bid + down_best_bid,
            "gap": 1.0 - (up_best_ask + down_best_ask) if (up_best_ask + down_best_ask) > 0 else 0,
            "depth_yes_usd": up_ob.get("depth_bid", 0),
            "depth_no_usd": down_ob.get("depth_bid", 0),
            "volatility_1h": volatility,
        }
        observations.append(obs)

    return observations


if __name__ == "__main__":
    log("=== Testing market discovery ===")
    obs = poll_all_coins()
    for o in obs:
        print(f"  {o['coin']:>4s}: "
              f"Up={o['implied_up']:.3f} Down={o['implied_down']:.3f} "
              f"(sum={o['implied_total']:.4f}) "
              f"| CLOB: up_bid={o['up_best_bid']:.2f} down_bid={o['down_best_bid']:.2f} "
              f"| {o['secs_left']:.0f}s left "
              f"| Binance=${o['binance_price']:,.2f}")
