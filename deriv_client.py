"""
deriv_client.py — Deriv WebSocket API client for market data.

This is the sole data source for the bot.
All candles are fetched from Deriv's public WebSocket endpoint.
No authentication is required for historical price data.

Deriv does not natively support W1 or MN candles — these are
resampled from D1 data automatically.

Deriv API docs: https://api.deriv.com/
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
# Timeframes that Deriv supports natively (granularity in seconds)
# ─────────────────────────────────────────────────────────────────────────────

_NATIVE_TIMEFRAMES = {
    tf: gran
    for tf, gran in config.TIMEFRAMES.items()
    if gran is not None
}

# W1 and MN are resampled — not fetched directly
_RESAMPLED_TIMEFRAMES = {"W1", "MN"}


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
    Fetch OHLCV candle data from Deriv for a single timeframe.

    Args:
        symbol:      Deriv instrument symbol, e.g. "frxXAUUSD".
        granularity: Candle size in seconds (e.g. 3600 for H1).
        count:       Number of recent candles (used when date_from is None).
        date_from:   Start of date range (UTC). Triggers range-mode fetch.
        date_to:     End of date range (UTC). Defaults to now if not provided.

    Returns:
        DataFrame with columns [time, open, high, low, close, volume],
        sorted oldest → newest. Returns None on failure.
    """
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

    W1 and MN are resampled from D1 candles.

    Returns:
        Dict mapping TF label → DataFrame (or None if fetch failed).
    """
    data: dict = {}

    # ── Fetch all native timeframes ───────────────────────────────────────
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
        log_event("  [W1 ] skipped — D1 data unavailable", level="WARNING")
        log_event("  [MN ] skipped — D1 data unavailable", level="WARNING")

    return data


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

    Returns:
        Resampled DataFrame, oldest → newest, with the same column schema.
    """
    df = df_daily.copy()
    df = df.set_index("time")

    resampled = df.resample(freq).agg(
        open   = ("open",   "first"),
        high   = ("high",   "max"),
        low    = ("low",    "min"),
        close  = ("close",  "last"),
        volume = ("volume", "sum"),
    ).dropna()

    # Drop the current incomplete period (last row may be partial)
    if len(resampled) > 1:
        resampled = resampled.iloc[:-1]

    resampled = resampled.tail(target_count).reset_index()
    resampled = resampled.rename(columns={"time": "time"})
    return resampled


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro) -> list:
    """Run an async coroutine synchronously. Keeps the rest of the bot sync."""
    return asyncio.run(coro)


async def _ws_request(payload: dict) -> dict:
    """
    Open a WebSocket connection, send one request, return the response.

    A new connection is opened per request. This is slightly less efficient
    than a persistent connection but is far simpler to reason about in a
    single-threaded polling bot.
    """
    url = f"{config.DERIV_WS_URL}?app_id={config.DERIV_APP_ID}"
    async with websockets.connect(url) as ws:
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
        "count":        count + 1,   # +1 because the current (live) candle is excluded below
        "end":          "latest",
        "granularity":  granularity,
        "style":        "candles",
    }
    response = await _ws_request(payload)
    candles  = response.get("candles", [])

    # The last candle may be the live (incomplete) candle — drop it
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
        "start":        int(date_from.replace(tzinfo=timezone.utc).timestamp()),
        "end":          end_epoch,
        "granularity":  granularity,
        "style":        "candles",
    }
    response = await _ws_request(payload)
    candles  = response.get("candles", [])

    # Drop the last (potentially incomplete) candle when fetching up to "now"
    if not date_to and candles:
        candles = candles[:-1]

    return [_parse_candle(c) for c in candles]


def _parse_candle(candle: dict) -> dict:
    """
    Parse a Deriv candle dict into a flat row dict.

    Deriv candle keys: epoch, open, high, low, close
    Volume is not provided by Deriv — set to 0 for schema compatibility.
    """
    return {
        "time":   pd.Timestamp(candle["epoch"], unit="s", tz="UTC"),
        "open":   float(candle["open"]),
        "high":   float(candle["high"]),
        "low":    float(candle["low"]),
        "close":  float(candle["close"]),
        "volume": 0,   # Deriv does not expose volume for forex/metals
    }
