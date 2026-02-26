from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional, Literal

import pandas as pd
import MetaTrader5 as mt5

Timeframe = Literal["M3", "M5", "M15", "M30", "H1", "H2"]

_TF_MAP = {
    "M3": mt5.TIMEFRAME_M3,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
}

# For XAUUSD: 1 pip = 0.10 price units
XAUUSD_PIP = 0.10


@dataclass
class SymbolSpec:
    symbol: str
    point: float
    digits: int
    tick_size: float
    tick_value: float
    volume_min: float
    volume_max: float
    volume_step: float

    @property
    def value_per_price_unit_per_lot(self) -> float:
        """Dollar value of 1 full price unit movement per 1 lot."""
        return 0.0 if self.tick_size <= 0 else self.tick_value / self.tick_size


def connect(
    login: Optional[int] = None,
    password: Optional[str] = None,
    server: Optional[str] = None,
) -> None:
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    if login is not None:
        ok = mt5.login(login=login, password=password or "", server=server or "")
        if not ok:
            raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")


def shutdown() -> None:
    mt5.shutdown()


def get_symbol_spec(symbol: str) -> SymbolSpec:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol not found: {symbol}")
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    return SymbolSpec(
        symbol=symbol,
        point=float(info.point),
        digits=int(info.digits),
        tick_size=float(getattr(info, "trade_tick_size", info.point)),
        tick_value=float(getattr(info, "trade_tick_value", 0.0)),
        volume_min=float(getattr(info, "volume_min", 0.01)),
        volume_max=float(getattr(info, "volume_max", 100.0)),
        volume_step=float(getattr(info, "volume_step", 0.01)),
    )


def _to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    if df.empty:
        return df
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "volume"})
    cols = ["time", "open", "high", "low", "close", "volume"]
    return df[[c for c in cols if c in df.columns]]


def fetch_rates(
    symbol: str,
    timeframe: Timeframe,
    dt_from: dt.datetime,
    dt_to: dt.datetime,
) -> pd.DataFrame:
    tf = _TF_MAP[timeframe]
    rates = mt5.copy_rates_range(symbol, tf, dt_from, dt_to)
    if rates is None:
        raise RuntimeError(f"copy_rates_range failed: {mt5.last_error()}")
    return _to_df(rates).sort_values("time").reset_index(drop=True)


def fetch_recent(symbol: str, timeframe: Timeframe, n: int = 800) -> pd.DataFrame:
    tf = _TF_MAP[timeframe]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, n)
    if rates is None:
        raise RuntimeError(f"copy_rates_from_pos failed: {mt5.last_error()}")
    return _to_df(rates).sort_values("time").reset_index(drop=True)


def pip_to_price(pips: float) -> float:
    """Convert pip count to XAUUSD price distance. 1 pip = 0.10."""
    return pips * XAUUSD_PIP


def tf_minutes(tf: Timeframe) -> int:
    return {"M3": 3, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H2": 120}[tf]
