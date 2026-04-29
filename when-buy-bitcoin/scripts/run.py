#!/usr/bin/env python3
"""Generate the When Buy Bitcoin rules-based market-regime dashboard."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys
from typing import Any

try:
    import numpy as np
    import pandas as pd
except ModuleNotFoundError as exc:
    skill_dir = pathlib.Path(__file__).resolve().parents[1]
    print(
        "Missing Python dependency: "
        f"{exc.name}. If uv is available, run with "
        f"`uv run --project {skill_dir} python {pathlib.Path(__file__).resolve()} ...`. "
        "If uv is not available, install the fallback requirements with "
        f"`python -m pip install -r {skill_dir / 'requirements.txt'}`.",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import classifier  # noqa: E402
import data_sources  # noqa: E402
import indicators  # noqa: E402
import render_html  # noqa: E402


UTC = dt.timezone.utc
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_OUTPUT_DIR = "output/when-buy-bitcoin"
DEFAULT_CACHE_DIR = "output/when-buy-bitcoin/cache"


def parse_env_file(path: pathlib.Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_environment(env_path: pathlib.Path) -> dict[str, str]:
    values = parse_env_file(env_path)
    merged = dict(values)
    allowed_exact = {"BITCOIN_LAB_API_TOKEN", "TZ"}
    merged.update({key: value for key, value in os.environ.items() if key.startswith("BTC_") or key in allowed_exact})
    return merged


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def expected_latest_closed_daily(now: dt.datetime) -> dt.date:
    return (now.astimezone(UTC).date() - dt.timedelta(days=1))


def freshness_label(latest_daily: str | None, now: dt.datetime) -> tuple[str, bool]:
    if not latest_daily:
        return "Unavailable", True
    try:
        latest_date = dt.date.fromisoformat(latest_daily)
    except ValueError:
        return "Unavailable", True
    expected = expected_latest_closed_daily(now)
    age_days = (expected - latest_date).days
    if age_days <= 0:
        return f"Fresh through {latest_date.isoformat()} UTC", False
    if age_days == 1:
        return f"One day behind expected UTC close ({latest_date.isoformat()})", False
    return f"Stale by {age_days} days ({latest_date.isoformat()})", True


def parse_float_env(values: dict[str, str], key: str) -> float | None:
    raw = values.get(key, "").strip()
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def parse_cycle_window(raw: str | None, now: dt.datetime) -> bool:
    if not raw:
        return False
    normalized = raw.replace(" to ", ",").replace(":", ",")
    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(parts) != 2:
        return False
    try:
        start = dt.date.fromisoformat(parts[0])
        end = dt.date.fromisoformat(parts[1])
    except ValueError:
        return False
    today = now.astimezone(UTC).date()
    return start <= today <= end


def synthetic_market_data(now: dt.datetime) -> pd.DataFrame:
    end = pd.Timestamp(expected_latest_closed_daily(now))
    dates = pd.date_range("2017-08-17", end, freq="D")
    x = np.arange(len(dates), dtype="float64")
    trend = np.exp(np.log(3800) + x * 0.00078)
    cycle = np.exp(np.sin(x / 265) * 0.55 + np.sin(x / 89) * 0.18)
    capitulation = 1 - 0.38 * np.exp(-((x - len(x) * 0.72) ** 2) / (2 * (len(x) * 0.055) ** 2))
    recovery = 1 + 0.18 / (1 + np.exp(-(x - len(x) * 0.83) / 45))
    close = trend * cycle * capitulation * recovery
    open_ = np.r_[close[0], close[:-1]] * (1 + 0.006 * np.sin(x / 7))
    high = np.maximum(open_, close) * (1.015 + 0.01 * (np.sin(x / 13) + 1))
    low = np.minimum(open_, close) * (0.985 - 0.006 * (np.cos(x / 11) + 1) / 2)
    volume = 18500 + (np.sin(x / 31) + 1) * 5500 + np.where(close < pd.Series(close).rolling(200, min_periods=1).mean(), 8500, 0)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )


def synthetic_onchain(price: float) -> dict[str, Any]:
    return {
        "mvrv_z_score": {"available": True, "value": -0.18, "date": None, "error": None},
        "mvrv_ratio": {"available": True, "value": 0.92, "date": None, "error": None},
        "realized_price": {"available": True, "value": price * 1.08, "date": None, "error": None},
        "balanced_price": {"available": True, "value": price * 0.74, "date": None, "error": None},
        "supply_profit": {"available": True, "value": 8_800_000, "date": None, "error": None},
        "supply_loss": {"available": True, "value": 9_600_000, "date": None, "error": None},
        "lth_supply_profit": {"available": False, "value": None, "date": None, "error": "Offline sample does not model LTH metrics."},
        "lth_supply_loss": {"available": False, "value": None, "date": None, "error": "Offline sample does not model LTH metrics."},
        "sth_supply_profit": {"available": False, "value": None, "date": None, "error": "Offline sample does not model STH metrics."},
        "sth_supply_loss": {"available": False, "value": None, "date": None, "error": "Offline sample does not model STH metrics."},
    }


def format_money(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"${value:,.0f}"


def format_pct(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:+.1f}%"


def onchain_value(onchain: dict[str, Any], key: str) -> float | None:
    payload = onchain.get(key, {})
    if not isinstance(payload, dict) or not payload.get("available"):
        return None
    value = payload.get("value")
    try:
        if value is None or math.isnan(float(value)):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def add_reason(reasons: list[str], condition: bool, text: str) -> None:
    if condition:
        reasons.append(text)


def add_missing(missing: list[str], condition: bool, text: str) -> None:
    if not condition:
        missing.append(text)


def build_signal_state(
    report_indicators: dict[str, Any],
    onchain: dict[str, Any],
    env: dict[str, str],
    now: dt.datetime,
    data_stale: bool,
) -> classifier.SignalState:
    daily = report_indicators["daily"]
    weekly = report_indicators["weekly"]
    monthly = report_indicators["monthly"]
    two_month = report_indicators["two_month"]
    three_month = report_indicators["three_month"]
    price = daily.get("close")

    supply_state = classifier.supply_profit_loss_state(
        onchain_value(onchain, "supply_profit"),
        onchain_value(onchain, "supply_loss"),
    )
    mvrv_z = onchain_value(onchain, "mvrv_z_score")
    realized_price = onchain_value(onchain, "realized_price")
    balanced_price = onchain_value(onchain, "balanced_price")
    onchain_available = any(isinstance(value, dict) and value.get("available") for value in onchain.values())
    cycle_window = parse_cycle_window(env.get("BTC_CYCLE_BOTTOM_WINDOW"), now)
    invalidation_level = parse_float_env(env, "BTC_INVALIDATION_LEVEL")

    lmacd_mature_red = bool(
        (two_month.get("lmacd_red_histograms_last_4") or 0) >= 2
        or (three_month.get("lmacd_red_histograms_last_4") or 0) >= 2
    )

    bottom_signals = {
        "weekly_close_below_100w_sma": bool(weekly.get("price_below_100w_sma")),
        "two_weekly_closes_below_100w_sma": bool(weekly.get("two_consecutive_weekly_closes_below_100w_sma")),
        "price_near_200w_sma": bool(weekly.get("price_near_200w_sma")),
        "price_below_200w_sma": bool(weekly.get("price_below_200w_sma")),
        "price_near_or_below_300w_sma": bool(weekly.get("price_near_or_below_300w_sma")),
        "price_near_or_below_400w_sma": bool(weekly.get("price_near_or_below_400w_sma")),
        "bmsb_50w_bearish_structure": bool(weekly.get("bmsb_50w_death_cross_or_bearish_structure")),
        "mvrv_z_score_below_0": bool(mvrv_z is not None and mvrv_z < 0),
        "price_below_realized_price": bool(price is not None and realized_price is not None and price < realized_price),
        "price_below_balanced_price": bool(price is not None and balanced_price is not None and price < balanced_price),
        "supply_profit_loss_converged": bool(supply_state.get("converged")),
        "supply_loss_exceeds_profit": bool(supply_state.get("loss_exceeds_profit")),
        "monthly_stoch_rsi_below_20": bool(monthly.get("stoch_rsi_below_20")),
        "two_month_stoch_rsi_k_below_10": bool(two_month.get("stoch_rsi_near_zero")),
        "lmacd_mature_red": lmacd_mature_red,
        "cycle_bottom_window": cycle_window,
    }
    trend_signals = {
        "weekly_close_above_bmsb_upper": bool(weekly.get("price_above_bmsb")),
        "bmsb_reclaim_with_volume": bool(weekly.get("bmsb_reclaim_with_volume")),
        "weekly_close_above_50w_sma": bool(weekly.get("price_above_50w_sma")),
        "golden_cross_50d_200d": bool(daily.get("golden_cross_50_200")),
        "bmsb_50w_bullish_structure": bool(weekly.get("bmsb_50w_bullish_cross_or_reclaim_structure")),
        "holds_above_bmsb_after_retest": bool(weekly.get("holds_above_bmsb_after_retest")),
        "weekly_close_above_invalidation_level": bool(
            price is not None and invalidation_level is not None and price > invalidation_level
        ),
    }

    reasons: list[str] = []
    add_reason(reasons, bottom_signals["weekly_close_below_100w_sma"], "Weekly close is below the 100W SMA.")
    add_reason(reasons, bottom_signals["two_weekly_closes_below_100w_sma"], "Two consecutive weekly closes are below the 100W SMA.")
    add_reason(reasons, bottom_signals["price_near_200w_sma"], f"Price is near the 200W SMA ({format_pct(weekly.get('distance_to_200w_pct'))}).")
    add_reason(reasons, bottom_signals["price_below_200w_sma"], "Price is below the 200W SMA.")
    add_reason(reasons, bottom_signals["price_near_or_below_300w_sma"], "Price is near or below the 300W SMA.")
    add_reason(reasons, bottom_signals["price_near_or_below_400w_sma"], "Price is near or below the 400W SMA.")
    add_reason(reasons, bottom_signals["bmsb_50w_bearish_structure"], "BMSB / 50W bearish structure is active.")
    add_reason(reasons, bottom_signals["mvrv_z_score_below_0"], "MVRV Z-Score is below 0.")
    add_reason(reasons, bottom_signals["price_below_realized_price"], "BTC is below realized price.")
    add_reason(reasons, bottom_signals["price_below_balanced_price"], "BTC is below balanced price.")
    add_reason(reasons, bottom_signals["supply_profit_loss_converged"], "Supply in profit and supply in loss are converged within 15%.")
    add_reason(reasons, bottom_signals["supply_loss_exceeds_profit"], "Supply in loss is greater than supply in profit.")
    add_reason(reasons, bottom_signals["monthly_stoch_rsi_below_20"], "Monthly Stochastic RSI is below 20.")
    add_reason(reasons, bottom_signals["two_month_stoch_rsi_k_below_10"], "2M Stochastic RSI K is below 10.")
    add_reason(reasons, bottom_signals["lmacd_mature_red"], "2M or 3M LMACD bearish momentum looks mature.")
    add_reason(reasons, trend_signals["weekly_close_above_bmsb_upper"], "Weekly close is above the BMSB upper band.")
    add_reason(reasons, trend_signals["bmsb_reclaim_with_volume"], "Weekly BMSB reclaim occurred with volume above the 20W average.")
    add_reason(reasons, trend_signals["weekly_close_above_50w_sma"], "Weekly close is above the 50W SMA.")
    add_reason(reasons, trend_signals["golden_cross_50d_200d"], "Daily 50D / 200D golden cross is active.")
    add_reason(reasons, trend_signals["bmsb_50w_bullish_structure"], "BMSB / 50W bullish reclaim structure is detectable.")

    bbwp = daily.get("simplified_bbwp")
    if (
        bbwp is not None
        and bbwp < 20
        and (weekly.get("price_below_bmsb") or weekly.get("bmsb_50w_death_cross_or_bearish_structure"))
    ):
        reasons.append("Volatility-risk warning: Simplified BBWP is low while bearish price structure remains active.")

    missing: list[str] = []
    add_missing(missing, trend_signals["weekly_close_above_bmsb_upper"], "Weekly close above the BMSB upper band.")
    add_missing(missing, trend_signals["bmsb_reclaim_with_volume"], "Weekly BMSB reclaim with volume above the 20W average.")
    add_missing(missing, trend_signals["weekly_close_above_50w_sma"], "Weekly close above the 50W SMA.")
    add_missing(missing, trend_signals["golden_cross_50d_200d"], "50D / 200D golden cross.")
    if not onchain_available:
        missing.append("Bitcoin Lab on-chain valuation metrics are unavailable; confidence is reduced.")
    if invalidation_level is not None:
        add_missing(missing, trend_signals["weekly_close_above_invalidation_level"], f"Weekly close above configured invalidation level {format_money(invalidation_level)}.")

    next_levels = [
        f"BMSB upper: {format_money(weekly.get('bmsb_upper'))}",
        f"BMSB lower: {format_money(weekly.get('bmsb_lower'))}",
        f"50W SMA: {format_money(weekly.get('sma_50'))}",
        f"100W SMA: {format_money(weekly.get('sma_100'))}",
        f"200W SMA: {format_money(weekly.get('sma_200'))}",
        f"300W SMA: {format_money(weekly.get('sma_300'))}",
        f"400W SMA: {format_money(weekly.get('sma_400'))}",
    ]
    if realized_price is not None:
        next_levels.append(f"Realized price: {format_money(realized_price)}")
    if balanced_price is not None:
        next_levels.append(f"Balanced price: {format_money(balanced_price)}")
    if invalidation_level is not None:
        next_levels.append(f"Configured invalidation level: {format_money(invalidation_level)}")

    high = parse_float_env(env, "BTC_CYCLE_HIGH")
    low = parse_float_env(env, "BTC_CYCLE_LOW")
    if high is not None and low is not None and high > low:
        fib_618 = high - (high - low) * 0.618
        fib_786 = high - (high - low) * 0.786
        next_levels.append(f"Configured Fibonacci 0.618 retrace: {format_money(fib_618)}")
        next_levels.append(f"Configured Fibonacci 0.786 retrace: {format_money(fib_786)}")

    return classifier.SignalState(
        bottom_signals=bottom_signals,
        trend_signals=trend_signals,
        unavailable_onchain=not onchain_available,
        data_stale=data_stale,
        reasons=reasons,
        missing=missing,
        next_levels=next_levels,
        context={"price_structure_heavy": not onchain_available, "supply_profit_loss": supply_state},
    )


def clean_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean_for_json(item) for key, item in value.items() if key != "_frames"}
    if isinstance(value, list):
        return [clean_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [clean_for_json(item) for item in value]
    if isinstance(value, (dt.datetime, dt.date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return clean_for_json(value.item())
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


def build_error_report(symbol: str, output_source: data_sources.MarketDataResult, now: dt.datetime) -> dict[str, Any]:
    return {
        "generated_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "data_freshness": "Unavailable",
        "error": True,
        "data_sources": {
            "market": output_source.source,
            "onchain": "unavailable",
            "fallback_used": output_source.fallback_used,
            "cache_used": output_source.cache_used,
            "errors": output_source.errors,
        },
        "btc": {
            "symbol": symbol,
            "current_price": 0,
            "latest_closed_daily": None,
            "latest_closed_weekly": None,
            "latest_closed_monthly": None,
        },
        "scores": {
            "bottom_zone_score": 0,
            "bottom_zone_score_max": sum(classifier.BOTTOM_WEIGHTS.values()),
            "trend_confirmation_score": 0,
            "trend_confirmation_score_max": sum(classifier.TREND_WEIGHTS.values()),
            "confidence": "Low",
        },
        "classification": {
            "phase": "Data Unavailable",
            "recommended_action": classifier.PHASE_ACTIONS["Data Unavailable"],
            "summary": "No live market data and no cache were available. The dashboard rendered an error state instead of crashing.",
            "main_reasons": [],
            "missing_confirmations": ["Closed daily BTC market data is required."],
            "next_levels": [],
        },
        "indicators": {
            "daily": {},
            "weekly": {},
            "monthly": {},
            "two_month": {},
            "three_month": {},
            "onchain": {},
        },
    }


def build_report(
    symbol: str,
    market: data_sources.MarketDataResult,
    onchain: dict[str, Any],
    onchain_source: str,
    onchain_errors: list[str],
    env: dict[str, str],
    now: dt.datetime,
) -> dict[str, Any]:
    if market.data.empty:
        return build_error_report(symbol, market, now)

    calculated = indicators.calculate_all_indicators(market.data)
    fresh_label, is_stale = freshness_label(calculated["daily"].get("latest_closed_daily"), now)
    signal_state = build_signal_state(calculated, onchain, env, now, is_stale)
    classification_result = classifier.classify_market(signal_state)

    data_errors = list(market.errors) + list(onchain_errors)
    report = {
        "generated_at_utc": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "data_freshness": fresh_label,
        "data_sources": {
            "market": market.source,
            "onchain": onchain_source,
            "fallback_used": market.fallback_used,
            "cache_used": market.cache_used,
            "errors": data_errors,
        },
        "btc": {
            "symbol": symbol,
            "current_price": calculated["daily"].get("close") or 0,
            "latest_closed_daily": calculated["daily"].get("latest_closed_daily"),
            "latest_closed_weekly": calculated["weekly"].get("latest_closed_weekly"),
            "latest_closed_monthly": calculated["monthly"].get("latest_closed_monthly"),
        },
        "scores": {
            "bottom_zone_score": classification_result["bottom_zone_score"],
            "bottom_zone_score_max": classification_result["bottom_zone_score_max"],
            "trend_confirmation_score": classification_result["trend_confirmation_score"],
            "trend_confirmation_score_max": classification_result["trend_confirmation_score_max"],
            "confidence": classification_result["confidence"],
        },
        "classification": {
            "phase": classification_result["phase"],
            "recommended_action": classification_result["recommended_action"],
            "summary": classification_result["summary"],
            "main_reasons": classification_result["main_reasons"],
            "missing_confirmations": classification_result["missing_confirmations"],
            "next_levels": classification_result["next_levels"],
        },
        "indicators": {
            "daily": calculated["daily"],
            "weekly": calculated["weekly"],
            "monthly": calculated["monthly"],
            "two_month": calculated["two_month"],
            "three_month": calculated["three_month"],
            "onchain": onchain,
        },
    }
    return clean_for_json(report)


def write_outputs(report: dict[str, Any], output_dir: pathlib.Path, json_only: bool) -> tuple[pathlib.Path, pathlib.Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "when-buy-bitcoin.latest.json"
    html_path = output_dir / "when-buy-bitcoin.html"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if json_only:
        return json_path, None
    html_path.write_text(render_html.render_dashboard(report), encoding="utf-8")
    return json_path, html_path


def run_self_test() -> int:
    close = pd.Series(np.linspace(100, 140, 60) + np.sin(np.arange(60)) * 2)
    rsi = indicators.rsi_wilder(close)
    stoch = indicators.stoch_rsi(close)
    macd = indicators.log_macd(close)
    bbwp = indicators.simplified_bbwp(close, length=20, lookback=30)
    checks = [
        not rsi.dropna().empty and 0 <= float(rsi.dropna().iloc[-1]) <= 100,
        not stoch["k"].dropna().empty and 0 <= float(stoch["k"].dropna().iloc[-1]) <= 100,
        not macd["histogram"].dropna().empty,
        not bbwp.dropna().empty and 0 <= float(bbwp.dropna().iloc[-1]) <= 100,
    ]
    if not all(checks):
        print("Self-test failed: one or more indicator formula checks failed.", file=sys.stderr)
        return 1
    print("Self-test passed: indicator formulas returned bounded, finite values.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the When Buy Bitcoin dashboard.")
    parser.add_argument("--offline-sample", action="store_true", help="Render a deterministic sample dashboard without network access.")
    parser.add_argument("--no-onchain", action="store_true", help="Skip optional Bitcoin Lab on-chain metric collection.")
    parser.add_argument("--json-only", action="store_true", help="Write only the machine-readable JSON report.")
    parser.add_argument("--self-test", action="store_true", help="Run lightweight indicator formula checks and exit.")
    parser.add_argument("--env", default=".env", help="Path to optional .env file.")
    parser.add_argument("--output-dir", default=None, help="Directory for generated when-buy-bitcoin report outputs.")
    parser.add_argument("--cache-dir", default=None, help="Directory for market data cache.")
    parser.add_argument("--symbol", default=None, help="Market symbol, default BTCUSDT.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()

    now = utc_now()
    env = load_environment(pathlib.Path(args.env))
    symbol = (args.symbol or env.get("BTC_DASHBOARD_SYMBOL") or DEFAULT_SYMBOL).upper()
    output_dir = pathlib.Path(args.output_dir or env.get("BTC_DASHBOARD_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR)
    cache_dir = pathlib.Path(args.cache_dir or env.get("BTC_DASHBOARD_CACHE_DIR") or DEFAULT_CACHE_DIR)
    market_source = env.get("BTC_DASHBOARD_MARKET_SOURCE", "binance")

    if args.offline_sample:
        market = data_sources.MarketDataResult(
            data=synthetic_market_data(now),
            source="offline synthetic sample",
            fallback_used=False,
            cache_used=False,
            errors=[],
        )
        latest_price = float(market.data["close"].iloc[-1])
        onchain = (
            {name: data_sources.unavailable_onchain("--no-onchain was set.") for name in data_sources.ONCHAIN_METRIC_NAMES}
            if args.no_onchain
            else synthetic_onchain(latest_price)
        )
        onchain_errors: list[str] = []
        onchain_source = "offline synthetic sample" if not args.no_onchain else "skipped"
    else:
        market = data_sources.get_market_data(symbol, market_source, cache_dir, now)
        if args.no_onchain:
            onchain = {name: data_sources.unavailable_onchain("--no-onchain was set.") for name in data_sources.ONCHAIN_METRIC_NAMES}
            onchain_errors = []
            onchain_source = "skipped"
        else:
            onchain, onchain_errors, onchain_source = data_sources.fetch_bitcoin_lab_metrics(
                env.get("BITCOIN_LAB_API_TOKEN"),
                now=now,
            )

    report = build_report(symbol, market, onchain, onchain_source, onchain_errors, env, now)
    json_path, html_path = write_outputs(report, output_dir, args.json_only)
    print(f"Wrote JSON: {json_path}")
    if html_path is not None:
        print(f"Wrote HTML: {html_path}")
    print(f"Phase: {report['classification']['phase']}")
    print(f"Confidence: {report['scores']['confidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
