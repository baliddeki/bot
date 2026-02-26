from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Literal, Tuple

import pandas as pd

SwingKind = Literal["HIGH", "LOW"]


@dataclass
class SwingPoint:
    kind: SwingKind
    time: pd.Timestamp
    price: float
    idx: int
    taken: bool = False


def detect_fractal_swings(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
) -> List[SwingPoint]:
    """
    Fractal swing detector on H2 data.

    A swing HIGH at index i means:
        df.high[i] > df.high[i-j] for j in 1..left
        df.high[i] > df.high[i+j] for j in 1..right

    A swing LOW at index i means:
        df.low[i] < df.low[i-j]  for j in 1..left
        df.low[i] < df.low[i+j]  for j in 1..right
    """
    swings: List[SwingPoint] = []
    if df is None or df.empty:
        return swings

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    times = df["time"].to_list()
    n = len(df)

    for i in range(left, n - right):
        h = highs[i]
        l = lows[i]

        is_high = all(h > highs[i - j] for j in range(1, left + 1)) and all(
            h > highs[i + j] for j in range(1, right + 1)
        )

        is_low = all(l < lows[i - j] for j in range(1, left + 1)) and all(
            l < lows[i + j] for j in range(1, right + 1)
        )

        if is_high:
            swings.append(SwingPoint("HIGH", times[i], float(h), i))
        if is_low:
            swings.append(SwingPoint("LOW", times[i], float(l), i))

    return swings


def find_sweep_on_candle(
    swings: List[SwingPoint],
    candle: pd.Series,
    candle_idx: int,
    require_wick_sweep: bool = True,
) -> Optional[Tuple[str, SwingPoint]]:
    """
    Check if this 2H candle sweeps any prior untaken swing.

    Sweep HIGH → SELL signal (price rejected after taking liquidity above)
    Sweep LOW  → BUY  signal (price rejected after taking liquidity below)

    Wick sweep means:
      HIGH sweep: candle.high > swing.price AND candle.close < swing.price
      LOW  sweep: candle.low  < swing.price AND candle.close > swing.price
    """
    ch = float(candle.high)
    cl = float(candle.low)
    cc = float(candle.close)

    # Check most recent untaken HIGH first → SELL
    highs = sorted(
        [s for s in swings if s.kind == "HIGH" and not s.taken and s.idx < candle_idx],
        key=lambda s: s.idx,
        reverse=True,
    )
    for s in highs:
        if ch > s.price:
            if not require_wick_sweep or cc < s.price:
                return ("SELL", s)
        break  # only check most recent

    # Check most recent untaken LOW → BUY
    lows = sorted(
        [s for s in swings if s.kind == "LOW" and not s.taken and s.idx < candle_idx],
        key=lambda s: s.idx,
        reverse=True,
    )
    for s in lows:
        if cl < s.price:
            if not require_wick_sweep or cc > s.price:
                return ("BUY", s)
        break  # only check most recent

    return None
