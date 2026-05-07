"""
fvg_detector.py — Fair Value Gap (FVG) detection within Order Block zones.

Definitions (strict 3-candle patterns):

  Bullish FVG:
    C1 Bearish → C2 Bullish → C3 Bullish
    Condition: C3.low > C1.high
    Gap zone: [C1.high, C3.low]

  Bearish FVG:
    C1 Bullish → C2 Bearish → C3 Bearish
    Condition: C3.high < C1.low
    Gap zone: [C3.high, C1.low]

  A FVG is only valid if its gap zone falls within the OB zone [ob_low, ob_high].

Selection rules when multiple FVGs exist within the same OB:
  BUY  → select the FVG with the LOWEST  gap_low  (deepest into the zone)
  SELL → select the FVG with the HIGHEST gap_high (deepest into the zone)
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# Data class
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FVG:
    direction:  str             # "BUY" or "SELL"
    gap_low:    float           # Lower boundary of the gap
    gap_high:   float           # Upper boundary of the gap
    time:       pd.Timestamp    # C3 candle time
    index:      int             # C3 row index in its DataFrame
    timeframe:  str


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

def _check_bullish_fvg(
    c1: pd.Series,
    c2: pd.Series,
    c3: pd.Series,
    c3_index: int,
    timeframe: str,
    ob_low: float,
    ob_high: float,
) -> Optional[FVG]:
    """
    Bullish FVG: C1 bearish, C2 bullish, C3 bullish, C3.low > C1.high.
    Gap zone must fall within [ob_low, ob_high].
    """
    if not (_is_bearish(c1) and _is_bullish(c2) and _is_bullish(c3)):
        return None

    gap_low  = float(c1["high"])
    gap_high = float(c3["low"])

    if gap_low >= gap_high:
        return None  # No gap

    if gap_low < ob_low or gap_high > ob_high:
        return None  # Gap falls outside OB zone

    return FVG(
        direction = "BUY",
        gap_low   = gap_low,
        gap_high  = gap_high,
        time      = c3["time"],
        index     = c3_index,
        timeframe = timeframe,
    )


def _check_bearish_fvg(
    c1: pd.Series,
    c2: pd.Series,
    c3: pd.Series,
    c3_index: int,
    timeframe: str,
    ob_low: float,
    ob_high: float,
) -> Optional[FVG]:
    """
    Bearish FVG: C1 bullish, C2 bearish, C3 bearish, C3.high < C1.low.
    Gap zone must fall within [ob_low, ob_high].
    """
    if not (_is_bullish(c1) and _is_bearish(c2) and _is_bearish(c3)):
        return None

    gap_low  = float(c3["high"])
    gap_high = float(c1["low"])

    if gap_low >= gap_high:
        return None  # No gap

    if gap_low < ob_low or gap_high > ob_high:
        return None  # Gap falls outside OB zone

    return FVG(
        direction = "SELL",
        gap_low   = gap_low,
        gap_high  = gap_high,
        time      = c3["time"],
        index     = c3_index,
        timeframe = timeframe,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def find_fvgs_in_ob(
    df: pd.DataFrame,
    timeframe: str,
    ob_low: float,
    ob_high: float,
    direction: str,
) -> list[FVG]:
    """
    Find all FVGs within an OB zone on a single timeframe.

    Args:
        df:        OHLCV DataFrame for this timeframe.
        timeframe: Label for this timeframe (used for attribution).
        ob_low:    OB zone lower boundary.
        ob_high:   OB zone upper boundary.
        direction: "BUY" to find bullish FVGs, "SELL" for bearish.
    """
    results = []

    for i in range(2, len(df)):
        c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]

        if direction == "BUY":
            fvg = _check_bullish_fvg(c1, c2, c3, i, timeframe, ob_low, ob_high)
        else:
            fvg = _check_bearish_fvg(c1, c2, c3, i, timeframe, ob_low, ob_high)

        if fvg:
            results.append(fvg)

    return results


def select_best_fvg(fvgs: list[FVG], direction: str) -> Optional[FVG]:
    """
    Select the best FVG from a list based on direction.

    BUY:  lowest gap_low  (deepest / most conservative entry)
    SELL: highest gap_high (deepest / most conservative entry)
    """
    if not fvgs:
        return None
    if direction == "BUY":
        return min(fvgs, key=lambda f: f.gap_low)
    else:
        return max(fvgs, key=lambda f: f.gap_high)


def search_fvg_across_timeframes(
    candle_data:  dict,
    ob_low:       float,
    ob_high:      float,
    direction:    str,
    search_order: list[str],
) -> Optional[FVG]:
    """
    Search for the best qualifying FVG across multiple timeframes,
    scanning from the lowest TF upward.

    Collects all valid FVGs across all TFs and then applies
    the selection rule to pick the single best one.

    Args:
        candle_data:  Dict of {tf_label: DataFrame}.
        ob_low:       OB zone lower boundary.
        ob_high:      OB zone upper boundary.
        direction:    "BUY" or "SELL".
        search_order: Timeframe labels in ascending order (lowest → highest).

    Returns:
        The best FVG, or None if none found.
    """
    all_fvgs = []

    for tf in search_order:
        df = candle_data.get(tf)
        if df is None or len(df) < 3:
            continue
        fvgs = find_fvgs_in_ob(df, tf, ob_low, ob_high, direction)
        all_fvgs.extend(fvgs)

    return select_best_fvg(all_fvgs, direction)
