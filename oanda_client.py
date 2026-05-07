"""
oanda_client.py — OANDA REST v20 API client for market data.

This is the SOLE data source for the bot.
MT5 is used only for trade execution — never for data.

All candles are fetched as mid-price (average of bid and ask),
which is the most broker-agnostic representation of price.
"""

import os
from typing import Optional
from datetime import datetime

import pandas as pd
import oandapyV20
import oandapyV20.endpoints.instruments as instruments
from oandapyV20.contrib.factories import InstrumentsCandlesFactory

import config
from logger import log_event


# ─────────────────────────────────────────────────────────────────────────────
# Client factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_client() -> oandapyV20.API:
    """Create an authenticated OANDA API client."""
    api_key = config.OANDA_API_KEY or os.getenv("OANDA_API_KEY", "")
    if not api_key:
        raise ValueError(
            "OANDA API key not found. "
            "Set OANDA_API_KEY in config.py or as an environment variable."
        )
    return oandapyV20.API(
        access_token=api_key,
        environment=config.OANDA_ENVIRONMENT,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Candle fetching
# ─────────────────────────────────────────────────────────────────────────────

def fetch_candles(
    instrument:  str,
    granularity: str,
    count:       int = 500,
    date_from:   Optional[datetime] = None,
    date_to:     Optional[datetime] = None,
    price:       str = "M",   # "M" = mid (recommended), "B" = bid, "A" = ask
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV candle data from OANDA.

    Args:
        instrument:  OANDA instrument code, e.g. "XAU_USD".
        granularity: OANDA granularity string, e.g. "H1", "M15", "D".
        count:       Number of recent candles (used when date_from is None).
        date_from:   Start of date range (UTC). If provided, fetches a range.
        date_to:     End of date range (UTC). Defaults to now if not provided.
        price:       Price type — use "M" for mid-price candles.

    Returns:
        DataFrame with columns [time, open, high, low, close, volume],
        sorted oldest → newest. Returns None on failure.
    """
    try:
        client = _build_client()

        if date_from:
            rows = _fetch_range(client, instrument, granularity, date_from, date_to, price)
        else:
            rows = _fetch_recent(client, instrument, granularity, count, price)

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
        return df

    except Exception as e:
        log_event(f"OANDA fetch error [{instrument} {granularity}]: {e}", level="ERROR")
        return None


def fetch_all_timeframes(
    instrument: str = config.SYMBOL,
    date_from:  Optional[datetime] = None,
    date_to:    Optional[datetime] = None,
) -> dict:
    """
    Fetch candles for every timeframe defined in config.TIMEFRAMES.

    Returns a dict mapping TF label → DataFrame (or None if the fetch failed).
    """
    data = {}

    for tf_label, oanda_gran in config.TIMEFRAMES.items():
        count = config.CANDLE_HISTORY.get(tf_label, 500)
        df = fetch_candles(
            instrument  = instrument,
            granularity = oanda_gran,
            count       = count,
            date_from   = date_from,
            date_to     = date_to,
        )
        data[tf_label] = df

        status = f"{len(df)} candles" if df is not None else "FAILED"
        log_event(f"  [{tf_label}] {status}")

    return data


# ─────────────────────────────────────────────────────────────────────────────
# Internal fetch helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_recent(
    client:      oandapyV20.API,
    instrument:  str,
    granularity: str,
    count:       int,
    price:       str,
) -> list[dict]:
    """Fetch the most recent N complete candles."""
    params = {"granularity": granularity, "price": price, "count": count}
    req = instruments.InstrumentsCandles(instrument=instrument, params=params)
    client.request(req)
    return [
        _parse_candle(c, price)
        for c in req.response.get("candles", [])
        if c.get("complete")
    ]


def _fetch_range(
    client:      oandapyV20.API,
    instrument:  str,
    granularity: str,
    date_from:   datetime,
    date_to:     Optional[datetime],
    price:       str,
) -> list[dict]:
    """Fetch a date-range of candles, auto-paginating for large requests."""
    params = {
        "granularity": granularity,
        "price":       price,
        "from":        date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if date_to:
        params["to"] = date_to.strftime("%Y-%m-%dT%H:%M:%SZ")

    rows = []
    for req in InstrumentsCandlesFactory(instrument=instrument, params=params):
        client.request(req)
        for candle in req.response.get("candles", []):
            if candle.get("complete"):
                rows.append(_parse_candle(candle, price))
    return rows


def _parse_candle(candle: dict, price: str) -> dict:
    """Parse an OANDA candle dict into a flat row dict."""
    price_key = {"M": "mid", "B": "bid", "A": "ask"}.get(price, "mid")
    ohlc = candle[price_key]
    return {
        "time":   candle["time"],
        "open":   float(ohlc["o"]),
        "high":   float(ohlc["h"]),
        "low":    float(ohlc["l"]),
        "close":  float(ohlc["c"]),
        "volume": int(candle.get("volume", 0)),
    }
