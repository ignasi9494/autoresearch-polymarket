"""
AutoResearch Polymarket - Market Data Fetcher
Fetches real-time data for 5 coins from Polymarket + Binance.
Polling every 30 seconds. No API keys needed (public endpoints).
"""

import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

COINS = {
    "BTC": {"binance": "BTCUSDT", "keywords": ["bitcoin"]},
    "ETH": {"binance": "ETHUSDT", "keywords": ["ethereum"]},
    "SOL": {"binance": "SOLUSDT", "keywords": ["solana"]},
    "XRP": {"binance": "XRPUSDT", "keywords": ["xrp"]},
    "DOGE": {"binance": "DOGEUSDT", "keywords": ["doge", "dogecoin"]},
}

# Cache for discovered markets
_market_cache = {}  # coin -> {condition_id, token_yes, token_no, question, end_date}
_cache_ts = 0

# Volatility cache
_vol_cache = {}
_vol_cache_ts = 0


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [FETCH] {msg}")


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


def get_realized_volatility(symbol: str) -> float:
    """24h realized volatility from Binance 1h klines."""
    import math
    global _vol_cache, _vol_cache_ts
    now = time.time()
    if symbol in _vol_cache and now - _vol_cache_ts < 600:
        return _vol_cache[symbol]

    defaults = {"BTCUSDT": 0.03, "ETHUSDT": 0.04, "SOLUSDT": 0.05,
                "XRPUSDT": 0.05, "DOGEUSDT": 0.06}
    try:
        r = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": symbol, "interval": "1h", "limit": 24,
        }, timeout=5)
        klines = r.json()
        if len(klines) < 10:
            return defaults.get(symbol, 0.04)

        closes = [float(k[4]) for k in klines]
        returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        mean_r = sum(returns) / len(returns)
        var = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        daily_vol = math.sqrt(var) * math.sqrt(24)
        daily_vol = max(0.005, min(0.15, daily_vol))

        _vol_cache[symbol] = daily_vol
        _vol_cache_ts = now
        return daily_vol
    except Exception:
        return defaults.get(symbol, 0.04)


def discover_markets() -> dict:
    """Find active 5-min 'Up or Down' markets for each of the 5 coins.

    Strategy: search by startDate desc (newest first) and pick the
    nearest-to-expiry market per coin that hasn't ended yet.
    """
    global _market_cache, _cache_ts

    # Refresh every 2 min (markets rotate fast)
    if _market_cache and time.time() - _cache_ts < 120:
        return _market_cache

    log("Discovering 5-min markets for 5 coins...")
    now = datetime.now(timezone.utc)
    candidates = {}  # coin -> list of (end_date, market_info)

    for page in range(5):
        try:
            r = requests.get(f"{GAMMA_BASE}/markets", params={
                "limit": 100,
                "offset": page * 100, "order": "startDate", "ascending": "false",
            }, timeout=15)
            markets = r.json()
            if not markets:
                break

            for m in markets:
                q = (m.get("question", "") + " " + m.get("description", "")).lower()

                # Must be "up or down" market
                if "up or down" not in q:
                    continue

                # Skip already expired
                end_str = m.get("endDate", "")
                if end_str:
                    try:
                        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if end_dt <= now:
                            continue
                    except Exception:
                        pass

                # Match to our 5 coins
                matched_coin = None
                for coin, info in COINS.items():
                    if any(kw in q for kw in info["keywords"]):
                        matched_coin = coin
                        break

                if not matched_coin:
                    continue

                # Extract token IDs
                token_ids = json.loads(m.get("clobTokenIds", "[]"))
                outcomes = json.loads(m.get("outcomes", '["Yes","No"]'))
                token_yes, token_no = None, None
                for i, outcome in enumerate(outcomes):
                    if outcome.lower() in ["yes", "up"]:
                        token_yes = token_ids[i] if i < len(token_ids) else None
                    elif outcome.lower() in ["no", "down"]:
                        token_no = token_ids[i] if i < len(token_ids) else None

                if token_yes and token_no:
                    info = {
                        "condition_id": m.get("conditionId"),
                        "token_yes": token_yes,
                        "token_no": token_no,
                        "question": m.get("question", ""),
                        "end_date": end_str,
                    }
                    if matched_coin not in candidates:
                        candidates[matched_coin] = []
                    candidates[matched_coin].append((end_str, info))

            if len(candidates) >= 5:
                # Check if we have at least one per coin
                all_found = all(c in candidates for c in COINS)
                if all_found:
                    break
        except Exception as e:
            log(f"  [WARN] Page {page}: {e}")
            break

    # Pick the nearest-expiry (soonest) market per coin — most actively traded
    found = {}
    for coin, entries in candidates.items():
        entries.sort(key=lambda x: x[0])  # Sort by end_date ascending
        found[coin] = entries[0][1]  # Pick earliest expiry
        log(f"  {coin}: {entries[0][1]['question'][:60]}")

    _market_cache = found
    _cache_ts = time.time()
    log(f"  Found markets for: {list(found.keys())}")
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
            # Calculate depth (sum of size * price for top 5 levels)
            depth = sum(float(a.get("size", 0)) * float(a.get("price", 0))
                        for a in asks[:5])
            return {
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": best_ask - best_bid if best_ask and best_bid else 0,
                "mid": (best_bid + best_ask) / 2 if best_bid and best_ask else 0,
                "depth_usd": depth,
                "bids": bids[:10],
                "asks": asks[:10],
            }
    except Exception:
        pass
    return {"best_bid": 0, "best_ask": 0, "spread": 0, "mid": 0,
            "depth_usd": 0, "bids": [], "asks": []}


def _fetch_midpoint_spread(token_id: str) -> dict:
    """Fallback: fetch midpoint and spread if orderbook fails."""
    data = {}
    try:
        r_mid = requests.get(f"{CLOB_BASE}/midpoint",
                             params={"token_id": token_id}, timeout=5)
        r_spread = requests.get(f"{CLOB_BASE}/spread",
                                params={"token_id": token_id}, timeout=5)
        if r_mid.status_code == 200:
            data["mid"] = float(r_mid.json().get("mid") or 0) or 0
        if r_spread.status_code == 200:
            data["spread"] = float(r_spread.json().get("spread") or 0) or 0
        if data.get("mid") and data.get("spread"):
            data["best_bid"] = data["mid"] - data["spread"] / 2
            data["best_ask"] = data["mid"] + data["spread"] / 2
    except Exception:
        pass
    return data


def poll_all_coins() -> list:
    """
    Poll all 5 coins in parallel. Returns list of observation dicts.
    Called every 30 seconds.
    """
    markets = discover_markets()
    if not markets:
        log("  No markets found!")
        return []

    binance_prices = get_binance_prices()
    observations = []

    # Fetch orderbooks for all tokens in parallel
    token_tasks = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for coin, mkt in markets.items():
            token_tasks[(coin, "yes")] = ex.submit(_fetch_orderbook, mkt["token_yes"])
            token_tasks[(coin, "no")] = ex.submit(_fetch_orderbook, mkt["token_no"])

        results = {}
        for key, fut in token_tasks.items():
            try:
                results[key] = fut.result()
            except Exception:
                results[key] = {"best_bid": 0, "best_ask": 0, "spread": 0,
                                "mid": 0, "depth_usd": 0, "bids": [], "asks": []}

    for coin, mkt in markets.items():
        yes_ob = results.get((coin, "yes"), {})
        no_ob = results.get((coin, "no"), {})

        yes_ask = yes_ob.get("best_ask", 0)
        yes_bid = yes_ob.get("best_bid", 0)
        no_ask = no_ob.get("best_ask", 0)
        no_bid = no_ob.get("best_bid", 0)

        # If orderbook failed, try midpoint/spread fallback
        if not yes_ask and not yes_bid:
            fallback = _fetch_midpoint_spread(mkt["token_yes"])
            yes_ask = fallback.get("best_ask", 0)
            yes_bid = fallback.get("best_bid", 0)
            yes_ob["mid"] = fallback.get("mid", 0)
            yes_ob["spread"] = fallback.get("spread", 0)

        if not no_ask and not no_bid:
            fallback = _fetch_midpoint_spread(mkt["token_no"])
            no_ask = fallback.get("best_ask", 0)
            no_bid = fallback.get("best_bid", 0)
            no_ob["mid"] = fallback.get("mid", 0)
            no_ob["spread"] = fallback.get("spread", 0)

        total_ask = yes_ask + no_ask  # Cost to buy both sides at ask
        total_bid = yes_bid + no_bid  # What we'd get selling both at bid
        gap = 1.0 - total_ask if total_ask > 0 else 0  # Positive = arb exists

        vol_symbol = COINS[coin]["binance"]
        volatility = get_realized_volatility(vol_symbol)

        obs = {
            "coin": coin,
            "condition_id": mkt["condition_id"],
            "token_yes": mkt["token_yes"],
            "token_no": mkt["token_no"],
            "question": mkt["question"],
            "end_date": mkt["end_date"],
            "yes_ask": yes_ask,
            "yes_bid": yes_bid,
            "yes_mid": yes_ob.get("mid", 0),
            "no_ask": no_ask,
            "no_bid": no_bid,
            "no_mid": no_ob.get("mid", 0),
            "spread_yes": yes_ob.get("spread", 0),
            "spread_no": no_ob.get("spread", 0),
            "total_ask": total_ask,
            "total_bid": total_bid,
            "gap": gap,
            "depth_yes_usd": yes_ob.get("depth_usd", 0),
            "depth_no_usd": no_ob.get("depth_usd", 0),
            "orderbook_yes": yes_ob,
            "orderbook_no": no_ob,
            "binance_price": binance_prices.get(coin, 0),
            "volatility_1h": volatility,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        observations.append(obs)

    return observations


if __name__ == "__main__":
    log("=== Manual poll test ===")
    obs = poll_all_coins()
    for o in obs:
        gap_pct = o["gap"] * 100
        arb = "ARB!" if o["gap"] > 0 else "no arb"
        print(f"  {o['coin']:>4s}: YES={o['yes_ask']:.4f} NO={o['no_ask']:.4f} "
              f"Total={o['total_ask']:.4f} Gap={gap_pct:+.2f}% [{arb}] "
              f"Spread Y={o['spread_yes']:.4f} N={o['spread_no']:.4f} "
              f"Binance=${o['binance_price']:,.2f}")
