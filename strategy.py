from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Literal

import pandas as pd

from config import StrategyConfig
from data_loader import pip_to_price, tf_minutes
from swings import detect_fractal_swings, find_sweep_on_candle, SwingPoint
from flag_pattern import find_flag, FlagSetup
from fvg import detect_fvgs, first_fvg_in_window, entry_price_for_zone, FVGZone

Direction = Literal["BUY", "SELL"]


@dataclass
class TradePlan:
    symbol: str
    direction: Direction
    flag_tf: str  # TF the flag was found on
    fvg_tf: str  # TF the FVG was found on
    sweep_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    zone: FVGZone
    meta: Dict = field(default_factory=dict)

    @property
    def tf(self) -> str:
        """Convenience: return fvg_tf for logging."""
        return self.fvg_tf


# ─────────────────────────────────────────────────────────────────────────────
#  Core plan builder
# ─────────────────────────────────────────────────────────────────────────────


def build_trade_plan_from_sweep(
    cfg: StrategyConfig,
    sweep_time: pd.Timestamp,
    direction: Direction,
    ltf_data: Dict[str, pd.DataFrame],
) -> Optional[TradePlan]:
    """
    Given a confirmed 2H sweep event, find a flag + FVG entry.

    FLOW:
    1. For each flag TF (H1 → M30 → M15), scan candles AFTER sweep_time
       for the first valid bullish/bearish flag pattern.

    2. Once a flag is found (c1, c2, c3 engulfing candle):

       a) PRIMARY RULE – check the candle AFTER c3 (call it c4) on the
          same flag TF. If c4 forms an FVG with c2 and c3, enter that FVG.
          (This means c4 already closed and the gap is confirmed.)

       b) FALLBACK RULE – scan LTF data (M3 → M5 → M15 → M30 → H1)
          for the first FVG formed DURING the c3 candle's time window.
          Enter the lowest TF FVG found.

    3. SL and TP are fixed-pip distances from entry (from config).
    """

    sl_dist = pip_to_price(cfg.sl_pips)
    tp_dist = pip_to_price(cfg.tp_pips)

    for flag_tf in cfg.flag_tfs:
        df_flag = ltf_data.get(flag_tf)
        if df_flag is None or df_flag.empty:
            continue

        # Only look at candles that opened AFTER the sweep
        after_sweep = (
            df_flag[df_flag["time"] > sweep_time].copy().reset_index(drop=True)
        )
        if after_sweep.empty:
            continue

        flag = find_flag(after_sweep, direction, cfg.flag_scan_max_bars)
        if flag is None:
            continue

        flag.tf = flag_tf
        c3_time: pd.Timestamp = flag.c3_time

        # ── TIME WINDOW OF c3 ──────────────────────────────────────────
        c3_end = c3_time + pd.Timedelta(minutes=tf_minutes(flag_tf))

        # ── PRIMARY: c4 FVG on the same flag TF ───────────────────────
        # c4 is the first candle that opened after c3 closed
        after_c3 = after_sweep[after_sweep["time"] >= c3_end].reset_index(drop=True)

        if not after_c3.empty:
            c4_time = after_c3.iloc[0].time

            # Slice from c2 to c4 (inclusive) to detect the 3-candle FVG
            # involving c2, c3, c4
            slice_c4 = after_sweep[
                (after_sweep["time"] >= flag.c2_time) & (after_sweep["time"] <= c4_time)
            ].reset_index(drop=True)

            zones = detect_fvgs(slice_c4, direction, flag_tf)
            c4_zones = [z for z in zones if z.formed_time == c4_time]

            if c4_zones:
                zone = c4_zones[0]
                entry = entry_price_for_zone(zone, cfg.entry_at_midpoint)
                sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
                tp = entry + tp_dist if direction == "BUY" else entry - tp_dist
                return TradePlan(
                    symbol=cfg.symbol,
                    direction=direction,
                    flag_tf=flag_tf,
                    fvg_tf=flag_tf,
                    sweep_time=sweep_time,
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    zone=zone,
                    meta={
                        "rule": "c4_fvg",
                        "flag_c3": str(c3_time),
                        "flag_c4": str(c4_time),
                    },
                )

        # ── FALLBACK: LTF FVG during c3 window ────────────────────────
        # Search M3 first (lowest), then M5, M15, M30, H1
        for fvg_tf in cfg.fvg_tfs:
            df_fvg = ltf_data.get(fvg_tf)
            if df_fvg is None or df_fvg.empty:
                continue

            zone = first_fvg_in_window(
                df_fvg,
                direction,
                fvg_tf,
                t_start=c3_time,
                t_end=c3_end,
            )
            if zone is None:
                continue

            entry = entry_price_for_zone(zone, cfg.entry_at_midpoint)
            sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
            tp = entry + tp_dist if direction == "BUY" else entry - tp_dist
            return TradePlan(
                symbol=cfg.symbol,
                direction=direction,
                flag_tf=flag_tf,
                fvg_tf=fvg_tf,
                sweep_time=sweep_time,
                entry=entry,
                sl=sl,
                tp=tp,
                zone=zone,
                meta={
                    "rule": "ltf_fvg_during_c3",
                    "flag_c3": str(c3_time),
                    "fvg_time": str(zone.formed_time),
                },
            )

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  History-based plan builder (used by live runner)
# ─────────────────────────────────────────────────────────────────────────────


def build_trade_plan_from_history(
    cfg: StrategyConfig,
    df_2h: pd.DataFrame,
    ltf_data: Dict[str, pd.DataFrame],
) -> Optional[TradePlan]:
    """
    Scan the most recent 2H data for the latest sweep event, then build a plan.
    """
    swings = detect_fractal_swings(
        df_2h, cfg.swing_fractal_left, cfg.swing_fractal_right
    )

    latest_sweep = None
    for i in range(1, len(df_2h)):
        result = find_sweep_on_candle(
            swings,
            df_2h.iloc[i],
            i,
            require_wick_sweep=cfg.require_wick_sweep,
        )
        if result:
            direction, swing = result
            latest_sweep = (direction, swing, df_2h.iloc[i].time)

    if latest_sweep is None:
        return None

    direction, swing, sweep_time = latest_sweep
    plan = build_trade_plan_from_sweep(cfg, sweep_time, direction, ltf_data)
    if plan is not None:
        plan.meta.update(
            {
                "swing_price": swing.price,
                "swing_time": str(swing.time),
                "swing_kind": swing.kind,
            }
        )
    return plan
