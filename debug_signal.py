"""
debug_signal.py — Traces the signal pipeline step by step to show exactly
where setups are being rejected and why.

Run this before the backtest to diagnose why no trades are firing.

Usage:
    python debug_signal.py
"""

from datetime import datetime
import pandas as pd

import config
from deriv_client import fetch_all_timeframes
from swing_detector import find_swing_highs, find_swing_lows, find_recent_sweep
from ob_detector import find_order_blocks, get_most_recent_ob, price_inside_ob
from fvg_detector import search_fvg_across_timeframes


def main():
    print("\n── Fetching recent data ──────────────────────────────")
    candle_data = fetch_all_timeframes(config.SYMBOL)
    current_price = _get_price(candle_data)
    print(f"Current price : {current_price}")

    print("\n── Step 1: Sweep scan ────────────────────────────────")
    for tf in config.LIQUIDATION_TIMEFRAMES:
        df = candle_data.get(tf)
        if df is None or len(df) < 10:
            print(
                f"  [{tf}] SKIP — insufficient data ({len(df) if df is not None else 0} candles)"
            )
            continue

        highs = find_swing_highs(df)
        lows = find_swing_lows(df)
        print(
            f"  [{tf}] {len(df)} candles | {len(highs)} swing highs | {len(lows)} swing lows"
        )

        if highs:
            print(
                f"         Latest swing high: {highs[-1].price:.2f}  @ {highs[-1].time}"
            )
        if lows:
            print(
                f"         Latest swing low : {lows[-1].price:.2f}  @ {lows[-1].time}"
            )

        result = find_recent_sweep(df, lookback=config.SWEEP_RECENCY_CANDLES)
        if result:
            direction, swing = result
            print(f"         ✓ SWEEP FOUND: {direction} — swing @ {swing.price:.2f}")
        else:
            # Show how close the last candle came to sweeping anything
            last = df.iloc[-1]
            if highs:
                nearest_high = min(
                    highs, key=lambda h: abs(h.price - float(last["high"]))
                )
                gap = float(last["high"]) - nearest_high.price
                print(
                    f"         ✗ No sweep. Last high {float(last['high']):.2f} vs nearest swing high {nearest_high.price:.2f} (gap: {gap:+.2f})"
                )
            if lows:
                nearest_low = min(lows, key=lambda l: abs(l.price - float(last["low"])))
                gap = float(last["low"]) - nearest_low.price
                print(
                    f"         ✗ No sweep. Last low  {float(last['low']):.2f} vs nearest swing low  {nearest_low.price:.2f} (gap: {gap:+.2f})"
                )

    print("\n── Step 2: Order Block scan ──────────────────────────")
    for tf, permitted_ob_tfs in config.OB_TIMEFRAME_RULES.items():
        df = candle_data.get(tf)
        if df is None or len(df) < 3:
            continue
        for ob_tf in permitted_ob_tfs:
            ob_df = candle_data.get(ob_tf)
            if ob_df is None or len(ob_df) < 3:
                print(f"  [{tf} → {ob_tf}] SKIP — no data")
                continue
            blocks = find_order_blocks(ob_df, ob_tf)
            bull = [b for b in blocks if b.direction == "BUY"]
            bear = [b for b in blocks if b.direction == "SELL"]
            print(
                f"  [{tf} → {ob_tf}] {len(bull)} bullish OBs | {len(bear)} bearish OBs"
            )
            if bull:
                ob = bull[-1]
                in_zone = price_inside_ob(current_price, ob) if current_price else False
                print(
                    f"             Latest BUY OB:  [{ob.zone_low:.2f} — {ob.zone_high:.2f}]  price inside: {in_zone}"
                )
            if bear:
                ob = bear[-1]
                in_zone = price_inside_ob(current_price, ob) if current_price else False
                print(
                    f"             Latest SELL OB: [{ob.zone_low:.2f} — {ob.zone_high:.2f}]  price inside: {in_zone}"
                )

    print("\n── Step 3: FVG scan (in latest OB zones) ─────────────")
    # Pick the most recent OB on H4 as a sample zone to scan
    h4_df = candle_data.get("H4")
    if h4_df is not None and len(h4_df) >= 3:
        blocks = find_order_blocks(h4_df, "H4")
        for direction in ["BUY", "SELL"]:
            ob = get_most_recent_ob(blocks, direction)
            if ob:
                fvg = search_fvg_across_timeframes(
                    candle_data=candle_data,
                    ob_low=ob.zone_low,
                    ob_high=ob.zone_high,
                    direction=direction,
                    search_order=config.FVG_SEARCH_ORDER,
                )
                status = (
                    f"✓ FVG found on {fvg.timeframe} [{fvg.gap_low:.2f}—{fvg.gap_high:.2f}]"
                    if fvg
                    else "✗ No FVG in zone"
                )
                print(
                    f"  H4 {direction} OB [{ob.zone_low:.2f}—{ob.zone_high:.2f}]: {status}"
                )

    print("\n── Step 4: SWEEP_RECENCY_CANDLES sensitivity ─────────")
    print(f"  Current setting: SWEEP_RECENCY_CANDLES = {config.SWEEP_RECENCY_CANDLES}")
    print("  Testing wider lookbacks...")
    for tf in config.LIQUIDATION_TIMEFRAMES:
        df = candle_data.get(tf)
        if df is None or len(df) < 10:
            continue
        for lookback in [5, 10, 20, 50]:
            result = find_recent_sweep(df, lookback=lookback)
            if result:
                direction, swing = result
                print(
                    f"  [{tf}] lookback={lookback:>2} → SWEEP FOUND: {direction} @ {swing.price:.2f}"
                )
                break
        else:
            print(f"  [{tf}] No sweep found even with lookback=50")

    print()


def _get_price(candle_data: dict):
    for tf in ["H1", "H4", "D1"]:
        df = candle_data.get(tf)
        if df is not None and not df.empty:
            return float(df.iloc[-1]["close"])
    return None


if __name__ == "__main__":
    main()
