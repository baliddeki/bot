from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal

import pandas as pd

Direction = Literal["BUY", "SELL"]


@dataclass
class FlagSetup:
    direction: Direction
    tf: str
    c1_time: pd.Timestamp
    c2_time: pd.Timestamp
    c3_time: pd.Timestamp
    c1: dict  # impulse candle
    c2: dict  # retracement candle
    c3: dict  # engulfing candle (trigger)
    idx: int  # index of c3 in the scanned DataFrame


# ── helpers ──────────────────────────────────────────────────────────────────


def _bull(o: float, c: float) -> bool:
    return c > o


def _bear(o: float, c: float) -> bool:
    return c < o


def _body_top(o: float, c: float) -> float:
    return max(o, c)


def _body_bot(o: float, c: float) -> float:
    return min(o, c)


def _engulfs(c3o: float, c3c: float, c2o: float, c2c: float) -> bool:
    """c3 body fully covers c2 body."""
    return _body_top(c3o, c3c) >= _body_top(c2o, c2c) and _body_bot(
        c3o, c3c
    ) <= _body_bot(c2o, c2c)


# ── public API ────────────────────────────────────────────────────────────────


def find_flag(
    df: pd.DataFrame,
    direction: Direction,
    max_bars: int,
) -> Optional[FlagSetup]:
    """
    Scan `df` for the first occurrence of the 3-candle flag:

    BUY flag  (bullish reversal after sweep of lows):
      c1 = bullish (impulse up)
      c2 = bearish (pullback)
      c3 = bullish AND body engulfs c2 body  ← trigger / engulfing candle

    SELL flag (bearish reversal after sweep of highs):
      c1 = bearish (impulse down)
      c2 = bullish (pullback)
      c3 = bearish AND body engulfs c2 body  ← trigger / engulfing candle

    Scanning stops at max_bars.
    Returns the FIRST valid setup found (earliest in time).
    """
    if df is None or df.empty:
        return None

    n = min(len(df), max_bars)

    for i in range(2, n):
        c1 = df.iloc[i - 2]
        c2 = df.iloc[i - 1]
        c3 = df.iloc[i]

        o1, cl1 = float(c1.open), float(c1.close)
        o2, cl2 = float(c2.open), float(c2.close)
        o3, cl3 = float(c3.open), float(c3.close)

        if direction == "BUY":
            valid = (
                _bull(o1, cl1)
                and _bear(o2, cl2)
                and _bull(o3, cl3)
                and _engulfs(o3, cl3, o2, cl2)
            )
        else:
            valid = (
                _bear(o1, cl1)
                and _bull(o2, cl2)
                and _bear(o3, cl3)
                and _engulfs(o3, cl3, o2, cl2)
            )

        if valid:
            return FlagSetup(
                direction=direction,
                tf="",
                c1_time=c1.time,
                c2_time=c2.time,
                c3_time=c3.time,
                c1=c1.to_dict(),
                c2=c2.to_dict(),
                c3=c3.to_dict(),
                idx=i,
            )

    return None
