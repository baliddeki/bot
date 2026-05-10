"""
executor_base.py — Abstract base class for all trade executors.

Every executor (MT5, Deriv, future brokers) must implement this interface.
The rest of the bot only interacts with this contract — never with a
specific executor directly.

To add a new broker:
  1. Create a new file, e.g. my_broker_executor.py
  2. Subclass BaseExecutor and implement all abstract methods
  3. Register it in executor_factory.py
  4. Set EXECUTION_BROKER in config.py
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseExecutor(ABC):
    """
    Broker-agnostic interface for trade execution.

    All price and SL/TP values are in the instrument's native price units.
    All size values are normalised to lots (1 lot = 100 oz for XAUUSD).
    Brokers that use stake-based sizing (e.g. Deriv Multipliers) convert
    internally inside their implementation.
    """

    # ── Connection lifecycle ──────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> bool:
        """
        Establish a connection to the broker.
        Returns True on success, False on failure.
        Called once at bot startup.
        """

    @abstractmethod
    def disconnect(self):
        """
        Close the broker connection cleanly.
        Called on bot shutdown.
        """

    @abstractmethod
    def get_balance(self) -> float:
        """
        Return the current account balance in USD.
        Called on every scan cycle to refresh risk calculations.
        """

    # ── Order placement ───────────────────────────────────────────────────

    @abstractmethod
    def place_trade(
        self,
        direction: str,
        entry:     float,
        sl:        float,
        tp:        float,
        lot_size:  float,
        comment:   str = "",
    ) -> Optional[str]:
        """
        Place a new trade.

        Args:
            direction: "BUY" or "SELL".
            entry:     Intended entry price (may trigger limit logic internally).
            sl:        Stop loss price.
            tp:        Take profit price.
            lot_size:  Position size in lots. Executors may convert to their
                       native unit (e.g. stake amount for Deriv Multipliers).
            comment:   Optional label for the order.

        Returns:
            A string trade ID / ticket on success, or None on failure.
            The ID is opaque — pass it back to the other methods as-is.
        """

    # ── Position management ───────────────────────────────────────────────

    @abstractmethod
    def close_partial(self, trade_id: str, lot_fraction: float, comment: str = "") -> bool:
        """
        Close a portion of an open position.

        Args:
            trade_id:     The ID returned by place_trade().
            lot_fraction: Fraction of the original lot size to close (0 < x ≤ 1).
                          Using a fraction rather than an absolute lot count makes
                          this interface portable across lot-based and stake-based brokers.
            comment:      Optional label for the close order.

        Returns:
            True if the partial close was confirmed, False otherwise.
        """

    @abstractmethod
    def close_full(self, trade_id: str, comment: str = "") -> bool:
        """
        Close an entire open position.

        Returns True if confirmed, False otherwise.
        """

    @abstractmethod
    def modify_sl(self, trade_id: str, new_sl: float) -> bool:
        """
        Modify the stop loss of an open position.

        Returns True if confirmed, False otherwise.
        """

    @abstractmethod
    def modify_tp(self, trade_id: str, new_tp: float) -> bool:
        """
        Modify the take profit of an open position.

        Returns True if confirmed, False otherwise.
        """
