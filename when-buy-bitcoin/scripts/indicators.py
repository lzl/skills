"""Indicator formulas for the when-buy-bitcoin dashboard.

The functions here intentionally avoid TA-Lib so the skill can run in a small,
automation-friendly Python environment.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd


UTC = dt.timezone.utc


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.astype("float64").diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.where(avg_loss != 0, 100)
    rsi = rsi.where(avg_gain != 0, 0)
    return rsi.clip(lower=0, upper=100)


def stoch_rsi(
    close_or_rsi: pd.Series,
    period: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
    input_is_rsi: bool = False,
) -> pd.DataFrame:
    rsi = close_or_rsi.astype("float64") if input_is_rsi else rsi_wilder(close_or_rsi, period)
    rolling_min = rsi.rolling(period, min_periods=period).min()
    rolling_max = rsi.rolling(period, min_periods=period).max()
    denominator = (rolling_max - rolling_min).replace(0, np.nan)
    stoch = (rsi - rolling_min) / denominator
    k = stoch.rolling(smooth_k, min_periods=smooth_k).mean() * 100
    d = k.rolling(smooth_d, min_periods=smooth_d).mean()
    return pd.DataFrame({"rsi": rsi, "stoch": stoch * 100, "k": k.clip(0, 100), "d": d.clip(0, 100)})


def log_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    log_close = np.log(close.astype("float64").replace(0, np.nan))
    fast_ema = ema(log_close, fast)
    slow_ema = ema(log_close, slow)
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "histogram": histogram}
    )


def simplified_bbwp(
    close: pd.Series,
    length: int = 20,
    lookback: int = 252,
    stddevs: float = 2.0,
) -> pd.Series:
    middle = sma(close.astype("float64"), length)
    std = close.astype("float64").rolling(length, min_periods=length).std()
    upper = middle + stddevs * std
    lower = middle - stddevs * std
    width = (upper - lower) / middle.replace(0, np.nan)

    min_periods = max(10, min(lookback, lookback // 4))

    def percentile_rank(window: pd.Series) -> float:
        latest = window.iloc[-1]
        if pd.isna(latest):
            return np.nan
        return float((window <= latest).sum() / window.notna().sum() * 100)

    return width.rolling(lookback, min_periods=min_periods).apply(percentile_rank, raw=False)


def distance_pct(price: float | None, moving_average: float | None) -> float | None:
    if price is None or moving_average in (None, 0) or pd.isna(price) or pd.isna(moving_average):
        return None
    return (float(price) / float(moving_average) - 1) * 100


def latest_value(series: pd.Series, default: Any = None) -> Any:
    clean = series.dropna()
    if clean.empty:
        return default
    value = clean.iloc[-1]
    if isinstance(value, np.generic):
        return value.item()
    return value


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cross_above(fast: pd.Series, slow: pd.Series) -> bool:
    pair = pd.DataFrame({"fast": fast, "slow": slow}).dropna()
    if len(pair) < 2:
        return False
    prev = pair.iloc[-2]
    latest = pair.iloc[-1]
    return bool(prev["fast"] <= prev["slow"] and latest["fast"] > latest["slow"])


def cross_below(fast: pd.Series, slow: pd.Series) -> bool:
    pair = pd.DataFrame({"fast": fast, "slow": slow}).dropna()
    if len(pair) < 2:
        return False
    prev = pair.iloc[-2]
    latest = pair.iloc[-1]
    return bool(prev["fast"] >= prev["slow"] and latest["fast"] < latest["slow"])


def latest_complete_week_end(latest_daily_date: pd.Timestamp) -> pd.Timestamp:
    latest = pd.Timestamp(latest_daily_date).normalize()
    days_since_sunday = (latest.weekday() + 1) % 7
    return latest - pd.Timedelta(days=days_since_sunday)


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    resampled = df.resample(rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return resampled.dropna(subset=["open", "high", "low", "close"])


def closed_weekly(df: pd.DataFrame) -> pd.DataFrame:
    weekly = resample_ohlcv(df, "W-SUN")
    if df.empty or weekly.empty:
        return weekly
    latest_week_end = latest_complete_week_end(df.index.max())
    return weekly.loc[weekly.index <= latest_week_end]


def closed_period(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    period = resample_ohlcv(df, rule)
    if df.empty or period.empty:
        return period
    return period.loc[period.index <= pd.Timestamp(df.index.max()).normalize()]


def add_daily_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    out = daily.copy()
    out["sma_50"] = sma(out["close"], 50)
    out["sma_200"] = sma(out["close"], 200)
    out["rsi_14"] = rsi_wilder(out["close"], 14)
    stoch = stoch_rsi(out["close"], 14, 3, 3)
    out["stoch_rsi_k"] = stoch["k"]
    out["stoch_rsi_d"] = stoch["d"]
    macd = log_macd(out["close"])
    out["lmacd"] = macd["macd"]
    out["lmacd_signal"] = macd["signal"]
    out["lmacd_histogram"] = macd["histogram"]
    out["simplified_bbwp"] = simplified_bbwp(out["close"], 20, 252)
    return out


def add_weekly_indicators(weekly: pd.DataFrame) -> pd.DataFrame:
    out = weekly.copy()
    out["sma_20"] = sma(out["close"], 20)
    out["ema_21"] = ema(out["close"], 21)
    out["bmsb_lower"] = out[["sma_20", "ema_21"]].min(axis=1)
    out["bmsb_upper"] = out[["sma_20", "ema_21"]].max(axis=1)
    out["sma_50"] = sma(out["close"], 50)
    out["sma_100"] = sma(out["close"], 100)
    out["sma_200"] = sma(out["close"], 200)
    out["sma_300"] = sma(out["close"], 300)
    out["sma_400"] = sma(out["close"], 400)
    out["rsi_14"] = rsi_wilder(out["close"], 14)
    out["avg_volume_20"] = sma(out["volume"], 20)
    out["simplified_bbwp"] = simplified_bbwp(out["close"], 20, 52)
    return out


def add_monthly_indicators(monthly: pd.DataFrame) -> pd.DataFrame:
    out = monthly.copy()
    out["rsi_14"] = rsi_wilder(out["close"], 14)
    stoch = stoch_rsi(out["close"], 14, 3, 3)
    out["stoch_rsi_k"] = stoch["k"]
    out["stoch_rsi_d"] = stoch["d"]
    macd = log_macd(out["close"])
    out["lmacd"] = macd["macd"]
    out["lmacd_signal"] = macd["signal"]
    out["lmacd_histogram"] = macd["histogram"]
    return out


def latest_daily_summary(daily_i: pd.DataFrame) -> dict[str, Any]:
    latest = daily_i.iloc[-1] if not daily_i.empty else pd.Series(dtype="float64")
    close = safe_float(latest.get("close"))
    sma_50 = safe_float(latest.get("sma_50"))
    sma_200 = safe_float(latest.get("sma_200"))
    return {
        "latest_closed_daily": daily_i.index[-1].date().isoformat() if not daily_i.empty else None,
        "close": close,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "sma_50_distance_pct": distance_pct(close, sma_50),
        "sma_200_distance_pct": distance_pct(close, sma_200),
        "golden_cross_50_200": cross_above(daily_i["sma_50"], daily_i["sma_200"]),
        "death_cross_50_200": cross_below(daily_i["sma_50"], daily_i["sma_200"]),
        "rsi_14": safe_float(latest.get("rsi_14")),
        "stoch_rsi_k": safe_float(latest.get("stoch_rsi_k")),
        "stoch_rsi_d": safe_float(latest.get("stoch_rsi_d")),
        "lmacd": safe_float(latest.get("lmacd")),
        "lmacd_signal": safe_float(latest.get("lmacd_signal")),
        "lmacd_histogram": safe_float(latest.get("lmacd_histogram")),
        "simplified_bbwp": safe_float(latest.get("simplified_bbwp")),
    }


def latest_weekly_summary(weekly_i: pd.DataFrame, near_threshold: float = 5.0) -> dict[str, Any]:
    latest = weekly_i.iloc[-1] if not weekly_i.empty else pd.Series(dtype="float64")
    prev = weekly_i.iloc[-2] if len(weekly_i) >= 2 else pd.Series(dtype="float64")
    close = safe_float(latest.get("close"))
    bmsb_upper = safe_float(latest.get("bmsb_upper"))
    bmsb_lower = safe_float(latest.get("bmsb_lower"))
    sma_50 = safe_float(latest.get("sma_50"))
    sma_100 = safe_float(latest.get("sma_100"))
    sma_200 = safe_float(latest.get("sma_200"))
    sma_300 = safe_float(latest.get("sma_300"))
    sma_400 = safe_float(latest.get("sma_400"))
    avg_volume_20 = safe_float(latest.get("avg_volume_20"))
    volume = safe_float(latest.get("volume"))
    prev_bmsb_upper = safe_float(prev.get("bmsb_upper"))
    prev_close = safe_float(prev.get("close"))

    def near_or_below(ma: float | None) -> bool:
        dist = distance_pct(close, ma)
        return bool(dist is not None and dist <= near_threshold)

    price_above_bmsb = bool(close is not None and bmsb_upper is not None and close > bmsb_upper)
    price_below_bmsb = bool(close is not None and bmsb_lower is not None and close < bmsb_lower)
    bmsb_reclaim = bool(
        close is not None
        and bmsb_upper is not None
        and prev_close is not None
        and prev_bmsb_upper is not None
        and close > bmsb_upper
        and prev_close <= prev_bmsb_upper
    )
    bmsb_reclaim_with_volume = bool(
        bmsb_reclaim and volume is not None and avg_volume_20 is not None and volume > avg_volume_20
    )
    below_100_series = (weekly_i["close"] < weekly_i["sma_100"]).dropna()
    two_below_100 = bool(len(below_100_series) >= 2 and below_100_series.iloc[-1] and below_100_series.iloc[-2])

    bmsb_mid = (weekly_i["bmsb_lower"] + weekly_i["bmsb_upper"]) / 2
    bearish_structure = cross_below(bmsb_mid, weekly_i["sma_50"]) or bool(
        latest.get("bmsb_upper", np.nan) < latest.get("sma_50", np.nan)
        and latest.get("close", np.nan) < latest.get("sma_50", np.nan)
    )
    bullish_structure = cross_above(bmsb_mid, weekly_i["sma_50"]) or bool(
        price_above_bmsb and close is not None and sma_50 is not None and close > sma_50
    )

    recent_above_bmsb = (weekly_i["close"] > weekly_i["bmsb_upper"]).dropna()
    held_bmsb_retest = bool(
        len(recent_above_bmsb) >= 4
        and recent_above_bmsb.iloc[-1]
        and recent_above_bmsb.tail(4).sum() >= 3
        and weekly_i["low"].iloc[-1] <= weekly_i["bmsb_upper"].iloc[-1] * 1.03
    )

    return {
        "latest_closed_weekly": weekly_i.index[-1].date().isoformat() if not weekly_i.empty else None,
        "close": close,
        "bmsb_lower": bmsb_lower,
        "bmsb_upper": bmsb_upper,
        "sma_50": sma_50,
        "sma_100": sma_100,
        "sma_200": sma_200,
        "sma_300": sma_300,
        "sma_400": sma_400,
        "rsi_14": safe_float(latest.get("rsi_14")),
        "volume": volume,
        "avg_volume_20": avg_volume_20,
        "simplified_bbwp": safe_float(latest.get("simplified_bbwp")),
        "price_above_bmsb": price_above_bmsb,
        "price_below_bmsb": price_below_bmsb,
        "bmsb_reclaim": bmsb_reclaim,
        "bmsb_reclaim_with_volume": bmsb_reclaim_with_volume,
        "price_above_50w_sma": bool(close is not None and sma_50 is not None and close > sma_50),
        "price_below_50w_sma": bool(close is not None and sma_50 is not None and close < sma_50),
        "price_below_100w_sma": bool(close is not None and sma_100 is not None and close < sma_100),
        "two_consecutive_weekly_closes_below_100w_sma": two_below_100,
        "price_near_200w_sma": bool(distance_pct(close, sma_200) is not None and abs(distance_pct(close, sma_200)) <= near_threshold),
        "price_below_200w_sma": bool(close is not None and sma_200 is not None and close < sma_200),
        "price_near_or_below_300w_sma": near_or_below(sma_300),
        "price_near_or_below_400w_sma": near_or_below(sma_400),
        "distance_to_50w_pct": distance_pct(close, sma_50),
        "distance_to_100w_pct": distance_pct(close, sma_100),
        "distance_to_200w_pct": distance_pct(close, sma_200),
        "distance_to_300w_pct": distance_pct(close, sma_300),
        "distance_to_400w_pct": distance_pct(close, sma_400),
        "bmsb_50w_death_cross_or_bearish_structure": bearish_structure,
        "bmsb_50w_bullish_cross_or_reclaim_structure": bullish_structure,
        "holds_above_bmsb_after_retest": held_bmsb_retest,
    }


def period_summary(period_i: pd.DataFrame, label: str) -> dict[str, Any]:
    latest = period_i.iloc[-1] if not period_i.empty else pd.Series(dtype="float64")
    hist = period_i["lmacd_histogram"].dropna() if "lmacd_histogram" in period_i else pd.Series(dtype="float64")
    red_count = int((hist.tail(4) < 0).sum()) if not hist.empty else 0
    deepening = bool(len(hist) >= 2 and hist.iloc[-1] < hist.iloc[-2] < 0)
    return {
        f"latest_closed_{label}": period_i.index[-1].date().isoformat() if not period_i.empty else None,
        "close": safe_float(latest.get("close")),
        "rsi_14": safe_float(latest.get("rsi_14")),
        "stoch_rsi_k": safe_float(latest.get("stoch_rsi_k")),
        "stoch_rsi_d": safe_float(latest.get("stoch_rsi_d")),
        "stoch_rsi_below_20": bool(safe_float(latest.get("stoch_rsi_k")) is not None and safe_float(latest.get("stoch_rsi_k")) < 20),
        "stoch_rsi_near_zero": bool(safe_float(latest.get("stoch_rsi_k")) is not None and safe_float(latest.get("stoch_rsi_k")) < 10),
        "lmacd": safe_float(latest.get("lmacd")),
        "lmacd_signal": safe_float(latest.get("lmacd_signal")),
        "lmacd_histogram": safe_float(latest.get("lmacd_histogram")),
        "lmacd_histogram_red": bool(safe_float(latest.get("lmacd_histogram")) is not None and safe_float(latest.get("lmacd_histogram")) < 0),
        "lmacd_red_histograms_last_4": red_count,
        "bearish_momentum_deepening": deepening,
    }


def calculate_all_indicators(daily: pd.DataFrame) -> dict[str, Any]:
    daily_i = add_daily_indicators(daily)
    weekly_i = add_weekly_indicators(closed_weekly(daily))
    monthly_i = add_monthly_indicators(closed_period(daily, "ME"))
    two_month_i = add_monthly_indicators(closed_period(daily, "2ME"))
    three_month_i = add_monthly_indicators(closed_period(daily, "3ME"))

    daily_summary = latest_daily_summary(daily_i)
    weekly_summary = latest_weekly_summary(weekly_i)
    monthly_summary = period_summary(monthly_i, "monthly")
    two_month_summary = period_summary(two_month_i, "two_month")
    three_month_summary = period_summary(three_month_i, "three_month")

    chart_tail = daily_i.tail(365)
    chart_history = [
        {
            "date": index.date().isoformat(),
            "close": safe_float(row.get("close")),
            "sma_50": safe_float(row.get("sma_50")),
            "sma_200": safe_float(row.get("sma_200")),
        }
        for index, row in chart_tail.iterrows()
    ]
    daily_summary["price_history_365"] = chart_history

    return {
        "daily": daily_summary,
        "weekly": weekly_summary,
        "monthly": monthly_summary,
        "two_month": two_month_summary,
        "three_month": three_month_summary,
        "_frames": {
            "daily": daily_i,
            "weekly": weekly_i,
            "monthly": monthly_i,
            "two_month": two_month_i,
            "three_month": three_month_i,
        },
    }
