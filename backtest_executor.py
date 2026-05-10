"""
backtest_executor.py — In-memory virtual broker for backtesting.

Implements BaseExecutor with no real broker connection.
Tracks virtual positions in memory and simulates fills at the prices
passed in by the backtest engine.

Fill assumptions:
  - Entries always fill at the requested price (no slippage model)
  - SL and TP exits fill at exactly those price levels
  - Partial closes fill at the requested fraction
"""

from dataclasses import dataclass, field
from typing import Optional
import uuid

from executor_base import BaseExecutor
import config

# ─────────────────────────────────────────────────────────────────────────────
# Virtual position record
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class VirtualPosition:
    trade_id: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    lot_size: float  # Original lot size
    current_lots: float  # Remaining lots (reduced on partial close)
    sl: float
    tp: float  # TP1 — used by trade_manager for MT5 side
    comment: str


# ─────────────────────────────────────────────────────────────────────────────
# Executor
# ─────────────────────────────────────────────────────────────────────────────


class BacktestExecutor(BaseExecutor):
    """
    Virtual executor for backtesting.

    The backtest engine uses this to open/close positions during the
    walk-forward simulation. All state is held in self.positions.
    """

    def __init__(self, initial_balance: float):
        self._balance: float = initial_balance
        self.positions: dict[str, VirtualPosition] = {}
        self.trade_log: list[dict] = []  # Completed trade records

    # ── Connection lifecycle (no-ops in backtest) ─────────────────────────

    def connect(self) -> bool:
        return True

    def disconnect(self):
        pass

    def get_balance(self) -> float:
        return self._balance

    def set_balance(self, amount: float):
        """Called by the engine after each closed trade to update balance."""
        self._balance = amount

    # ── Order placement ───────────────────────────────────────────────────

    def place_trade(
        self,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        lot_size: float,
        comment: str = "",
    ) -> Optional[str]:
        """Open a virtual position. Fills immediately at entry price."""
        trade_id = uuid.uuid4().hex[:8].upper()
        self.positions[trade_id] = VirtualPosition(
            trade_id=trade_id,
            direction=direction,
            entry_price=entry,
            lot_size=lot_size,
            current_lots=lot_size,
            sl=sl,
            tp=tp,
            comment=comment,
        )
        return trade_id

    # ── Position management ───────────────────────────────────────────────

    def close_partial(
        self, trade_id: str, lot_fraction: float, comment: str = ""
    ) -> bool:
        """Close a fraction of the position. Records the partial exit."""
        pos = self.positions.get(trade_id)
        if not pos:
            return False

        lots_closed = round(pos.current_lots * lot_fraction, 2)
        pos.current_lots = round(pos.current_lots - lots_closed, 2)
        return True

    def close_full(self, trade_id: str, comment: str = "") -> bool:
        """Close the entire position and remove it from the registry."""
        pos = self.positions.get(trade_id)
        if not pos:
            return False
        del self.positions[trade_id]
        return True

    def modify_sl(self, trade_id: str, new_sl: float) -> bool:
        pos = self.positions.get(trade_id)
        if not pos:
            return False
        pos.sl = new_sl
        return True

    def modify_tp(self, trade_id: str, new_tp: float) -> bool:
        pos = self.positions.get(trade_id)
        if not pos:
            return False
        pos.tp = new_tp
        return True
