"""
Order Block Detector (2H Timeframe)
====================================
Detects 3-candle order block patterns:

Bullish OB:
  Candle A = Bullish
  Candle B = Bearish
  Candle C = Bullish, C.close >= B.high  (body engulfs B up to upper wick)

Bearish OB:
  Candle A = Bearish
  Candle B = Bullish
  Candle C = Bearish, C.open >= B.high AND C.close <= B.low  (body wraps entire B)
"""

import config


def is_bullish(candle):
    return candle["close"] > candle["open"]


def is_bearish(candle):
    return candle["close"] < candle["open"]


def detect_order_blocks(candles_df):
    """
    Scan 2H candles for order block patterns.

    Args:
        candles_df: DataFrame with columns [time, open, high, low, close]

    Returns:
        List of dicts, each representing an order block:
        {
            "type": "bullish" or "bearish",
            "candle_a": {...},
            "candle_b": {...},     # The middle candle (defines the OB zone)
            "candle_c": {...},     # The engulfing candle
            "ob_high": float,     # Top of OB zone
            "ob_low": float,      # Bottom of OB zone
            "candle_c_time": datetime,   # Start time of candle C
            "candle_c_end": datetime,    # End time of candle C (start + 2 hours)
        }
    """
    if candles_df is None or len(candles_df) < 3:
        return []

    order_blocks = []
    rows = candles_df.to_dict("records")

    for i in range(len(rows) - 2):
        a = rows[i]
        b = rows[i + 1]
        c = rows[i + 2]

        # --- BULLISH ORDER BLOCK ---
        # A = bullish, B = bearish, C = bullish with C.close >= B.high
        if is_bullish(a) and is_bearish(b) and is_bullish(c):
            if c["close"] >= b["high"]:
                ob = _build_ob("bullish", a, b, c)
                order_blocks.append(ob)

        # --- BEARISH ORDER BLOCK ---
        # A = bearish, B = bullish, C = bearish with C.open >= B.high AND C.close <= B.low
        elif is_bearish(a) and is_bullish(b) and is_bearish(c):
            if c["open"] >= b["high"] and c["close"] <= b["low"]:
                ob = _build_ob("bearish", a, b, c)
                order_blocks.append(ob)

    return order_blocks


def _build_ob(ob_type, a, b, c):
    """Build an order block dict from the three candles."""
    from datetime import timedelta

    # OB zone = the range of candle B (the middle candle)
    ob_high = b["high"]
    ob_low = b["low"]

    # Candle C time window (for FVG scanning)
    c_time = c["time"]
    c_end = c_time + timedelta(hours=2)

    return {
        "type": ob_type,
        "candle_a": a,
        "candle_b": b,
        "candle_c": c,
        "ob_high": ob_high,
        "ob_low": ob_low,
        "candle_c_time": c_time,
        "candle_c_end": c_end,
    }


def filter_recent_obs(order_blocks, max_age_candles=None):
    """
    Keep only recent order blocks (within max_age_candles of the latest candle).
    If max_age_candles is None, uses config.OB_MAX_AGE_CANDLES.
    """
    if not order_blocks:
        return []

    if max_age_candles is None:
        max_age_candles = config.OB_MAX_AGE_CANDLES

    # Sort by candle C time, newest first
    order_blocks.sort(key=lambda x: x["candle_c_time"], reverse=True)

    # Keep only the most recent ones
    return order_blocks[:max_age_candles]


def get_latest_order_block(candles_df):
    """
    Convenience: detect all OBs and return the most recent one.
    Returns None if no OB found.
    """
    obs = detect_order_blocks(candles_df)
    obs = filter_recent_obs(obs)
    return obs[0] if obs else None
