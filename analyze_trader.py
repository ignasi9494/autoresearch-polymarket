#!/usr/bin/env python3
"""
Deep Analysis of Polymarket Trader 0xe1d6b51521bd4365769199f392f9818661bd907

This script fetches ALL trade data from Polymarket's public Data API
and performs comprehensive mathematical analysis:
- P&L breakdown (realized, unrealized, total)
- Fill rate analysis (full fills, partial fills, misses)
- Win rate / loss rate
- Maker vs Taker ratio
- Trade size distribution
- Market concentration
- Time-of-day patterns
- Edge per trade
- Sharpe-like consistency metric

Usage: python analyze_trader.py
       python analyze_trader.py --address 0x...

Requires: requests, (optional) matplotlib for charts
"""

import requests
import json
import time
import sys
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ─── CONFIG ──────────────────────────────────────────────────────
TRADER_ADDRESS = "0xe1d6b51521bd4365769199f392f9818661bd907"
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

POLYMARKET_FEE_RATE = 0.022  # 2.2% fee formula: price * (1-price) * 0.022


# ─── DATA FETCHING ───────────────────────────────────────────────

def fetch_paginated(endpoint, params, label="records"):
    """Generic paginated fetch from Data API."""
    all_data = []
    offset = 0
    limit = params.get("limit", 500)

    while True:
        params["offset"] = offset
        params["limit"] = limit
        url = f"{DATA_API}/{endpoint}"
        print(f"  [{label}] Fetching offset={offset}...", end=" ")

        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if resp.status_code == 403:
                print(f"403 Forbidden - API may require auth or different IP")
                break
            if resp.status_code != 200:
                print(f"Error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            if not data:
                print("(empty - done)")
                break

            all_data.extend(data)
            print(f"got {len(data)} (total: {len(all_data)})")

            if len(data) < limit:
                break
            offset += limit
            time.sleep(0.3)

        except Exception as e:
            print(f"Error: {e}")
            break

    return all_data


def fetch_all_trades(address):
    """Fetch ALL trades for this address."""
    print("\n[1/5] FETCHING TRADES...")
    return fetch_paginated("trades", {"user": address}, "trades")


def fetch_all_activity(address):
    """Fetch ALL activity (trades, splits, merges, redeems, rewards)."""
    print("\n[2/5] FETCHING ACTIVITY...")
    return fetch_paginated("activity", {"user": address}, "activity")


def fetch_positions(address):
    """Fetch current open positions."""
    print("\n[3/5] FETCHING OPEN POSITIONS...")
    return fetch_paginated("positions", {
        "user": address,
        "sizeThreshold": 0,
        "sortBy": "CURRENT",
        "sortDirection": "DESC",
    }, "positions")


def fetch_closed_positions(address):
    """Fetch closed/resolved positions."""
    print("\n[4/5] FETCHING CLOSED POSITIONS...")
    return fetch_paginated("closed-positions", {"user": address}, "closed")


def fetch_portfolio_value(address):
    """Fetch total portfolio value."""
    print("\n[5/5] FETCHING PORTFOLIO VALUE...")
    url = f"{DATA_API}/value"
    try:
        resp = requests.get(url, params={"user": address}, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  Portfolio value: {data}")
            return data
    except Exception as e:
        print(f"  Error: {e}")
    return None


# ─── ANALYSIS FUNCTIONS ─────────────────────────────────────────

def estimate_fee(price):
    """Polymarket fee: price * (1 - price) * 2.2%"""
    if price <= 0 or price >= 1:
        return 0.0
    return price * (1 - price) * POLYMARKET_FEE_RATE


def analyze_trades_deep(trades):
    """Comprehensive trade-by-trade analysis."""
    if not trades:
        return {}

    total = len(trades)
    buys = [t for t in trades if str(t.get("side", "")).upper() == "BUY"]
    sells = [t for t in trades if str(t.get("side", "")).upper() == "SELL"]

    # Extract numeric fields
    prices = []
    sizes_usd = []
    sizes_shares = []

    for t in trades:
        try:
            p = float(t.get("price", 0))
            if p > 0:
                prices.append(p)
        except:
            pass
        try:
            u = float(t.get("usdcSize", t.get("usdc_size", 0)))
            if u > 0:
                sizes_usd.append(u)
        except:
            pass
        try:
            s = float(t.get("size", 0))
            if s > 0:
                sizes_shares.append(s)
        except:
            pass

    # Price buckets
    price_buckets = defaultdict(int)
    for p in prices:
        bucket = round(p * 10) / 10  # 0.1 increments
        price_buckets[bucket] += 1

    # Time analysis
    timestamps = []
    for t in trades:
        ts_raw = t.get("timestamp", t.get("createdAt", ""))
        try:
            if isinstance(ts_raw, (int, float)):
                if ts_raw > 1e12:
                    ts_raw = ts_raw / 1000  # ms -> s
                timestamps.append(datetime.fromtimestamp(ts_raw, tz=timezone.utc))
            elif ts_raw:
                ts_str = str(ts_raw).replace("Z", "+00:00")
                timestamps.append(datetime.fromisoformat(ts_str))
        except:
            pass

    # Daily breakdown
    daily_trades = defaultdict(int)
    daily_volume = defaultdict(float)
    hourly_trades = defaultdict(int)
    for i, ts in enumerate(timestamps):
        daily_trades[ts.date()] += 1
        hourly_trades[ts.hour] += 1
        if i < len(sizes_usd):
            daily_volume[ts.date()] += sizes_usd[i]

    # Market concentration
    markets = defaultdict(lambda: {"count": 0, "volume": 0, "buys": 0, "sells": 0, "title": ""})
    for t in trades:
        mkt = t.get("market", t.get("conditionId", t.get("condition_id", "unknown")))
        markets[mkt]["count"] += 1
        markets[mkt]["title"] = t.get("title", t.get("market_slug", str(mkt)[:30]))
        try:
            markets[mkt]["volume"] += float(t.get("usdcSize", t.get("usdc_size", 0)))
        except:
            pass
        if str(t.get("side", "")).upper() == "BUY":
            markets[mkt]["buys"] += 1
        else:
            markets[mkt]["sells"] += 1

    # Outcome analysis
    outcomes = defaultdict(int)
    for t in trades:
        o = t.get("outcome", t.get("outcomeName", "unknown"))
        outcomes[o] += 1

    # Fee estimation
    total_est_fees = sum(estimate_fee(p) for p in prices) * (sum(sizes_usd) / len(sizes_usd) if sizes_usd else 0)

    return {
        "total_trades": total,
        "buys": len(buys),
        "sells": len(sells),
        "buy_pct": 100 * len(buys) / total if total else 0,
        "sell_pct": 100 * len(sells) / total if total else 0,
        "prices": prices,
        "sizes_usd": sizes_usd,
        "sizes_shares": sizes_shares,
        "price_buckets": dict(price_buckets),
        "timestamps": timestamps,
        "daily_trades": dict(daily_trades),
        "daily_volume": dict(daily_volume),
        "hourly_trades": dict(hourly_trades),
        "markets": dict(markets),
        "unique_markets": len(markets),
        "outcomes": dict(outcomes),
        "total_volume": sum(sizes_usd) if sizes_usd else 0,
        "avg_trade_size": sum(sizes_usd) / len(sizes_usd) if sizes_usd else 0,
        "median_trade_size": sorted(sizes_usd)[len(sizes_usd) // 2] if sizes_usd else 0,
        "max_trade_size": max(sizes_usd) if sizes_usd else 0,
        "min_trade_size": min(sizes_usd) if sizes_usd else 0,
        "avg_price": sum(prices) / len(prices) if prices else 0,
        "est_total_fees": total_est_fees,
    }


def analyze_activity_deep(activity):
    """Analyze all on-chain activity types."""
    if not activity:
        return {}

    types = defaultdict(lambda: {"count": 0, "total_usdc": 0, "total_tokens": 0})
    for a in activity:
        atype = a.get("type", "UNKNOWN")
        types[atype]["count"] += 1
        try:
            types[atype]["total_usdc"] += float(a.get("usdcSize", 0))
        except:
            pass
        try:
            types[atype]["total_tokens"] += float(a.get("size", 0))
        except:
            pass

    return dict(types)


def analyze_positions_deep(positions, closed_positions):
    """Analyze open + closed positions for P&L."""
    results = {
        "open": {"count": 0, "total_value": 0, "total_cost": 0, "unrealized_pnl": 0},
        "closed": {"count": 0, "total_payout": 0, "total_cost": 0, "realized_pnl": 0,
                   "wins": 0, "losses": 0, "breakeven": 0},
        "by_market": {},
    }

    # Open positions
    for p in (positions or []):
        results["open"]["count"] += 1
        try:
            size = float(p.get("size", 0))
            price = float(p.get("curPrice", p.get("currentPrice", 0)))
            cost = float(p.get("initialValue", p.get("avgPrice", price) * size))
            value = size * price
            results["open"]["total_value"] += value
            results["open"]["total_cost"] += cost
        except:
            pass

    results["open"]["unrealized_pnl"] = results["open"]["total_value"] - results["open"]["total_cost"]

    # Closed positions
    for p in (closed_positions or []):
        results["closed"]["count"] += 1
        try:
            pnl = float(p.get("cashPnl", p.get("pnl", 0)))
            results["closed"]["realized_pnl"] += pnl
            if pnl > 0.001:
                results["closed"]["wins"] += 1
            elif pnl < -0.001:
                results["closed"]["losses"] += 1
            else:
                results["closed"]["breakeven"] += 1
        except:
            pass

    closed_total = results["closed"]["wins"] + results["closed"]["losses"]
    results["closed"]["win_rate"] = (
        100 * results["closed"]["wins"] / closed_total if closed_total > 0 else 0
    )

    return results


def analyze_arbitrage_patterns(trades):
    """
    Detect binary arbitrage patterns:
    - Same market, both YES and NO purchased within short timeframe
    - Calculate fill rates for both legs
    """
    if not trades:
        return {}

    # Group trades by market and time window (5 minutes)
    market_windows = defaultdict(list)
    for t in trades:
        mkt = t.get("market", t.get("conditionId", "unknown"))
        ts_raw = t.get("timestamp", t.get("createdAt", ""))
        try:
            if isinstance(ts_raw, (int, float)):
                if ts_raw > 1e12:
                    ts_raw = ts_raw / 1000
                ts = ts_raw
            else:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp()
        except:
            ts = 0

        window = int(ts // 300) * 300  # 5-min windows
        key = f"{mkt}_{window}"
        market_windows[key].append(t)

    # Analyze each window for arbitrage patterns
    arb_attempts = 0
    arb_complete = 0  # Both YES and NO filled
    arb_partial = 0   # Only one side filled
    arb_pnls = []

    for key, window_trades in market_windows.items():
        outcomes_traded = set()
        for t in window_trades:
            outcome = str(t.get("outcome", t.get("outcomeName", ""))).upper()
            if "YES" in outcome or "UP" in outcome:
                outcomes_traded.add("YES")
            elif "NO" in outcome or "DOWN" in outcome:
                outcomes_traded.add("NO")

        if len(outcomes_traded) >= 1:  # At least attempting arb
            arb_attempts += 1

        if len(outcomes_traded) >= 2:  # Both sides traded
            arb_complete += 1
            # Calculate arb P&L
            total_cost = sum(float(t.get("usdcSize", 0)) for t in window_trades)
            total_shares = sum(float(t.get("size", 0)) for t in window_trades)
            if total_cost > 0:
                # In binary arb: payout is $1 per share pair
                # Simplified: edge = 1.0 - total_cost_per_pair
                arb_pnls.append(1.0 - total_cost / max(total_shares, 1))
        elif len(outcomes_traded) == 1:
            arb_partial += 1

    return {
        "total_windows": len(market_windows),
        "arb_attempts": arb_attempts,
        "arb_complete_both_sides": arb_complete,
        "arb_partial_one_side": arb_partial,
        "full_fill_rate": 100 * arb_complete / arb_attempts if arb_attempts > 0 else 0,
        "partial_rate": 100 * arb_partial / arb_attempts if arb_attempts > 0 else 0,
        "avg_arb_edge": sum(arb_pnls) / len(arb_pnls) if arb_pnls else 0,
        "arb_pnls": arb_pnls,
    }


def compute_consistency(pnls):
    """Sharpe-like consistency metric: mean / std."""
    if len(pnls) < 2:
        return 0
    mean = sum(pnls) / len(pnls)
    variance = sum((x - mean) ** 2 for x in pnls) / (len(pnls) - 1)
    std = math.sqrt(variance) if variance > 0 else 0.001
    return min(abs(mean) / std, 3.0)  # Cap at 3.0


# ─── REPORT ──────────────────────────────────────────────────────

def print_report(trade_analysis, activity_analysis, position_analysis, arb_analysis, portfolio_value):
    """Print comprehensive report."""
    ta = trade_analysis
    pa = position_analysis
    arb = arb_analysis

    print(f"\n{'='*80}")
    print(f"  DEEP TRADER ANALYSIS REPORT")
    print(f"  Address: {TRADER_ADDRESS}")
    print(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*80}")

    # ── SUMMARY ──
    print(f"\n┌─────────────────────────────────────────────┐")
    print(f"│  EXECUTIVE SUMMARY                          │")
    print(f"├─────────────────────────────────────────────┤")
    if portfolio_value:
        print(f"│  Portfolio Value: ${portfolio_value:>18,.2f}     │" if isinstance(portfolio_value, (int, float)) else f"│  Portfolio Value: {portfolio_value}  │")
    if pa:
        realized = pa["closed"]["realized_pnl"]
        unrealized = pa["open"]["unrealized_pnl"]
        total_pnl = realized + unrealized
        print(f"│  Realized P&L:   ${realized:>18,.2f}     │")
        print(f"│  Unrealized P&L: ${unrealized:>18,.2f}     │")
        print(f"│  Total P&L:      ${total_pnl:>18,.2f}     │")
        print(f"│  Win Rate:       {pa['closed']['win_rate']:>18.1f}%    │")
    if ta:
        print(f"│  Total Trades:   {ta['total_trades']:>18,}     │")
        print(f"│  Total Volume:   ${ta['total_volume']:>18,.2f}     │")
        print(f"│  Unique Markets: {ta['unique_markets']:>18,}     │")
    print(f"└─────────────────────────────────────────────┘")

    # ── TRADE STATS ──
    if ta and ta["total_trades"] > 0:
        print(f"\n{'─'*60}")
        print(f"  TRADE STATISTICS")
        print(f"{'─'*60}")
        print(f"  Total trades:      {ta['total_trades']:>10,}")
        print(f"  Buys:              {ta['buys']:>10,} ({ta['buy_pct']:.1f}%)")
        print(f"  Sells:             {ta['sells']:>10,} ({ta['sell_pct']:.1f}%)")
        print(f"  Total volume:      ${ta['total_volume']:>12,.2f}")
        print(f"  Avg trade size:    ${ta['avg_trade_size']:>12,.2f}")
        print(f"  Median trade size: ${ta['median_trade_size']:>12,.2f}")
        print(f"  Max trade size:    ${ta['max_trade_size']:>12,.2f}")
        print(f"  Min trade size:    ${ta['min_trade_size']:>12,.2f}")
        print(f"  Avg price:         {ta['avg_price']:>12.4f}")
        print(f"  Est. total fees:   ${ta['est_total_fees']:>12,.2f}")

        # Price distribution
        if ta["price_buckets"]:
            print(f"\n  PRICE DISTRIBUTION:")
            max_count = max(ta["price_buckets"].values())
            for bucket in sorted(ta["price_buckets"].keys()):
                count = ta["price_buckets"][bucket]
                bar_len = int(count * 40 / max_count) if max_count > 0 else 0
                bar = "█" * bar_len
                print(f"    {bucket:.1f}: {count:>5} {bar}")

        # Time analysis
        if ta["timestamps"]:
            ts_sorted = sorted(ta["timestamps"])
            active_days = len(ta["daily_trades"])
            total_days = (ts_sorted[-1] - ts_sorted[0]).days + 1
            avg_daily = ta["total_trades"] / active_days if active_days > 0 else 0
            max_daily = max(ta["daily_trades"].values()) if ta["daily_trades"] else 0

            print(f"\n  TIME ANALYSIS:")
            print(f"    First trade:     {ts_sorted[0].strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"    Last trade:      {ts_sorted[-1].strftime('%Y-%m-%d %H:%M UTC')}")
            print(f"    Calendar days:   {total_days}")
            print(f"    Active days:     {active_days}")
            print(f"    Activity rate:   {100 * active_days / total_days:.1f}%")
            print(f"    Avg trades/day:  {avg_daily:.1f}")
            print(f"    Max trades/day:  {max_daily}")

            # Hourly heatmap
            print(f"\n  HOURLY ACTIVITY (UTC):")
            if ta["hourly_trades"]:
                max_h = max(ta["hourly_trades"].values())
                for h in range(24):
                    count = ta["hourly_trades"].get(h, 0)
                    bar_len = int(count * 30 / max_h) if max_h > 0 else 0
                    bar = "▓" * bar_len
                    print(f"    {h:02d}:00  {count:>5}  {bar}")

        # Outcome breakdown
        if ta["outcomes"] and list(ta["outcomes"].keys()) != ["unknown"]:
            print(f"\n  OUTCOME BREAKDOWN:")
            for outcome, count in sorted(ta["outcomes"].items(), key=lambda x: -x[1]):
                pct = 100 * count / ta["total_trades"]
                print(f"    {outcome:>20s}: {count:>6} ({pct:.1f}%)")

        # Top markets
        if ta["markets"]:
            print(f"\n  TOP 20 MARKETS BY VOLUME:")
            sorted_markets = sorted(ta["markets"].items(), key=lambda x: x[1]["volume"], reverse=True)[:20]
            for mkt_id, data in sorted_markets:
                title = data["title"][:40] if data["title"] else str(mkt_id)[:40]
                print(f"    {title:40s} | Trades:{data['count']:>5} | Vol:${data['volume']:>12,.2f} | B/S:{data['buys']}/{data['sells']}")

    # ── ACTIVITY BREAKDOWN ──
    if activity_analysis:
        print(f"\n{'─'*60}")
        print(f"  ON-CHAIN ACTIVITY BREAKDOWN")
        print(f"{'─'*60}")
        for atype, data in sorted(activity_analysis.items(), key=lambda x: -x[1]["count"]):
            print(f"    {atype:15s}: {data['count']:>6} events | USDC: ${data['total_usdc']:>12,.2f} | Tokens: {data['total_tokens']:>12,.2f}")

    # ── POSITION ANALYSIS ──
    if pa:
        print(f"\n{'─'*60}")
        print(f"  POSITION ANALYSIS")
        print(f"{'─'*60}")
        print(f"  Open positions:    {pa['open']['count']:>8}")
        print(f"  Open value:        ${pa['open']['total_value']:>12,.2f}")
        print(f"  Open cost:         ${pa['open']['total_cost']:>12,.2f}")
        print(f"  Unrealized P&L:    ${pa['open']['unrealized_pnl']:>12,.2f}")
        print(f"")
        print(f"  Closed positions:  {pa['closed']['count']:>8}")
        print(f"  Realized P&L:      ${pa['closed']['realized_pnl']:>12,.2f}")
        print(f"  Wins:              {pa['closed']['wins']:>8}")
        print(f"  Losses:            {pa['closed']['losses']:>8}")
        print(f"  Breakeven:         {pa['closed']['breakeven']:>8}")
        print(f"  Win Rate:          {pa['closed']['win_rate']:>8.1f}%")

    # ── ARBITRAGE ANALYSIS ──
    if arb and arb.get("arb_attempts", 0) > 0:
        print(f"\n{'─'*60}")
        print(f"  ARBITRAGE PATTERN ANALYSIS")
        print(f"{'─'*60}")
        print(f"  Time windows analyzed:    {arb['total_windows']:>8}")
        print(f"  Arb attempts detected:    {arb['arb_attempts']:>8}")
        print(f"  Complete (both sides):    {arb['arb_complete_both_sides']:>8}")
        print(f"  Partial (one side only):  {arb['arb_partial_one_side']:>8}")
        print(f"")
        print(f"  ┌───────────────────────────────────────────┐")
        print(f"  │  FILL RATE (Full Arb):   {arb['full_fill_rate']:>8.1f}%         │")
        print(f"  │  PARTIAL RATE:           {arb['partial_rate']:>8.1f}%         │")
        print(f"  │  AVG EDGE PER ARB:       {arb['avg_arb_edge']*100:>8.2f} cents    │")
        print(f"  └───────────────────────────────────────────┘")

        if arb["arb_pnls"]:
            consistency = compute_consistency(arb["arb_pnls"])
            avg_edge = sum(arb["arb_pnls"]) / len(arb["arb_pnls"])
            std_edge = math.sqrt(sum((x - avg_edge)**2 for x in arb["arb_pnls"]) / max(len(arb["arb_pnls"]) - 1, 1))
            print(f"\n  EDGE STATISTICS:")
            print(f"    Mean edge:       {avg_edge*100:.3f} cents")
            print(f"    Std edge:        {std_edge*100:.3f} cents")
            print(f"    Consistency:     {consistency:.2f} (Sharpe-like, max 3.0)")
            print(f"    Min edge:        {min(arb['arb_pnls'])*100:.3f} cents")
            print(f"    Max edge:        {max(arb['arb_pnls'])*100:.3f} cents")

    print(f"\n{'='*80}")
    print(f"  END OF REPORT")
    print(f"{'='*80}\n")


# ─── MAIN ────────────────────────────────────────────────────────

def main():
    global TRADER_ADDRESS

    if len(sys.argv) > 1 and sys.argv[1] == "--address":
        TRADER_ADDRESS = sys.argv[2]

    print(f"{'='*80}")
    print(f"  POLYMARKET TRADER DEEP ANALYSIS")
    print(f"  Target: {TRADER_ADDRESS}")
    print(f"{'='*80}")

    # Fetch all data
    trades = fetch_all_trades(TRADER_ADDRESS)
    activity = fetch_all_activity(TRADER_ADDRESS)
    positions = fetch_positions(TRADER_ADDRESS)
    closed = fetch_closed_positions(TRADER_ADDRESS)
    portfolio_value = fetch_portfolio_value(TRADER_ADDRESS)

    # Save raw data
    raw = {
        "address": TRADER_ADDRESS,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "trades": trades,
        "activity": activity,
        "positions": positions,
        "closed_positions": closed,
        "portfolio_value": portfolio_value,
    }
    outfile = f"trader_data_{TRADER_ADDRESS[:10]}.json"
    with open(outfile, "w") as f:
        json.dump(raw, f, indent=2, default=str)
    print(f"\nRaw data saved to {outfile}")

    # Analyze
    total_fetched = len(trades) + len(activity) + len(positions) + len(closed)
    if total_fetched == 0:
        print("\n⚠  No data fetched. Possible reasons:")
        print("   1. API requires different IP / no proxy")
        print("   2. Address may be incomplete (should be 42 chars: 0x + 40 hex)")
        print(f"      Your address: {TRADER_ADDRESS} ({len(TRADER_ADDRESS)} chars)")
        print("   3. Try running this script from a local machine without proxy")
        print("\n   Run: python analyze_trader.py")
        return

    trade_analysis = analyze_trades_deep(trades)
    activity_analysis = analyze_activity_deep(activity)
    position_analysis = analyze_positions_deep(positions, closed)
    arb_analysis = analyze_arbitrage_patterns(trades)

    pv = None
    if portfolio_value and isinstance(portfolio_value, dict):
        pv = portfolio_value.get("value", portfolio_value.get("totalValue", None))
    elif isinstance(portfolio_value, (int, float)):
        pv = portfolio_value

    print_report(trade_analysis, activity_analysis, position_analysis, arb_analysis, pv)


if __name__ == "__main__":
    main()
