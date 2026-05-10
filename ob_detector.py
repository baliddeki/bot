"""
ob_detector.py — Order Block detection.

Definitions (strict 3-candle patterns):

  Bullish OB:
    C1 Bullish → C2 Bearish → C3 Bullish
    Condition 1 (Swing Low embedded): C2.low  < C3.low
    Condition 2 (Sweep of C1):        C3.high > C1.high
    OB Zone: [C2.low, C2.high]

  Bearish OB:
    C1 Bearish → C2 Bullish → C3 Bearish
    Condition 1 (Swing High embedded): C2.high > C3.high
    Condition 2 (Sweep of C1):         C3.low  < C1.low
    OB Zone: [C2.low, C2.high]

The OB is valid for entry when price returns into the OB zone after the sweep.
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Data class
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class OrderBlock:
    direction: str  # "BUY" or "SELL"
    zone_low: float  # C2 low
    zone_high: float  # C2 high
    time: pd.Timestamp  # C2 candle time
    index: int  # C2 row index in its DataFrame
    timeframe: str  # TF label this OB was identified on
    c1_time: pd.Timestamp
    c3_time: pd.Timestamp


# ─────────────────────────────────────────────────────────────────────────────
# Candle direction helpers
# ─────────────────────────────────────────────────────────────────────────────


def _is_bullish(c: pd.Series) -> bool:
    return float(c["close"]) > float(c["open"])


def _is_bearish(c: pd.Series) -> bool:
    return float(c["close"]) < float(c["open"])


# ─────────────────────────────────────────────────────────────────────────────
# Pattern checks
# ─────────────────────────────────────────────────────────────────────────────


def _check_bullish_ob(
    c1: pd.Series,
    c2: pd.Series,
    c3: pd.Series,
    c2_index: int,
    timeframe: str,
) -> Optional[OrderBlock]:
    """
    Bullish OB: C1 bullish, C2 bearish, C3 bullish.
    C2.low < C3.low  (swing low condition).
    C3.high > C1.high (sweep condition).
    """
    if not (_is_bullish(c1) and _is_bearish(c2) and _is_bullish(c3)):
        return None
    if float(c2["low"]) >= float(c3["low"]):
        return None
    if float(c3["high"]) <= float(c1["high"]):
        return None

    return OrderBlock(
        direction="BUY",
        zone_low=float(c2["low"]),
        zone_high=float(c2["high"]),
        time=c2["time"],
        index=c2_index,
        timeframe=timeframe,
        c1_time=c1["time"],
        c3_time=c3["time"],
    )


def _check_bearish_ob(
    c1: pd.Series,
    c2: pd.Series,
    c3: pd.Series,
    c2_index: int,
    timeframe: str,
) -> Optional[OrderBlock]:
    """
    Bearish OB: C1 bearish, C2 bullish, C3 bearish.
    C2.high > C3.high (swing high condition).
    C3.low  < C1.low  (sweep condition).
    """
    if not (_is_bearish(c1) and _is_bullish(c2) and _is_bearish(c3)):
        return None
    if float(c2["high"]) <= float(c3["high"]):
        return None
    if float(c3["low"]) >= float(c1["low"]):
        return None

    return OrderBlock(
        direction="SELL",
        zone_low=float(c2["low"]),
        zone_high=float(c2["high"]),
        time=c2["time"],
        index=c2_index,
        timeframe=timeframe,
        c1_time=c1["time"],
        c3_time=c3["time"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────


def find_order_blocks(df: pd.DataFrame, timeframe: str) -> list[OrderBlock]:
    """
    Scan a DataFrame and return all valid order blocks (both directions).
    Results are ordered oldest → newest.
    """
    blocks = []

    for i in range(1, len(df) - 1):
        c1, c2, c3 = df.iloc[i - 1], df.iloc[i], df.iloc[i + 1]

        bull_ob = _check_bullish_ob(c1, c2, c3, i, timeframe)
        if bull_ob:
            blocks.append(bull_ob)

        bear_ob = _check_bearish_ob(c1, c2, c3, i, timeframe)
        if bear_ob:
            blocks.append(bear_ob)

    return blocks


def get_most_recent_ob(
    blocks: list[OrderBlock],
    direction: str,
    before_time: Optional[pd.Timestamp] = None,
) -> Optional[OrderBlock]:
    """
    Return the most recent OB matching the given direction.

    Args:
        blocks:      List of OrderBlock objects to search.
        direction:   "BUY" or "SELL".
        before_time: If provided, only consider OBs that formed before this time.
    """
    matching = [b for b in blocks if b.direction == direction and (before_time is None or b.time < before_time)]
    return matching[-1] if matching else None


def price_inside_ob(price: float, ob: OrderBlock) -> bool:
    """Return True if the given price is within the OB zone (inclusive)."""
    return ob.zone_low <= price <= ob.zone_high
