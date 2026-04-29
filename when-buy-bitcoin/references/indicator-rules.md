# Indicator And Classification Rules

This reference is the compact source of truth for the `when-buy-bitcoin`
dashboard. Use closed UTC candles for confirmed signals.

## Market Data

- Primary source: Binance public REST API.
- Default symbol: `BTCUSDT`.
- Fetch daily OHLCV from `2017-08-17` to present.
- Exclude open or incomplete daily candles.
- Cache market data locally.
- If Binance fails, attempt CoinGecko.
- If all live data fails but cache exists, use cache and clearly mark it.
- If no live data and no cache exist, render an error dashboard.

## Optional On-Chain Data

Use Bitcoin Lab only when `BITCOIN_LAB_API_TOKEN` is present.

Attempt:

- `/v2/market_value_to_realized_value/mvrv_z`
- `/v2/market_value_to_realized_value/mvrv`
- `/v2/realizedprice/realized_price`
- `/v2/supply_in_profitloss/supply_in_profit`
- `/v2/supply_in_profitloss/supply_in_loss`
- `/v2/supply_in_profitloss/supply_in_profit_lth`
- `/v2/supply_in_profitloss/supply_in_loss_lth`
- `/v2/supply_in_profitloss/supply_in_profit_sth`
- `/v2/supply_in_profitloss/supply_in_loss_sth`

Any missing, unauthorized, subscription-limited, or failed metric is unavailable
and reduces confidence. Do not crash.

Bitcoin Lab does not currently expose a direct Balanced Price metric in the
configured metric set. Mark Balanced Price unavailable rather than deriving a
proxy.

## Indicator Formulas

SMA:

```text
rolling mean(close, n)
```

EMA:

```text
pandas ewm(span=n, adjust=False).mean()
```

Wilder RSI:

```text
delta = close.diff()
gain = max(delta, 0)
loss = abs(min(delta, 0))
avg_gain = ewm(gain, alpha=1/period, adjust=False)
avg_loss = ewm(loss, alpha=1/period, adjust=False)
RSI = 100 - 100 / (1 + avg_gain / avg_loss)
```

Stochastic RSI:

```text
RSI first
stoch = (RSI - rolling_min_RSI) / (rolling_max_RSI - rolling_min_RSI)
K = rolling_mean(stoch, 3) * 100
D = rolling_mean(K, 3)
```

LMACD:

```text
MACD calculated on log(close)
fast EMA = 12
slow EMA = 26
signal EMA = 9
histogram = MACD line - signal line
```

Simplified BBWP:

```text
middle = 20-period SMA
upper = middle + 2 * rolling_std
lower = middle - 2 * rolling_std
width = (upper - lower) / middle
BBWP = percentile rank of width over lookback
```

Default lookbacks:

- Daily Simplified BBWP: 252
- Weekly Simplified BBWP: 52

Distance to moving average:

```text
distance_pct = (price / moving_average - 1) * 100
```

Near a long-term moving average defaults to within `+/- 5%`.

## Daily Indicators

- Latest closed daily close
- 50D SMA
- 200D SMA
- 50D / 200D golden cross
- 50D / 200D death cross
- RSI 14
- Stochastic RSI
- LMACD
- Simplified BBWP

## Weekly Indicators

Resample daily candles into closed UTC weekly candles ending Sunday.

- Weekly OHLCV
- 20W SMA
- 21W EMA
- BMSB lower = `min(20W SMA, 21W EMA)`
- BMSB upper = `max(20W SMA, 21W EMA)`
- 50W, 100W, 200W, 300W, 400W SMA
- Weekly RSI 14
- 20W average weekly volume
- Price above / below BMSB
- Weekly BMSB reclaim
- Weekly BMSB reclaim with volume
- Price above / below 50W SMA
- Price below 100W SMA
- Two consecutive weekly closes below 100W SMA
- Price near 200W SMA
- Price below 200W SMA
- Price near or below 300W SMA
- Price near or below 400W SMA
- BMSB / 50W SMA death cross or bearish structure
- BMSB / 50W SMA bullish cross or reclaim structure

BMSB reclaim:

```text
latest closed weekly candle closes above BMSB upper
AND previous closed weekly candle was at or below BMSB upper
```

BMSB reclaim with volume:

```text
BMSB reclaim
AND latest weekly volume > 20W average weekly volume
```

## Monthly, 2M, 3M Indicators

Monthly:

- Monthly RSI
- Monthly Stochastic RSI
- Monthly LMACD
- Monthly Stoch RSI below 20

Two-month:

- 2M Stochastic RSI
- 2M LMACD
- 2M Stoch RSI near zero when K < 10

Three-month:

- 3M LMACD
- 3M LMACD histogram red
- Bearish momentum deepening when latest red histogram is lower than the prior
  red histogram

## Supply Profit/Loss Convergence

If both values are available:

```text
gap = abs(supply_profit - supply_loss) / max(supply_profit, supply_loss)
```

Converged when `gap <= 0.15`.

Loss exceeds profit when:

```text
supply_loss > supply_profit
```

## Bottom Zone Score

Price-structure bottom signals:

- Weekly close below 100W SMA: `+1`
- Two consecutive weekly closes below 100W SMA: `+1`
- Price near 200W SMA: `+1`
- Price below 200W SMA: `+1`
- Price near or below 300W SMA: `+1`
- Price near or below 400W SMA: `+1`
- BMSB / 50W SMA death cross or bearish structure active: `+1`

On-chain bottom signals:

- MVRV Z-Score below 0: `+2`
- BTC price below Realized Price: `+1`
- BTC price below Balanced Price: `+2`
- Supply in Profit and Supply in Loss convergence gap <= 15%: `+2`
- Supply in Loss greater than Supply in Profit: `+3`

Momentum exhaustion:

- Monthly Stoch RSI below 20: `+1`
- 2M Stoch RSI K below 10: `+1`
- 2M or 3M LMACD printed multiple red histograms and appears mature: `+1`

Timing:

- Current date inside configured `BTC_CYCLE_BOTTOM_WINDOW`: `+1`

Do not count low BBWP as a bottom signal by itself. Show it only as a
volatility-risk warning when low and expanding while bearish structure remains.

## Trend Confirmation Score

- Weekly close above BMSB upper: `+1`
- Weekly BMSB reclaim with volume: `+2`
- Weekly close above 50W SMA: `+2`
- 50D / 200D golden cross: `+1`
- BMSB / 50W bullish cross or bullish reclaim structure: `+1`
- Price holds above BMSB after retest, if detectable: `+1`
- Weekly close above `BTC_INVALIDATION_LEVEL`, if configured: `+1`

## Phase Labels

1. Bear Market / Preserve Cash
   Action: "Do not rush. Preserve cash. Avoid FOMO on counter-trend rallies."

2. Capitulation Approach
   Action: "Prepare a plan. Watch the 200W SMA and on-chain capitulation signals."

3. Bottom Watch
   Action: "Small DCA only, if this matches your predefined plan. Keep most cash available."

4. Left-Side Accumulation Zone
   Action: "Consider measured, staged accumulation. Do not go all-in. Expect volatility."

5. High-Conviction Bottom Candidate
   Action: "This is a strong bottom-candidate zone. Use a planned ladder; still wait for confirmation before maximum allocation."

6. Right-Side Confirmation
   Action: "Trend is improving. Consider deploying remaining planned allocation in stages."

7. Bull Recovery / Risk-On Confirmed
   Action: "Bear-market bottoming phase may be complete. Shift from bottom-hunting to trend-following risk management."

## Confidence

Confidence depends on:

- Data availability
- Data freshness
- Number of independent signals
- Whether on-chain data is available
- Whether signals come from closed candles

If on-chain data is unavailable, confidence should usually be capped at Medium
unless price confirmation is very strong. If data is stale, confidence drops.
If only price data is available, clearly say the analysis is price-structure
heavy.
