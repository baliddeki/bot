"""
deriv_client.py — Deriv WebSocket API client for market data.

Uses the NEW Deriv API (api.derivws.com), not the legacy binaryws endpoint.

Public market data requires NO authentication and NO App ID.
Candle data is fetched via the ticks_history call with style="candles".

Supported granularities (seconds):
  60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400
  → M1, M2, M3, M5, M10, M15, M30, H1, H2, H4, H8, D1

W1 and MN are not native — resampled from D1 automatically.

Deriv API docs: https://developers.deriv.com/docs/
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import websockets

import config
from logger import log_event


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

# Public endpoint — no auth, no App ID required
_PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"

# Allowed granularities by Deriv (in seconds)
_ALLOWED_GRANULARITIES = {60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400}

# Native timeframes: label → granularity in seconds
_NATIVE_TIMEFRAMES = {
    tf: gran
    for tf, gran in config.TIMEFRAMES.items()
    if gran is not None
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_candles(
    symbol:      str,
    granularity: int,
    count:       int = 500,
    date_from:   Optional[datetime] = None,
    date_to:     Optional[datetime] = None,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candle data from Deriv's public WebSocket endpoint.

    Args:
        symbol:      Deriv symbol, e.g. "frxXAUUSD".
        granularity: Candle size in seconds. Must be one of the allowed values.
        count:       Number of recent candles (used when date_from is None).
        date_from:   Start of date range (UTC). Triggers range-mode fetch.
        date_to:     End of date range (UTC). Defaults to now if not provided.

    Returns:
        DataFrame with columns [time, open, high, low, close, volume],
        sorted oldest → newest. Returns None on failure.
    """
    if granularity not in _ALLOWED_GRANULARITIES:
        log_event(
            f"Deriv: unsupported granularity {granularity}s. "
            f"Allowed: {sorted(_ALLOWED_GRANULARITIES)}",
            level="ERROR",
        )
        return None

    try:
        if date_from:
            rows = _run(_fetch_range(symbol, granularity, date_from, date_to))
        else:
            rows = _run(_fetch_recent(symbol, granularity, count))

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.sort_values("time").reset_index(drop=True)
        return df

    except Exception as exc:
        log_event(f"Deriv fetch error [{symbol} {granularity}s]: {exc}", level="ERROR")
        return None


def fetch_all_timeframes(
    symbol:    str = config.SYMBOL,
    date_from: Optional[datetime] = None,
    date_to:   Optional[datetime] = None,
) -> dict:
    """
    Fetch candles for every timeframe in config.TIMEFRAMES.

    W1 and MN are resampled from D1 candles since Deriv does not provide them natively.

    Returns:
        Dict mapping TF label → DataFrame (or None if fetch failed).
    """
    data: dict = {}

    # ── Fetch all natively supported timeframes ───────────────────────────
    for tf, granularity in _NATIVE_TIMEFRAMES.items():
        count = config.CANDLE_HISTORY.get(tf, 500)
        df = fetch_candles(
            symbol      = symbol,
            granularity = granularity,
            count       = count,
            date_from   = date_from,
            date_to     = date_to,
        )
        data[tf] = df
        status = f"{len(df)} candles" if df is not None else "FAILED"
        log_event(f"  [{tf:3s}] {status}")

    # ── Resample W1 and MN from D1 ────────────────────────────────────────
    d1_df = data.get("D1")

    if d1_df is not None:
        data["W1"] = _resample(d1_df, "W",  config.CANDLE_HISTORY.get("W1", 52))
        data["MN"] = _resample(d1_df, "ME", config.CANDLE_HISTORY.get("MN", 24))
        log_event(f"  [W1 ] resampled from D1 → {len(data['W1'])} candles")
        log_event(f"  [MN ] resampled from D1 → {len(data['MN'])} candles")
    else:
        data["W1"] = None
        data["MN"] = None
        log_event("  [W1 ] skipped — D1 unavailable", level="WARNING")
        log_event("  [MN ] skipped — D1 unavailable", level="WARNING")

    return data


def ping() -> bool:
    """
    Ping the Deriv public WebSocket. Useful for testing connectivity.
    Returns True if the server responds correctly.
    """
    try:
        response = _run(_ws_request({"ping": 1}))
        return response.get("ping") == "pong"
    except Exception as exc:
        log_event(f"Deriv ping failed: {exc}", level="ERROR")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Resampling  (D1 → W1 / MN)
# ─────────────────────────────────────────────────────────────────────────────

def _resample(df_daily: pd.DataFrame, freq: str, target_count: int) -> pd.DataFrame:
    """
    Resample a daily OHLCV DataFrame to a lower frequency.

    Args:
        df_daily:     D1 DataFrame with a UTC-aware 'time' column.
        freq:         Pandas offset alias — "W" for weekly, "ME" for month-end.
        target_count: Number of completed periods to keep (most recent N).
    """
    df = df_daily.copy().set_index("time")

    resampled = df.resample(freq).agg(
        open   = ("open",   "first"),
        high   = ("high",   "max"),
        low    = ("low",    "min"),
        close  = ("close",  "last"),
        volume = ("volume", "sum"),
    ).dropna()

    # Drop the current incomplete period
    if len(resampled) > 1:
        resampled = resampled.iloc[:-1]

    return resampled.tail(target_count).reset_index()


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket transport
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro) -> list | dict:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


async def _ws_request(payload: dict) -> dict:
    """
    Open a connection to the public endpoint, send one request, return the response.
    No authentication or App ID required for public market data.
    """
    async with websockets.connect(_PUBLIC_WS_URL) as ws:
        await ws.send(json.dumps(payload))
        response = json.loads(await ws.recv())

    if "error" in response:
        raise RuntimeError(
            f"Deriv API error: {response['error'].get('message', response['error'])}"
        )
    return response


async def _fetch_recent(symbol: str, granularity: int, count: int) -> list[dict]:
    """Fetch the most recent N completed candles."""
    payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "count":       count + 1,   # +1 because the live open candle is trimmed below
        "end":         "latest",
        "granularity": granularity,
        "style":       "candles",
    }
    response = await _ws_request(payload)
    candles  = response.get("candles", [])

    # Drop the last candle — it's the currently forming (incomplete) candle
    if candles:
        candles = candles[:-1]

    return [_parse_candle(c) for c in candles]


async def _fetch_range(
    symbol:      str,
    granularity: int,
    date_from:   datetime,
    date_to:     Optional[datetime],
) -> list[dict]:
    """Fetch candles between two UTC datetimes."""
    end_epoch = (
        int(date_to.replace(tzinfo=timezone.utc).timestamp())
        if date_to
        else int(datetime.now(timezone.utc).timestamp())
    )
    payload = {
        "ticks_history": symbol,
        "adjust_start_time": 1,
        "start":       int(date_from.replace(tzinfo=timezone.utc).timestamp()),
        "end":         end_epoch,
        "granularity": granularity,
        "style":       "candles",
    }
    response = await _ws_request(payload)
    candles  = response.get("candles", [])

    # If fetching up to now, the last candle may be incomplete
    if not date_to and candles:
        candles = candles[:-1]

    return [_parse_candle(c) for c in candles]


def _parse_candle(candle: dict) -> dict:
    """
    Parse a Deriv candle dict into a flat row dict.

    Deriv keys: epoch, open, high, low, close
    Volume is not provided for forex/metals — set to 0 for schema compatibility.
    """
    return {
        "time":   pd.Timestamp(candle["epoch"], unit="s", tz="UTC"),
        "open":   float(candle["open"]),
        "high":   float(candle["high"]),
        "low":    float(candle["low"]),
        "close":  float(candle["close"]),
        "volume": 0,
    }
