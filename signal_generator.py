"""
signal_generator.py — Orchestrates the full signal generation pipeline.

Strategy flow (Gold XAU/USD only):

  1. Scan MN / W1 / D1 / H4 for a liquidation (wick sweep) of a swing
     high or swing low on those timeframes.

  2. Classify trade type:
       H4 sweep  → INTRADAY  (quick 150/250-pip target)
       D1/W1/MN  → SWING     (exit at key swing structure)

  3. After the sweep, look for an Order Block that formed on a lower TF
     as price reversed away from the swept level.

       Swept TF    | Valid OB timeframes
       ------------|--------------------
       MN          | W1, D1
       W1          | W1, D1
       D1          | D1, H4, H2
       H4          | H4, H2, H1

  4. Confirm current price is inside the OB zone.

  5. Find the best FVG inside the OB (scanning M3 → D1):
       BUY  → lowest  gap_low  FVG (deepest support)
       SELL → highest gap_high FVG (deepest resistance)

  6. Entry = C3 wick of the FVG (limit order, never cancelled):
       BUY  → fvg.gap_high  (C3.low  of bullish FVG)
       SELL → fvg.gap_low   (C3.high of bearish FVG)

  7. SL = 100 pips from entry.

  8. TP:
       INTRADAY → TP1 = 150 pips (50% close), TP2 = 250 pips (full close)
       SWING    → Nearest swing high (BUY) or swing low (SELL) on swept TF
"""

from dataclasses import dataclass
from typing import Optional, Union
import pandas as pd

import config
from swing_detector import (
    SwingHigh,
    SwingLow,
    find_swing_highs,
    find_swing_lows,
    find_recent_sweep,
)
from ob_detector import (
    OrderBlock,
    find_order_blocks,
    get_most_recent_ob,
    price_inside_ob,
)
from fvg_detector import FVG, search_fvg_across_timeframes

# ─────────────────────────────────────────────────────────────────────────────
# Signal data class
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Signal:
    direction: str  # "BUY" or "SELL"
    trade_type: str  # "INTRADAY" or "SWING"
    swept_tf: str  # TF whose swing was swept
    swept_swing: Union[SwingHigh, SwingLow]  # The swing that was swept
    ob: OrderBlock
    fvg: FVG
    entry: float  # Limit order at C3 wick of FVG
    sl: float
    tp1: Optional[float]
    tp2: Optional[float]  # Intraday only
    ob_tf: str
    fvg_tf: str
    rejection_reason: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────


def generate_signal(
    candle_data: dict,
    current_time: pd.Timestamp,
) -> Optional[Signal]:
    """
    Run the full signal generation pipeline.
    Returns a Signal if all steps pass, otherwise None.
    """

    # ── Step 1: Find a sweep ──────────────────────────────────────────────
    sweep = _find_sweep(candle_data)
    if sweep is None:
        return None
    direction, swept_tf, swept_swing = sweep

    # ── Step 2: Classify trade type ───────────────────────────────────────
    trade_type = _classify_trade(swept_tf)

    # ── Step 3: Find OB that formed AFTER the sweep ───────────────────────
    # After the sweep price reverses. We look for an OB that formed during
    # or after that reversal — NOT one that pre-dated the sweep.
    ob = _find_ob_after_sweep(candle_data, swept_tf, direction, swept_swing.time)
    if ob is None:
        return None

    # ── Step 4: Confirm price is currently inside the OB zone ─────────────
    current_price = _get_latest_price(candle_data)
    if current_price is None or not price_inside_ob(current_price, ob):
        return None

    # ── Step 5: Find FVG within the OB ───────────────────────────────────
    fvg = search_fvg_across_timeframes(
        candle_data=candle_data,
        ob_low=ob.zone_low,
        ob_high=ob.zone_high,
        direction=direction,
        search_order=config.FVG_SEARCH_ORDER,
    )
    if fvg is None:
        return None

    # ── Step 6: Calculate entry, SL, TP ───────────────────────────────────
    entry = _calculate_entry(fvg, direction)
    sl = _calculate_sl(entry, direction)
    tp1, tp2 = _calculate_tp(entry, direction, trade_type, candle_data, swept_tf)

    return Signal(
        direction=direction,
        trade_type=trade_type,
        swept_tf=swept_tf,
        swept_swing=swept_swing,
        ob=ob,
        fvg=fvg,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        ob_tf=ob.timeframe,
        fvg_tf=fvg.timeframe,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline steps
# ─────────────────────────────────────────────────────────────────────────────


def _find_sweep(
    candle_data: dict,
) -> Optional[tuple[str, str, Union[SwingHigh, SwingLow]]]:
    """
    Scan liquidation TFs from highest to lowest priority.
    MN → W1 → D1 → H4 (higher TF sweep takes precedence).
    Returns (direction, swept_tf, swing) or None.
    """
    for tf in config.LIQUIDATION_TIMEFRAMES:
        df = candle_data.get(tf)
        min_candles = {"MN": 4, "W1": 6}.get(tf, 10)
        if df is None or len(df) < min_candles:
            continue

        result = find_recent_sweep(df, lookback=config.SWEEP_RECENCY_CANDLES)
        if result:
            direction, swing = result
            return (direction, tf, swing)

    return None


def _classify_trade(swept_tf: str) -> str:
    return "INTRADAY" if swept_tf in config.INTRADAY_SWEPT_TIMEFRAMES else "SWING"


def _find_ob_after_sweep(
    candle_data: dict,
    swept_tf: str,
    direction: str,
    sweep_time: pd.Timestamp,
) -> Optional[OrderBlock]:
    """
    Find the most recent OB on a permitted timeframe that formed AFTER
    the sweep candle.

    After a swing is liquidated, price reverses. During that reversal
    an OB forms on a lower TF. We want the most recent such OB because
    it is the freshest and most relevant supply/demand zone.

    Permitted TFs per swept level:
      MN  → W1, D1
      W1  → W1, D1
      D1  → D1, H4, H2
      H4  → H4, H2, H1
    """
    permitted_tfs = config.OB_TIMEFRAME_RULES.get(swept_tf, [])

    for tf in permitted_tfs:
        df = candle_data.get(tf)
        if df is None or len(df) < 3:
            continue

        blocks = find_order_blocks(df, tf)

        # Look for the most recent OB that formed AFTER the sweep time
        ob = get_most_recent_ob(blocks, direction, after_time=sweep_time)
        if ob:
            return ob

    return None


def _get_latest_price(candle_data: dict) -> Optional[float]:
    for tf in ["H1", "H4", "D1"]:
        df = candle_data.get(tf)
        if df is not None and not df.empty:
            return float(df.iloc[-1]["close"])
    return None


def _calculate_entry(fvg: FVG, direction: str) -> float:
    """
    Limit order placed at the C3 wick of the FVG — the first level that
    retracing price would touch when returning into the FVG zone.

    Bullish FVG (BUY):  fvg.gap_high = C3.low  (bottom wick of C3)
    Bearish FVG (SELL): fvg.gap_low  = C3.high (top wick of C3)

    These orders are never cancelled — they sit until filled.
    """
    return fvg.gap_high if direction == "BUY" else fvg.gap_low


def _calculate_sl(entry: float, direction: str) -> float:
    """Fixed 100-pip SL from entry."""
    dist = config.pips_to_price(config.SL_PIPS)
    return entry - dist if direction == "BUY" else entry + dist


def _calculate_tp(
    entry: float,
    direction: str,
    trade_type: str,
    candle_data: dict,
    swept_tf: str,
) -> tuple[Optional[float], Optional[float]]:
    """
    INTRADAY: TP1 = 150 pips (50% close), TP2 = 250 pips (full close).
    SWING:    TP1 = nearest swing high/low on swept TF. TP2 = None.
    """
    if trade_type == "INTRADAY":
        tp1_dist = config.pips_to_price(config.INTRADAY_TP1_PIPS)
        tp2_dist = config.pips_to_price(config.INTRADAY_TP2_PIPS)
        if direction == "BUY":
            return entry + tp1_dist, entry + tp2_dist
        else:
            return entry - tp1_dist, entry - tp2_dist

    return _find_nearest_swing_tp(entry, direction, candle_data, swept_tf), None


def _find_nearest_swing_tp(
    entry: float,
    direction: str,
    candle_data: dict,
    swept_tf: str,
) -> Optional[float]:
    """Nearest swing high (BUY) or swing low (SELL) beyond entry on swept TF."""
    df = candle_data.get(swept_tf)
    if df is None:
        return None

    if direction == "BUY":
        candidates = [h.price for h in find_swing_highs(df) if h.price > entry]
        return min(candidates) if candidates else None
    else:
        candidates = [l.price for l in find_swing_lows(df) if l.price < entry]
        return max(candidates) if candidates else None
