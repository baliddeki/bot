from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal, List

import pandas as pd

Direction = Literal["BUY", "SELL"]


@dataclass
class FVGZone:
    direction: Direction
    tf: str
    formed_time: pd.Timestamp  # time of c3 (the candle that closed the gap)
    top: float
    bottom: float

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom


def detect_fvgs(
    df: pd.DataFrame,
    direction: Direction,
    tf: str,
) -> List[FVGZone]:
    """
    Standard 3-candle FVG scan.

    Bullish FVG: c1.high < c3.low   → zone = [c1.high, c3.low]
    Bearish FVG: c1.low  > c3.high  → zone = [c3.high, c1.low]

    formed_time = c3.time
    """
    zones: List[FVGZone] = []
    if df is None or len(df) < 3:
        return zones

    for i in range(2, len(df)):
        c1 = df.iloc[i - 2]
        c3 = df.iloc[i]

        if direction == "BUY":
            if float(c1.high) < float(c3.low):
                zones.append(
                    FVGZone(
                        direction="BUY",
                        tf=tf,
                        formed_time=c3.time,
                        top=float(c3.low),
                        bottom=float(c1.high),
                    )
                )
        else:
            if float(c1.low) > float(c3.high):
                zones.append(
                    FVGZone(
                        direction="SELL",
                        tf=tf,
                        formed_time=c3.time,
                        top=float(c1.low),
                        bottom=float(c3.high),
                    )
                )

    return zones


def first_fvg_in_window(
    df: pd.DataFrame,
    direction: Direction,
    tf: str,
    t_start: pd.Timestamp,
    t_end: pd.Timestamp,
) -> Optional[FVGZone]:
    """
    Return the first FVG formed in the time window [t_start, t_end].
    Used to find LTF FVGs during the engulfing candle's time window.
    """
    window = (
        df[(df["time"] >= t_start) & (df["time"] <= t_end)]
        .copy()
        .reset_index(drop=True)
    )
    zones = detect_fvgs(window, direction, tf)
    return zones[0] if zones else None


def entry_price_for_zone(zone: FVGZone, midpoint: bool = True) -> float:
    """
    Entry price for a given FVG zone.
    midpoint=True  → midpoint of the zone
    midpoint=False → zone bottom (BUY) or zone top (SELL)
    """
    if midpoint:
        return zone.midpoint
    return zone.bottom if zone.direction == "BUY" else zone.top
