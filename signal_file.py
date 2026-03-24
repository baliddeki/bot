"""
Signal Engine
==============
Combines Order Block detection (2H) with FVG scanning (LTF)
to produce trade signals.

Flow:
1. Fetch 2H candles, detect order blocks
2. For each OB, scan Candle C's time window for FVGs on 3M/5M/15M/30M
3. If FVG found, produce a signal with entry at FVG edge
"""

import config
from order_block import detect_order_blocks, filter_recent_obs
from fvg_detector import find_first_fvg_in_window


def scan_for_signal(connection):
    """
    Main signal scanner. Called periodically by the bot.

    Args:
        connection: MT5Connection instance

    Returns:
        Signal dict or None:
        {
            "action": "BUY" or "SELL",
            "entry": float,       # Limit order price
            "sl": float,          # Stop loss price
            "tp1": float,         # First take profit (80% close)
            "tp2": float,         # Second take profit (20% runner)
            "ob": dict,           # The order block that triggered this
            "fvg": dict,          # The FVG used for entry
            "reason": str,        # Human-readable reason
        }
    """
    # Step 1: Get 2H candles
    candles_2h = connection.get_candles(
        config.OB_TIMEFRAME, count=config.OB_LOOKBACK_CANDLES
    )
    if candles_2h is None or len(candles_2h) < 3:
        return None

    # Step 2: Detect order blocks
    obs = detect_order_blocks(candles_2h)
    obs = filter_recent_obs(obs)

    if not obs:
        return None

    # Step 3: For each OB (newest first), try to find an FVG
    for ob in obs:
        signal = _process_order_block(connection, ob)
        if signal:
            return signal

    return None


def _process_order_block(connection, ob):
    """
    Given an order block, scan for FVGs in Candle C's window.
    Returns a signal dict or None.
    """
    direction = ob["type"]  # "bullish" or "bearish"
    start_time = ob["candle_c_time"]
    end_time = ob["candle_c_end"]

    # Find FVG on lower timeframes
    fvg = find_first_fvg_in_window(connection, direction, start_time, end_time)
    if fvg is None:
        return None

    # Build signal
    sl_points = config.pips_to_points(config.SL_PIPS)
    tp1_points = config.pips_to_points(config.TP1_PIPS)
    tp2_points = config.pips_to_points(config.TP2_PIPS)

    if direction == "bullish":
        # Buy limit at top of bullish FVG (price retraces down to here)
        entry = fvg["zone_top"]
        sl = entry - sl_points
        tp1 = entry + tp1_points
        tp2 = entry + tp2_points
        action = "BUY"
    else:
        # Sell limit at bottom of bearish FVG (price retraces up to here)
        entry = fvg["zone_bottom"]
        sl = entry + sl_points
        tp1 = entry - tp1_points
        tp2 = entry - tp2_points
        action = "SELL"

    tf_label = fvg.get("timeframe", "?")

    return {
        "action": action,
        "entry": round(entry, 2),
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "ob": ob,
        "fvg": fvg,
        "reason": f"{direction.upper()} OB on 2H + FVG on {tf_label}",
    }


def format_signal(signal):
    """Pretty-print a signal for logging."""
    if signal is None:
        return "No signal"

    lines = [
        f"Signal: {signal['action']}",
        f"  Entry:  {signal['entry']}",
        f"  SL:     {signal['sl']}",
        f"  TP1:    {signal['tp1']} (close {config.TP1_CLOSE_PERCENT}%)",
        f"  TP2:    {signal['tp2']} (remaining {100 - config.TP1_CLOSE_PERCENT}%)",
        f"  Reason: {signal['reason']}",
    ]
    return "\n".join(lines)
