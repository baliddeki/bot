"""
swing_detector.py — Swing high and swing low detection.

Definitions (strict 3-candle patterns):

  Swing High:
    C1 Bearish → C2 Bullish → C3 Bearish
    Condition: C2.high > C3.high
    Swing price = C2.high

  Swing Low:
    C1 Bullish → C2 Bearish → C3 Bullish
    Condition: C2.low < C3.low
    Swing price = C2.low

Candle direction is part of the definition.
A wick-only high on a bearish candle does NOT qualify as a swing high.
"""

from dataclasses import dataclass, field
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SwingHigh:
    price:  float           # C2 high — the swing high price
    time:   pd.Timestamp    # C2 candle time
    index:  int             # C2 row index in its DataFrame
    swept:  bool = False    # Marked True once a candle wick passes above this price


@dataclass
class SwingLow:
    price:  float           # C2 low — the swing low price
    time:   pd.Timestamp    # C2 candle time
    index:  int             # C2 row index in its DataFrame
    swept:  bool = False    # Marked True once a candle wick passes below this price


# ─────────────────────────────────────────────────────────────────────────────
# Candle direction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_bullish(candle: pd.Series) -> bool:
    return float(candle["close"]) > float(candle["open"])


def _is_bearish(candle: pd.Series) -> bool:
    return float(candle["close"]) < float(candle["open"])


# ─────────────────────────────────────────────────────────────────────────────
# Swing detection
# ─────────────────────────────────────────────────────────────────────────────

def find_swing_highs(df: pd.DataFrame) -> list[SwingHigh]:
    """
    Scan a DataFrame and return all swing highs found.

    Pattern: C1 bearish, C2 bullish, C3 bearish, C2.high > C3.high.
    Results are ordered oldest → newest.
    """
    highs = []

    for i in range(1, len(df) - 1):
        c1, c2, c3 = df.iloc[i - 1], df.iloc[i], df.iloc[i + 1]

        pattern_matches = (
            _is_bearish(c1)
            and _is_bullish(c2)
            and _is_bearish(c3)
            and float(c2["high"]) > float(c3["high"])
        )

        if pattern_matches:
            highs.append(SwingHigh(
                price = float(c2["high"]),
                time  = c2["time"],
                index = i,
            ))

    return highs


def find_swing_lows(df: pd.DataFrame) -> list[SwingLow]:
    """
    Scan a DataFrame and return all swing lows found.

    Pattern: C1 bullish, C2 bearish, C3 bullish, C2.low < C3.low.
    Results are ordered oldest → newest.
    """
    lows = []

    for i in range(1, len(df) - 1):
        c1, c2, c3 = df.iloc[i - 1], df.iloc[i], df.iloc[i + 1]

        pattern_matches = (
            _is_bullish(c1)
            and _is_bearish(c2)
            and _is_bullish(c3)
            and float(c2["low"]) < float(c3["low"])
        )

        if pattern_matches:
            lows.append(SwingLow(
                price = float(c2["low"]),
                time  = c2["time"],
                index = i,
            ))

    return lows


# ─────────────────────────────────────────────────────────────────────────────
# Sweep checks  (wick only — no close required)
# ─────────────────────────────────────────────────────────────────────────────

def is_swept_high(swing: SwingHigh, candle: pd.Series) -> bool:
    """Return True if the candle's wick passes above the swing high price."""
    return float(candle["high"]) > swing.price


def is_swept_low(swing: SwingLow, candle: pd.Series) -> bool:
    """Return True if the candle's wick passes below the swing low price."""
    return float(candle["low"]) < swing.price


def find_recent_sweep(
    df: pd.DataFrame,
    lookback: int = 5,
) -> tuple[str, object] | None:
    """
    Check the most recent `lookback` candles for any swing sweep.

    Scans for swing patterns in the full DataFrame, then checks whether
    any of the last `lookback` candles swept those swings.

    Returns:
        ("BUY",  SwingLow)  if a swing low was swept  → look for long setup
        ("SELL", SwingHigh) if a swing high was swept → look for short setup
        None if no sweep detected.
    """
    if len(df) < lookback + 3:
        return None

    recent_candles = df.tail(lookback)

    # Check swing lows (swept low → BUY direction)
    for swing in reversed(find_swing_lows(df)):
        for _, candle in recent_candles.iterrows():
            if is_swept_low(swing, candle):
                return ("BUY", swing)

    # Check swing highs (swept high → SELL direction)
    for swing in reversed(find_swing_highs(df)):
        for _, candle in recent_candles.iterrows():
            if is_swept_high(swing, candle):
                return ("SELL", swing)

    return None
