"""
deriv_client.py — Deriv WebSocket API client for market data.

Uses the new Deriv API (api.derivws.com) public endpoint.
No authentication or App ID required for market data.

Pagination:
  Deriv returns a limited number of candles per request (~1000).
  _fetch_range() paginates automatically until the full date range
  is covered — there is no cap on total candles returned.

Supported granularities (seconds):
  60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400

W1 and MN are not native — resampled from D1 automatically.
"""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import websockets

import config
from logger import log_event

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
_ALLOWED_GRANULARITIES = {
    60,
    120,
    180,
    300,
    600,
    900,
    1800,
    3600,
    7200,
    14400,
    28800,
    86400,
}

# Deriv returns at most this many candles per request — used to detect truncation
_DERIV_PAGE_LIMIT = 1000

# Native TFs: label → granularity in seconds (None = resampled)
_NATIVE_TIMEFRAMES = {
    tf: gran for tf, gran in config.TIMEFRAMES.items() if gran is not None
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def fetch_candles(
    symbol: str,
    granularity: int,
    count: int = 500,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candles from Deriv's public WebSocket endpoint.

    When date_from is provided, paginates automatically to retrieve
    the complete date range regardless of Deriv's per-request limit.

    Args:
        symbol:      Deriv symbol, e.g. "frxXAUUSD".
        granularity: Candle size in seconds.
        count:       Recent candle count (only used when date_from is None).
        date_from:   Start of date range (UTC).
        date_to:     End of date range (UTC). Defaults to now.

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
            rows = _run(_fetch_range_paginated(symbol, granularity, date_from, date_to))
        else:
            rows = _run(_fetch_recent(symbol, granularity, count))

        if not rows:
            return None

        df = pd.DataFrame(
            rows, columns=["time", "open", "high", "low", "close", "volume"]
        )
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = (
            df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
        )
        return df

    except Exception as exc:
        log_event(f"Deriv fetch error [{symbol} {granularity}s]: {exc}", level="ERROR")
        return None


def fetch_all_timeframes(
    symbol: str = config.SYMBOL,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> dict:
    """
    Fetch candles for every timeframe in config.TIMEFRAMES.
    W1 and MN are resampled from D1 automatically.

    Returns: dict mapping TF label → DataFrame (or None if failed).
    """
    data: dict = {}

    for tf, granularity in _NATIVE_TIMEFRAMES.items():
        count = config.CANDLE_HISTORY.get(tf, 500)
        df = fetch_candles(
            symbol=symbol,
            granularity=granularity,
            count=count,
            date_from=date_from,
            date_to=date_to,
        )
        data[tf] = df
        status = f"{len(df)} candles" if df is not None else "FAILED"
        log_event(f"  [{tf:3s}] {status}")

    # ── Resample W1 and MN from D1 ────────────────────────────────────────
    d1_df = data.get("D1")

    if d1_df is not None:
        data["W1"] = _resample(d1_df, "W", config.CANDLE_HISTORY.get("W1", 52))
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
    """Ping the Deriv public WebSocket. Returns True if reachable."""
    try:
        return _run(_ws_request({"ping": 1})).get("ping") == "pong"
    except Exception as exc:
        log_event(f"Deriv ping failed: {exc}", level="ERROR")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Resampling  (D1 → W1 / MN)
# ─────────────────────────────────────────────────────────────────────────────


def _resample(df_daily: pd.DataFrame, freq: str, target_count: int) -> pd.DataFrame:
    """Resample daily OHLCV to a lower frequency, keeping the most recent N periods."""
    df = df_daily.copy().set_index("time")
    resampled = (
        df.resample(freq)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna()
    )
    if len(resampled) > 1:
        resampled = resampled.iloc[:-1]  # Drop current incomplete period
    return resampled.tail(target_count).reset_index()


# ─────────────────────────────────────────────────────────────────────────────
# Paginated range fetch
# ─────────────────────────────────────────────────────────────────────────────


async def _fetch_range_paginated(
    symbol: str,
    granularity: int,
    date_from: datetime,
    date_to: Optional[datetime],
) -> list[dict]:
    """
    Fetch candles for a date range, paginating until the full range is covered.

    Deriv returns at most ~1000 candles per request. This function keeps
    fetching from where the last batch ended until it reaches date_to.
    """
    end_epoch = (
        int(date_to.replace(tzinfo=timezone.utc).timestamp())
        if date_to
        else int(datetime.now(timezone.utc).timestamp())
    )

    current_start = int(date_from.replace(tzinfo=timezone.utc).timestamp())
    all_rows: list[dict] = []
    page = 0

    while current_start < end_epoch:
        page += 1
        payload = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "start": current_start,
            "end": end_epoch,
            "granularity": granularity,
            "style": "candles",
        }

        try:
            response = await _ws_request(payload)
        except Exception as exc:
            log_event(f"Deriv pagination error (page {page}): {exc}", level="WARNING")
            break

        candles = response.get("candles", [])
        if not candles:
            break

        # Drop the last candle on final page — it may be the live (incomplete) candle
        # Keep it on intermediate pages since it's a complete boundary candle
        is_last_page = len(candles) < _DERIV_PAGE_LIMIT
        if is_last_page and candles:
            candles = candles[:-1]

        if not candles:
            break

        all_rows.extend(_parse_candle(c) for c in candles)

        if is_last_page:
            break  # We've covered the full range

        # Advance start to one granularity step beyond the last received candle
        last_epoch = candles[-1]["epoch"]
        current_start = last_epoch + granularity

    return all_rows


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket helpers
# ─────────────────────────────────────────────────────────────────────────────


def _run(coro) -> list | dict:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


async def _ws_request(payload: dict) -> dict:
    """Send one request to the public WebSocket endpoint and return the response."""
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
        "count": count + 1,
        "end": "latest",
        "granularity": granularity,
        "style": "candles",
    }
    response = await _ws_request(payload)
    candles = response.get("candles", [])
    if candles:
        candles = candles[:-1]  # Drop the live (incomplete) candle
    return [_parse_candle(c) for c in candles]


def _parse_candle(candle: dict) -> dict:
    """Parse a Deriv candle dict into a flat row dict."""
    return {
        "time": pd.Timestamp(candle["epoch"], unit="s", tz="UTC"),
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": 0,
    }
