# AutoResearch: Binary Arbitrage on Polymarket

## What this is

An autonomous research loop that experiments with a binary arbitrage strategy
on Polymarket's 5-minute "Up or Down" crypto markets.

**Binary Arbitrage**: Buy BOTH YES and NO tokens. If combined cost < $1.00,
you profit when the market resolves (guaranteed $1.00 payout).

## Setup

- 5 coins: BTC, ETH, SOL, XRP, DOGE
- Polling: every 30 seconds
- Markets: 5-minute resolution
- Paper trading only (realistic simulation with exact fees)

## The file you modify

**`strategy.py`** is the ONLY file you edit. Everything else is read-only.

The contract:
```python
def decide(observations: list, history: list, config: dict) -> list:
    # observations = data from 5 coins (prices, spreads, orderbooks)
    # history = trades from this session
    # config = global config dict
    # Returns: list of trade decisions, or empty list
```

## Experimentation rules

1. **One change at a time**. State your hypothesis before modifying code.
2. **Each experiment**: 30 min baseline + 30 min test.
3. **Metric**: RAPR (Risk-Adjusted Profit Rate) = pnl_per_hour * consistency * fill_rate
4. **Keep if**: RAPR improves >5% with p-value < 0.10
5. **Confirm if**: Improvement >30% with p < 0.01 (repeat once to verify)
6. **Discard if**: No improvement or p > 0.10
7. **Never stop**: Continue experimenting until manually interrupted.

## What you CAN do

- Change any parameter in strategy.py
- Add new filters (spread, depth, timing, volatility)
- Implement orderbook analysis (walk depth, detect walls, imbalance)
- Use cross-coin signals (correlations, if BTC has gap does ETH too?)
- Implement timing logic (enter early vs late in the 5-min window)
- Dynamic position sizing (more when gap is large, less when small)
- Add any helper functions or logic you want
- Import standard library modules

## What you CANNOT do

- Modify orchestrator.py, paper_trader.py, scorer.py, db.py
- Modify the decide() function signature
- Place real trades (paper mode only)
- Install new packages

## Polymarket fee formula

```
fee_per_side = price * (1 - price) * 0.022
```

At p=0.50: fee = 0.55% per side, ~1.1% round-trip.
Your edge (gap) must exceed total fees to profit.

## Experiment ideas (prioritized)

### Phase 1: Threshold tuning
1. MIN_GAP_CENTS: try 0.5, 1.0, 1.5, 2.0, 3.0, 5.0
2. MAX_TOTAL_COST: try 0.98, 0.985, 0.99, 0.995
3. MAX_SPREAD: try 0.02, 0.03, 0.05, 0.08

### Phase 2: Filters
4. Depth filter: only trade when orderbook depth > $X on both sides
5. Volatility filter: skip during high volatility (gap might be noise)
6. Spread asymmetry: skip if one side has much wider spread

### Phase 3: Timing & sizing
7. Window timing: only enter in first/last N minutes of 5-min window
8. Dynamic sizing: size = base * (gap / avg_gap)
9. Cooldown: don't trade same coin twice in a row

### Phase 4: Advanced
10. Orderbook imbalance: trade when book is balanced (less likely to move)
11. Cross-coin: if 3+ coins have gaps, market is dislocated → trade all
12. Gap trend: enter when gap is widening, skip when narrowing
13. Binance momentum: skip if price moving fast (gap may close)

## Reading results

Check `results.tsv` for the full experiment history:
```
experiment  rapr      p_value  improvement  status   hypothesis
1           0.000123  0.3200   +0.0%        discard  baseline
2           0.000156  0.0800   +26.8%       keep     Change MIN_GAP_CENTS to 1.0
```

## The loop

```
FOREVER:
  1. Baseline (30 min): run current strategy
  2. Mutate strategy.py (one change)
  3. Test (30 min): run modified strategy
  4. Compare: Welch's t-test + RAPR
  5. Keep or discard
  6. Repeat
```
