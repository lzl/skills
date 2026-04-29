"""Market and optional on-chain data collection."""

from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import re
import time
from typing import Any

import pandas as pd
import requests


UTC = dt.timezone.utc
DAY_MS = 24 * 60 * 60 * 1000
BINANCE_START = dt.datetime(2017, 8, 17, tzinfo=UTC)
BINANCE_API = "https://api.binance.com/api/v3/klines"
COINGECKO_RANGE_API = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart/range"
BITCOIN_LAB_API_ROOT = "https://api.researchbitcoin.net/v2"


ONCHAIN_METRIC_NAMES = (
    "mvrv_z_score",
    "mvrv_ratio",
    "realized_price",
    "balanced_price",
    "supply_profit",
    "supply_loss",
    "lth_supply_profit",
    "lth_supply_loss",
    "sth_supply_profit",
    "sth_supply_loss",
)

BITCOIN_LAB_METRICS = {
    "mvrv_z_score": ("market_value_to_realized_value", "mvrv_z"),
    "mvrv_ratio": ("market_value_to_realized_value", "mvrv"),
    "realized_price": ("realizedprice", "realized_price"),
    "supply_profit": ("supply_in_profitloss", "supply_in_profit"),
    "supply_loss": ("supply_in_profitloss", "supply_in_loss"),
    "lth_supply_profit": ("supply_in_profitloss", "supply_in_profit_lth"),
    "lth_supply_loss": ("supply_in_profitloss", "supply_in_loss_lth"),
    "sth_supply_profit": ("supply_in_profitloss", "supply_in_profit_sth"),
    "sth_supply_loss": ("supply_in_profitloss", "supply_in_loss_sth"),
}


@dataclasses.dataclass(frozen=True)
class MarketDataResult:
    data: pd.DataFrame
    source: str
    fallback_used: bool
    cache_used: bool
    errors: list[str]


def utc_now() -> dt.datetime:
    return dt.datetime.now(tz=UTC)


def safe_error(error: Exception | str) -> str:
    message = str(error)
    lower = message.lower()
    if any(secret_hint in lower for secret_hint in ("api_key", "api-token", "api_token", "token", "authorization", "bearer")):
        return "API request failed; secret-bearing parameters were redacted."
    message = re.sub(r"(?i)(api[_-]?key|api[_-]?token|authorization)\s*[:=]\s*[^&\s]+", r"\1=<redacted>", message)
    message = re.sub(r"(?i)bearer\s+[a-z0-9._~+/-]+", "Bearer <redacted>", message)
    return message[:500]


def request_json(
    session: requests.Session,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 3,
    timeout: int = 20,
) -> Any:
    params = params or {}
    headers = headers or {}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = session.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"{url} returned HTTP {response.status_code}")
            if response.status_code in {401, 403}:
                raise PermissionError(f"{url} returned HTTP {response.status_code}")
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 - keep network handling broad.
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(8, 0.75 * (2**attempt)))
    raise RuntimeError(safe_error(last_error or "request failed"))


def normalize_market_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = df.copy()
    out.index = pd.to_datetime(out.index, utc=True).tz_convert(None).normalize()
    out = out[["open", "high", "low", "close", "volume"]].astype("float64")
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def cache_path(cache_dir: pathlib.Path, source: str, symbol: str) -> pathlib.Path:
    safe_symbol = "".join(ch for ch in symbol if ch.isalnum() or ch in {"-", "_"}).upper()
    return cache_dir / f"{source}_{safe_symbol}_1d.csv"


def read_market_cache(cache_dir: pathlib.Path, symbol: str) -> tuple[pd.DataFrame, str | None]:
    for source in ("binance", "coingecko"):
        path = cache_path(cache_dir, source, symbol)
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, parse_dates=["date"])
            df = df.set_index("date")
            return normalize_market_frame(df), source
        except Exception:
            continue
    return pd.DataFrame(), None


def write_market_cache(cache_dir: pathlib.Path, source: str, symbol: str, df: pd.DataFrame) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = normalize_market_frame(df).copy()
    out.insert(0, "date", out.index.date.astype(str))
    out.to_csv(cache_path(cache_dir, source, symbol), index=False)


def binance_frame_from_rows(rows: list[list[Any]], now: dt.datetime) -> pd.DataFrame:
    records = []
    now_ms = int(now.timestamp() * 1000)
    for row in rows:
        open_time = int(row[0])
        if open_time + DAY_MS > now_ms:
            continue
        records.append(
            {
                "date": pd.to_datetime(open_time, unit="ms", utc=True),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    if not records:
        return pd.DataFrame()
    return normalize_market_frame(pd.DataFrame.from_records(records).set_index("date"))


def fetch_binance_daily(symbol: str, cache_dir: pathlib.Path, now: dt.datetime) -> pd.DataFrame:
    cached, _ = read_market_cache(cache_dir, symbol)
    if not cached.empty:
        start = pd.Timestamp(cached.index.max()).to_pydatetime().replace(tzinfo=UTC) + dt.timedelta(days=1)
    else:
        start = BINANCE_START

    session = requests.Session()
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    rows: list[list[Any]] = []

    while start_ms < end_ms:
        payload = request_json(
            session,
            BINANCE_API,
            {
                "symbol": symbol.upper(),
                "interval": "1d",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            retries=3,
        )
        if not payload:
            break
        rows.extend(payload)
        last_open = int(payload[-1][0])
        next_start = last_open + DAY_MS
        if next_start <= start_ms:
            break
        start_ms = next_start
        time.sleep(0.05)

    fresh = binance_frame_from_rows(rows, now)
    if not cached.empty and not fresh.empty:
        combined = normalize_market_frame(pd.concat([cached, fresh]))
    elif not cached.empty:
        combined = cached
    else:
        combined = fresh

    if combined.empty:
        raise RuntimeError("Binance returned no closed daily candles.")
    write_market_cache(cache_dir, "binance", symbol, combined)
    return combined


def fetch_coingecko_daily(symbol: str, cache_dir: pathlib.Path, now: dt.datetime) -> pd.DataFrame:
    if symbol.upper() != "BTCUSDT":
        raise RuntimeError("CoinGecko fallback only supports BTCUSDT/BTC-USD equivalent.")
    session = requests.Session()
    payload = request_json(
        session,
        COINGECKO_RANGE_API,
        {
            "vs_currency": "usd",
            "from": int(BINANCE_START.timestamp()),
            "to": int(now.timestamp()),
        },
        retries=3,
        timeout=30,
    )
    prices = payload.get("prices") or []
    volumes = payload.get("total_volumes") or []
    if not prices:
        raise RuntimeError("CoinGecko returned no price rows.")

    price_df = pd.DataFrame(prices, columns=["timestamp", "price"])
    price_df["date"] = pd.to_datetime(price_df["timestamp"], unit="ms", utc=True).dt.normalize()
    ohlc = price_df.groupby("date")["price"].agg(open="first", high="max", low="min", close="last")

    if volumes:
        volume_df = pd.DataFrame(volumes, columns=["timestamp", "volume"])
        volume_df["date"] = pd.to_datetime(volume_df["timestamp"], unit="ms", utc=True).dt.normalize()
        ohlc["volume"] = volume_df.groupby("date")["volume"].last()
    else:
        ohlc["volume"] = 0.0

    latest_allowed = pd.Timestamp(now.astimezone(UTC).date()) - pd.Timedelta(days=1)
    ohlc = ohlc.loc[ohlc.index.tz_convert(None) <= latest_allowed]
    out = normalize_market_frame(ohlc)
    if out.empty:
        raise RuntimeError("CoinGecko fallback produced no closed daily candles.")
    write_market_cache(cache_dir, "coingecko", symbol, out)
    return out


def get_market_data(symbol: str, market_source: str, cache_dir: pathlib.Path, now: dt.datetime | None = None) -> MarketDataResult:
    now = now or utc_now()
    errors: list[str] = []
    source = market_source.lower().strip() or "binance"

    if source == "binance":
        try:
            return MarketDataResult(
                data=fetch_binance_daily(symbol, cache_dir, now),
                source="binance",
                fallback_used=False,
                cache_used=False,
                errors=[],
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Binance failed: {safe_error(exc)}")

    try:
        return MarketDataResult(
            data=fetch_coingecko_daily(symbol, cache_dir, now),
            source="coingecko",
            fallback_used=True,
            cache_used=False,
            errors=errors,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"CoinGecko fallback failed: {safe_error(exc)}")

    cached, cached_source = read_market_cache(cache_dir, symbol)
    if not cached.empty:
        return MarketDataResult(
            data=cached,
            source=f"{cached_source or 'market'} cache",
            fallback_used=source == "binance",
            cache_used=True,
            errors=errors,
        )

    return MarketDataResult(
        data=pd.DataFrame(),
        source="unavailable",
        fallback_used=source == "binance",
        cache_used=False,
        errors=errors or ["No live market data and no cache were available."],
    )


def latest_numeric_value(payload: Any) -> tuple[float | None, str | None]:
    if not isinstance(payload, list):
        return None, None
    for row in reversed(payload):
        if not isinstance(row, dict) or "v" not in row:
            continue
        value = row.get("v")
        if isinstance(value, dict):
            numeric_values = [v for v in value.values() if isinstance(v, (int, float))]
            value = numeric_values[0] if numeric_values else None
        if isinstance(value, (int, float)):
            timestamp = row.get("t")
            date = (
                dt.datetime.fromtimestamp(timestamp, tz=UTC).date().isoformat()
                if isinstance(timestamp, (int, float))
                else None
            )
            return float(value), date
    return None, None


def latest_bitcoin_lab_value(payload: Any, data_field: str) -> tuple[float | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    rows = payload.get("data")
    if not isinstance(rows, list):
        return None, None
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        raw_value = row.get(data_field)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        raw_time = row.get("time")
        date = None
        if isinstance(raw_time, str):
            try:
                date = dt.datetime.fromisoformat(raw_time.replace("Z", "+00:00")).date().isoformat()
            except ValueError:
                date = raw_time[:10] or None
        return value, date
    return None, None


def unavailable_onchain(reason: str) -> dict[str, Any]:
    return {"available": False, "value": None, "date": None, "error": reason}


def bitcoin_lab_query_window(now: dt.datetime | None = None) -> tuple[str, str]:
    now = now or utc_now()
    start = (now.astimezone(UTC).date() - dt.timedelta(days=14)).isoformat()
    end = (now.astimezone(UTC).date() + dt.timedelta(days=1)).isoformat()
    return start, end


def fetch_bitcoin_lab_metrics(api_token: str | None, now: dt.datetime | None = None) -> tuple[dict[str, Any], list[str], str]:
    if not api_token:
        return (
            {name: unavailable_onchain("BITCOIN_LAB_API_TOKEN is not configured.") for name in ONCHAIN_METRIC_NAMES},
            [],
            "unavailable",
        )

    from_time, to_time = bitcoin_lab_query_window(now)
    session = requests.Session()
    metrics: dict[str, Any] = {
        "balanced_price": unavailable_onchain("Bitcoin Lab does not expose a direct Balanced Price metric in the configured metric set.")
    }
    errors: list[str] = []
    headers = {"X-API-Token": api_token}
    for name, (endpoint, data_field) in BITCOIN_LAB_METRICS.items():
        try:
            payload = request_json(
                session,
                f"{BITCOIN_LAB_API_ROOT}/{endpoint}/{data_field}",
                {
                    "resolution": "d1",
                    "output_format": "json",
                    "from_time": from_time,
                    "to_time": to_time,
                },
                headers=headers,
                retries=2,
                timeout=20,
            )
            value, date = latest_bitcoin_lab_value(payload, data_field)
            if value is None:
                metrics[name] = unavailable_onchain("Metric returned no numeric value.")
            else:
                metrics[name] = {
                    "available": True,
                    "value": value,
                    "date": date,
                    "error": None,
                }
        except PermissionError:
            metrics[name] = unavailable_onchain("Metric unavailable due to Bitcoin Lab permission, quota, or token limits.")
        except Exception as exc:  # noqa: BLE001
            reason = safe_error(exc)
            metrics[name] = unavailable_onchain(reason)
            errors.append(f"Bitcoin Lab {name} unavailable: {reason}")
    for name in ONCHAIN_METRIC_NAMES:
        metrics.setdefault(name, unavailable_onchain("Metric was not requested."))
    source = "bitcoin_lab" if any(metric.get("available") for metric in metrics.values()) else "unavailable"
    return metrics, errors, source
