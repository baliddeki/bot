"""
signal_generator.py — Orchestrates the full signal generation pipeline.

Full flow on each candle close:
  1. Scan MN/W1/D1/H4 for a recently swept swing high or low
  2. Classify the trade type (INTRADAY or SWING)
  3. Find an Order Block on the permitted timeframes for that sweep
  4. Confirm price is currently inside the OB zone
  5. Search for the best FVG inside the OB across LTFs
  6. Calculate entry, SL, and TP
  7. Return a Signal (or None if any step fails)
"""

from dataclasses import dataclass, field
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
    ob: OrderBlock  # The Order Block confirming the setup
    fvg: FVG  # The FVG used for entry
    entry: float
    sl: float
    tp1: Optional[
        float
    ]  # Primary TP (always set for intraday, may be None for swing until structure forms)
    tp2: Optional[float]  # Secondary TP (intraday only; swing uses tp1)
    ob_tf: str  # TF the OB was found on
    fvg_tf: str  # TF the FVG was found on
    rejection_reason: str = ""  # Populated if setup was rejected at any step


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────


def generate_signal(
    candle_data: dict,
    current_time: pd.Timestamp,
) -> Optional[Signal]:
    """
    Run the full signal generation pipeline.

    Args:
        candle_data:  Dict mapping TF label → OHLCV DataFrame (from OANDA).
        current_time: Timestamp of the current candle close.

    Returns:
        A Signal if a valid setup is found, otherwise None.
    """

    # ── Step 1: Scan for a sweep on liquidation timeframes ────────────────
    sweep = _find_sweep(candle_data)
    if sweep is None:
        return None
    direction, swept_tf, swept_swing = sweep

    # ── Step 2: Classify trade type ───────────────────────────────────────
    trade_type = _classify_trade(swept_tf)

    # ── Step 3: Find Order Block on permitted timeframes ──────────────────
    ob = _find_ob(candle_data, swept_tf, direction, swept_swing.time)
    if ob is None:
        return None

    # ── Step 4: Confirm price is inside the OB zone ───────────────────────
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
# Step implementations
# ─────────────────────────────────────────────────────────────────────────────


def _find_sweep(
    candle_data: dict,
) -> Optional[tuple[str, str, Union[SwingHigh, SwingLow]]]:
    """
    Check each liquidation timeframe for a recently swept swing.

    Priority: MN → W1 → D1 → H4 (higher TF sweeps take precedence).

    Returns (direction, swept_tf, swept_swing) or None.
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
    """Return "INTRADAY" or "SWING" based on which TF was swept."""
    if swept_tf in config.INTRADAY_SWEPT_TIMEFRAMES:
        return "INTRADAY"
    return "SWING"


def _find_ob(
    candle_data: dict,
    swept_tf: str,
    direction: str,
    sweep_time: pd.Timestamp,
) -> Optional[OrderBlock]:
    """
    Search permitted OB timeframes for the most recent valid OB
    that formed before the sweep candle.
    """
    permitted_tfs = config.OB_TIMEFRAME_RULES.get(swept_tf, [])

    for tf in permitted_tfs:
        df = candle_data.get(tf)
        if df is None or len(df) < 3:
            continue

        blocks = find_order_blocks(df, tf)
        ob = get_most_recent_ob(blocks, direction, before_time=sweep_time)
        if ob:
            return ob

    return None


def _get_latest_price(candle_data: dict) -> Optional[float]:
    """Get the most recent close price, falling back through TFs."""
    for tf in ["H1", "H4", "D1"]:
        df = candle_data.get(tf)
        if df is not None and not df.empty:
            return float(df.iloc[-1]["close"])
    return None


def _calculate_entry(fvg: FVG, direction: str) -> float:
    """
    Entry at the FVG zone edge closest to current price.
    BUY:  bottom of bullish FVG (C1.high of the FVG pattern)
    SELL: top of bearish FVG (C1.low of the FVG pattern)
    """
    return fvg.gap_high if direction == "BUY" else fvg.gap_low


def _calculate_sl(entry: float, direction: str) -> float:
    """Fixed 100-pip stop loss from entry."""
    sl_distance = config.pips_to_price(config.SL_PIPS)
    return entry - sl_distance if direction == "BUY" else entry + sl_distance


def _calculate_tp(
    entry: float,
    direction: str,
    trade_type: str,
    candle_data: dict,
    swept_tf: str,
) -> tuple[Optional[float], Optional[float]]:
    """
    Calculate TP levels based on trade type.

    Intraday:
        TP1 = entry ± 150 pips (close 50%)
        TP2 = entry ± 250 pips (close remaining 50%)

    Swing:
        TP1 = nearest swing high (BUY) or swing low (SELL) on the swept TF.
        TP2 = None (dynamic — updated as new swings form during trade).
    """
    if trade_type == "INTRADAY":
        tp1_dist = config.pips_to_price(config.INTRADAY_TP1_PIPS)
        tp2_dist = config.pips_to_price(config.INTRADAY_TP2_PIPS)
        if direction == "BUY":
            return entry + tp1_dist, entry + tp2_dist
        else:
            return entry - tp1_dist, entry - tp2_dist

    # Swing TP — nearest qualifying swing on the swept TF
    tp1 = _find_nearest_swing_tp(entry, direction, candle_data, swept_tf)
    return tp1, None


def _find_nearest_swing_tp(
    entry: float,
    direction: str,
    candle_data: dict,
    swept_tf: str,
) -> Optional[float]:
    """
    Find the nearest swing high (BUY) or swing low (SELL) on the swept TF
    that lies beyond the entry price.
    """
    df = candle_data.get(swept_tf)
    if df is None:
        return None

    if direction == "BUY":
        highs = find_swing_highs(df)
        candidates = [h.price for h in highs if h.price > entry]
        return min(candidates) if candidates else None
    else:
        lows = find_swing_lows(df)
        candidates = [l.price for l in lows if l.price < entry]
        return max(candidates) if candidates else None
