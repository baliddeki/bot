"""
Fair Value Gap (FVG) Detector
==============================
Scans lower timeframes (3M, 5M, 15M, 30M) for FVGs within a given time window.

Bullish FVG:
  Candle 1 = Bearish
  Candle 2 = Bullish
  Candle 3 = Bullish
  Gap: Candle 3 low > Candle 1 high
  Zone: [Candle 1 high, Candle 3 low]

Bearish FVG:
  Candle 1 = Bullish
  Candle 2 = Bearish
  Candle 3 = Bearish
  Gap: Candle 3 high < Candle 1 low
  Zone: [Candle 3 high, Candle 1 low]
"""


def detect_fvgs(candles_df, direction):
    """
    Find all FVGs in the given candles for the specified direction.

    Args:
        candles_df: DataFrame with [time, open, high, low, close]
        direction: "bullish" or "bearish"

    Returns:
        List of FVG dicts:
        {
            "direction": "bullish" or "bearish",
            "zone_top": float,
            "zone_bottom": float,
            "time": datetime (time of candle 2, the middle candle),
            "candle_1": dict,
            "candle_2": dict,
            "candle_3": dict,
        }
    """
    if candles_df is None or len(candles_df) < 3:
        return []

    fvgs = []
    rows = candles_df.to_dict("records")

    for i in range(len(rows) - 2):
        c1 = rows[i]
        c2 = rows[i + 1]
        c3 = rows[i + 2]

        if direction == "bullish":
            # Candle 1 bearish, candles 2 & 3 bullish
            if _is_bearish(c1) and _is_bullish(c2) and _is_bullish(c3):
                gap = c3["low"] - c1["high"]
                if gap > 0:
                    fvgs.append(
                        {
                            "direction": "bullish",
                            "zone_top": c3["low"],  # Top of gap
                            "zone_bottom": c1["high"],  # Bottom of gap
                            "time": c2["time"],
                            "candle_1": c1,
                            "candle_2": c2,
                            "candle_3": c3,
                        }
                    )

        elif direction == "bearish":
            # Candle 1 bullish, candles 2 & 3 bearish
            if _is_bullish(c1) and _is_bearish(c2) and _is_bearish(c3):
                gap = c1["low"] - c3["high"]
                if gap > 0:
                    fvgs.append(
                        {
                            "direction": "bearish",
                            "zone_top": c1["low"],  # Top of gap
                            "zone_bottom": c3["high"],  # Bottom of gap
                            "time": c2["time"],
                            "candle_1": c1,
                            "candle_2": c2,
                            "candle_3": c3,
                        }
                    )

    return fvgs


def find_first_fvg_in_window(connection, direction, start_time, end_time):
    """
    Scan timeframes 3M -> 5M -> 15M -> 30M for the first FVG.
    Returns the first one found on the lowest timeframe, or None.

    Args:
        connection: MT5Connection instance
        direction: "bullish" or "bearish"
        start_time: Start of Candle C window
        end_time: End of Candle C window

    Returns:
        FVG dict or None
    """
    from config import FVG_TIMEFRAMES

    for tf in FVG_TIMEFRAMES:
        candles = connection.get_candles_in_window(tf, start_time, end_time)
        if candles is None or len(candles) < 3:
            continue

        fvgs = detect_fvgs(candles, direction)
        if fvgs:
            # Return the first FVG found (earliest in time)
            fvgs.sort(key=lambda x: x["time"])
            best = fvgs[0]
            best["timeframe"] = tf
            return best

    return None


def _is_bullish(candle):
    return candle["close"] > candle["open"]


def _is_bearish(candle):
    return candle["close"] < candle["open"]
