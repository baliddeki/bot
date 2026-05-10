"""
mt5_executor.py — MetaTrader 5 trade execution.

Implements BaseExecutor. Works with any MT5 broker — including Deriv's
own MT5 gateway, IC Markets, Pepperstone, etc. Point your MT5 terminal
at whichever broker's server you want and this executor handles the rest.

Execution uses a hybrid model:
  ≤ MARKET_ORDER_MAX_DISTANCE_PIPS  → Market order (immediate fill)
  ≤ LIMIT_ORDER_MAX_DISTANCE_PIPS   → Limit order  (expires after N hours)
  > LIMIT_ORDER_MAX_DISTANCE_PIPS   → Signal skipped (too stale)
"""

import datetime
from typing import Optional

import MetaTrader5 as mt5

import config
from executor_base import BaseExecutor
from logger import log_event


class MT5Executor(BaseExecutor):
    """
    Executes trades via a locally running MetaTrader 5 terminal.

    Trade IDs returned by this executor are MT5 ticket numbers (cast to str).
    """

    # ── Connection lifecycle ──────────────────────────────────────────────

    def connect(self) -> bool:
        """Initialise the MT5 terminal connection."""
        kwargs = {}
        if config.MT5_LOGIN:
            kwargs["login"]    = config.MT5_LOGIN
            kwargs["password"] = config.MT5_PASSWORD
            kwargs["server"]   = config.MT5_SERVER

        if not mt5.initialize(**kwargs):
            log_event(f"MT5 initialise failed: {mt5.last_error()}", level="ERROR")
            return False

        info = mt5.account_info()
        log_event(
            f"MT5 connected: account #{info.login} on {info.server} | "
            f"balance ${info.balance:,.2f}"
        )
        return True

    def disconnect(self):
        """Cleanly shut down the MT5 connection."""
        mt5.shutdown()
        log_event("MT5 disconnected.")

    def get_balance(self) -> float:
        """Return the current MT5 account balance in USD."""
        info = mt5.account_info()
        return float(info.balance) if info else 0.0

    # ── Order placement ───────────────────────────────────────────────────

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
        Place a trade using hybrid market/limit logic.

        Compares current MT5 price to the signal entry and decides
        whether to use a market order, limit order, or skip the signal.
        """
        current_price = self._get_current_price(direction)
        if current_price is None:
            log_event("MT5: cannot read current price — trade skipped.", level="ERROR")
            return None

        distance_pips = abs(current_price - entry) / config.PIP_SIZE

        if distance_pips <= config.MARKET_ORDER_MAX_DISTANCE_PIPS:
            log_event(f"MT5: placing MARKET order ({distance_pips:.1f} pips from entry)")
            ticket = self._market_order(direction, sl, tp, lot_size, comment)

        elif distance_pips <= config.LIMIT_ORDER_MAX_DISTANCE_PIPS:
            log_event(f"MT5: placing LIMIT order ({distance_pips:.1f} pips from entry)")
            ticket = self._limit_order(direction, entry, sl, tp, lot_size, comment)

        else:
            log_event(
                f"MT5: signal skipped — {distance_pips:.1f} pips from entry "
                f"(max {config.LIMIT_ORDER_MAX_DISTANCE_PIPS} pips)."
            )
            return None

        return str(ticket) if ticket is not None else None

    # ── Position management ───────────────────────────────────────────────

    def close_partial(self, trade_id: str, lot_fraction: float, comment: str = "") -> bool:
        """Close a fraction of an open position."""
        ticket   = int(trade_id)
        position = self._get_position(ticket)
        if not position:
            log_event(f"MT5: partial close failed — ticket {ticket} not found.", level="ERROR")
            return False

        lots_to_close = round(position.volume * lot_fraction, 2)
        lots_to_close = max(config.MIN_LOT_SIZE, lots_to_close)

        close_type = (
            mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        tick  = mt5.symbol_info_tick(config.MT5_SYMBOL)
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    config.MT5_SYMBOL,
            "volume":    lots_to_close,
            "type":      close_type,
            "position":  ticket,
            "price":     price,
            "deviation": config.DEVIATION_POINTS,
            "magic":     config.MAGIC_NUMBER,
            "comment":   comment,
        }
        result  = mt5.order_send(request)
        success = result.retcode == mt5.TRADE_RETCODE_DONE

        if not success:
            log_event(
                f"MT5: partial close failed: code {result.retcode} — {result.comment}",
                level="ERROR",
            )
        else:
            log_event(f"MT5: closed {lots_to_close} lots on ticket {ticket}")
        return success

    def close_full(self, trade_id: str, comment: str = "") -> bool:
        """Close the entire position."""
        ticket   = int(trade_id)
        position = self._get_position(ticket)
        if not position:
            log_event(f"MT5: full close failed — ticket {ticket} not found.", level="ERROR")
            return False
        return self.close_partial(trade_id, lot_fraction=1.0, comment=comment)

    def modify_sl(self, trade_id: str, new_sl: float) -> bool:
        """Modify the stop loss of an open position."""
        return self._modify_sltp(int(trade_id), new_sl=new_sl)

    def modify_tp(self, trade_id: str, new_tp: float) -> bool:
        """Modify the take profit of an open position."""
        return self._modify_sltp(int(trade_id), new_tp=new_tp)

    # ── Private helpers ───────────────────────────────────────────────────

    def _get_current_price(self, direction: str) -> Optional[float]:
        tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        if not tick:
            return None
        return tick.ask if direction == "BUY" else tick.bid

    def _get_position(self, ticket: int):
        positions = mt5.positions_get(ticket=ticket)
        return positions[0] if positions else None

    def _modify_sltp(
        self,
        ticket: int,
        new_sl: Optional[float] = None,
        new_tp: Optional[float] = None,
    ) -> bool:
        position = self._get_position(ticket)
        if not position:
            return False

        request = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl":       new_sl if new_sl is not None else position.sl,
            "tp":       new_tp if new_tp is not None else position.tp,
        }
        result  = mt5.order_send(request)
        success = result.retcode == mt5.TRADE_RETCODE_DONE

        if not success:
            log_event(
                f"MT5: SL/TP modify failed: code {result.retcode} — {result.comment}",
                level="ERROR",
            )
        return success

    def _market_order(
        self, direction: str, sl: float, tp: float, lots: float, comment: str
    ) -> Optional[int]:
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
        tick  = mt5.symbol_info_tick(config.MT5_SYMBOL)
        price = tick.ask if direction == "BUY" else tick.bid

        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       config.MT5_SYMBOL,
            "volume":       lots,
            "type":         order_type,
            "price":        price,
            "sl":           sl,
            "tp":           tp,
            "deviation":    config.DEVIATION_POINTS,
            "magic":        config.MAGIC_NUMBER,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log_event(
                f"MT5: market order failed: code {result.retcode} — {result.comment}",
                level="ERROR",
            )
            return None

        log_event(
            f"MT5: market order placed: ticket {result.order} | "
            f"{lots} lots {direction} @ {price:.2f}"
        )
        return result.order

    def _limit_order(
        self,
        direction: str, entry: float, sl: float, tp: float,
        lots: float, comment: str,
    ) -> Optional[int]:
        order_type = (
            mt5.ORDER_TYPE_BUY_LIMIT if direction == "BUY"
            else mt5.ORDER_TYPE_SELL_LIMIT
        )
        expiry = datetime.datetime.now() + datetime.timedelta(
            hours=config.LIMIT_ORDER_EXPIRY_HOURS
        )
        request = {
            "action":       mt5.TRADE_ACTION_PENDING,
            "symbol":       config.MT5_SYMBOL,
            "volume":       lots,
            "type":         order_type,
            "price":        entry,
            "sl":           sl,
            "tp":           tp,
            "deviation":    config.DEVIATION_POINTS,
            "magic":        config.MAGIC_NUMBER,
            "comment":      comment,
            "type_time":    mt5.ORDER_TIME_SPECIFIED,
            "expiration":   int(expiry.timestamp()),
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        result = mt5.order_send(request)

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log_event(
                f"MT5: limit order failed: code {result.retcode} — {result.comment}",
                level="ERROR",
            )
            return None

        log_event(
            f"MT5: limit order placed: ticket {result.order} | "
            f"{lots} lots {direction} @ {entry:.2f}"
        )
        return result.order
