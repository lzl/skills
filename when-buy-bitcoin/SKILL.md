---
name: when-buy-bitcoin
description: Use this skill whenever the user wants a daily Bitcoin market-regime dashboard, BTC cycle phase read, cautious rules-based buy-zone analysis, or an automation that collects BTC market data, optional Glassnode on-chain data, technical indicators, and a self-contained English HTML report. Prefer this skill for prompts like "when buy bitcoin", "classify BTC market phase", "daily BTC dashboard", "Bitcoin bottom watch", or "rules-based BTC accumulation signal" even when the user does not explicitly mention the skill name.
---

# When Buy Bitcoin

Use this skill to generate a daily, automation-friendly Bitcoin market-regime
dashboard. The skill answers:

> According to the current Bitcoin price structure, momentum, and available
> on-chain signals, what phase are we in, and what should a disciplined investor
> do?

The output is not a trading bot, does not place trades, and does not give
absolute financial advice. Frame every result as a rules-based regime read.
The dashboard must visibly include:

> This is not financial advice. It is a rules-based market-regime dashboard.

## What It Collects

Primary market data comes from Binance public REST API:

- Default symbol: `BTCUSDT`
- Daily OHLCV from `2017-08-17` to present
- Paginated requests as needed
- UTC closed daily candles only for confirmed calculations
- Local cache where possible

If Binance fails, the script attempts a public CoinGecko fallback. If live data
fails but cached market data exists, it uses the cache and marks the dashboard
accordingly. If no live data and no cache are available, it renders an error
state instead of crashing.

Optional on-chain data comes from Glassnode only when `GLASSNODE_API_KEY` is
present. Unavailable metrics are marked unavailable and reduce confidence.
Missing API keys, subscription limits, permission errors, and API failures must
never stop the dashboard from rendering.

## Bundled Script

The deterministic implementation lives in `scripts/run.py`. The automation
command will be chosen later, so do not assume one fixed command format in the
skill instructions. When you need to execute the skill manually, prefer the
bundled script and its flags instead of rewriting the workflow.

If `uv` is available, use `uv` to manage dependencies and run the script. From
the workspace root, use this pattern:

```bash
uv run --project when-buy-bitcoin python when-buy-bitcoin/scripts/run.py --offline-sample
uv run --project when-buy-bitcoin python when-buy-bitcoin/scripts/run.py --self-test
uv run --project when-buy-bitcoin python when-buy-bitcoin/scripts/run.py --no-onchain
```

These are manual invocation examples, not a fixed automation command. When the
skill path differs, pass that path to `--project` and to the script location.
Run from the workspace root when you want generated artifacts to land under the
root `output/when-buy-bitcoin/` directory.

Useful script modes:

- `--offline-sample`: create deterministic synthetic BTC data and render a
  sample dashboard without network access.
- `--no-onchain`: skip Glassnode and mark on-chain data unavailable.
- `--json-only`: write only the machine-readable JSON report.
- `--self-test`: run lightweight formula checks for the indicator functions.
- `--output-dir DIR`: choose where `when-buy-bitcoin.html` and
  `when-buy-bitcoin.latest.json` are written.

Runtime dependencies are declared in `pyproject.toml` for `uv`: Python 3.10+,
`pandas`, `numpy`, and `requests`. `requirements.txt` is kept as a compatibility
fallback for environments where `uv` is not available.

## Outputs

By default the script writes generated report artifacts under
`output/when-buy-bitcoin/`:

- `output/when-buy-bitcoin/when-buy-bitcoin.html`
- `output/when-buy-bitcoin/when-buy-bitcoin.latest.json`

The JSON follows this high-level structure:

```json
{
  "generated_at_utc": "...",
  "data_sources": {},
  "btc": {},
  "scores": {},
  "classification": {},
  "indicators": {}
}
```

The HTML dashboard is self-contained with inline CSS and inline JavaScript. It
uses no remote fonts, no CDNs, and no external charting libraries. It includes a
vanilla canvas price chart.

## Environment Variables

Optional configuration can come from `.env` or the process environment:

```dotenv
GLASSNODE_API_KEY=
BTC_DASHBOARD_OUTPUT_DIR=output/when-buy-bitcoin
BTC_DASHBOARD_SYMBOL=BTCUSDT
BTC_DASHBOARD_MARKET_SOURCE=binance
BTC_CYCLE_BOTTOM_WINDOW=
BTC_CYCLE_HIGH=
BTC_CYCLE_LOW=
BTC_INVALIDATION_LEVEL=
TZ=UTC
```

Do not write real API keys to disk and do not print secrets in logs or output
files. Keep local `.env` files ignored.

## Regime Philosophy

Bitcoin's own price action takes priority over external narratives.

Use confluence. Do not overfit to one indicator, and do not let a single metric
trigger a strong conclusion. Keep bottom-zone capitulation evidence separate
from right-side recovery evidence:

- Bottom Zone Score: price-structure stress, on-chain valuation, momentum
  exhaustion, and optional timing window.
- Trend Confirmation Score: BMSB/50W reclaim structure, volume confirmation,
  moving-average recovery, golden cross, retest behavior, and optional
  invalidation-level reclaim.

Treat unfinished candles as provisional. Confirmed signals use closed UTC
daily, weekly, monthly, 2-month, and 3-month candles only. Weekly candles are
resampled from daily candles into weeks ending Sunday.

Read `references/indicator-rules.md` when you need the exact scoring and
indicator definitions.

## Dashboard Requirements

The dashboard should be English, polished, responsive, and readable. Let the
local design guidance shape layout, spacing, hierarchy, typography, motion, and
presentation quality. Keep the interface professional and analytical rather
than promotional. Do not make it look like a trading terminal that implies
execution.

Include:

- Title: `When Buy Bitcoin?`
- Subtitle: `Daily rules-based BTC market-regime analysis`
- Generated timestamp and data freshness
- Current BTC price
- Current Phase
- Recommended Action
- Confidence
- Bottom Zone Score
- Trend Confirmation Score
- Main reasons
- Missing confirmations
- Next levels to watch
- Price Structure
- On-Chain Valuation
- Momentum Exhaustion
- Right-Side Confirmation
- Data Quality
- Visible disclaimer

## Safety Boundaries

Do not build a trading bot. Do not place trades. Do not provide absolute
financial advice. Use cautious language such as "prepare a plan", "small DCA
only if this matches your predefined plan", "measured staged accumulation", and
"wait for confirmation" depending on the phase.

If on-chain data is unavailable, say the analysis is price-structure heavy and
usually cap confidence at Medium unless price confirmation is very strong. If
data is stale, lower confidence.
