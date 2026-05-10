"""
mt5_executor.py — MetaTrader 5 trade execution.

Implements BaseExecutor. Works with any MT5 broker.

Entry logic:
  Every signal is placed as a GTC limit order at the C3 wick of the FVG.
  These orders are NEVER cancelled — they remain active until filled.
  No distance thresholds. No expiry. The market comes to the order.
"""

from typing import Optional

import MetaTrader5 as mt5

import config
from executor_base import BaseExecutor
from logger import log_event


class MT5Executor(BaseExecutor):

    def connect(self) -> bool:
        kwargs = {}
        if config.MT5_LOGIN:
            kwargs["login"] = config.MT5_LOGIN
            kwargs["password"] = config.MT5_PASSWORD
            kwargs["server"] = config.MT5_SERVER
        if not mt5.initialize(**kwargs):
            log_event(f"MT5 initialise failed: {mt5.last_error()}", level="ERROR")
            return False
        info = mt5.account_info()
        log_event(
            f"MT5 connected: #{info.login} on {info.server} | ${info.balance:,.2f}"
        )
        return True

    def disconnect(self):
        mt5.shutdown()
        log_event("MT5 disconnected.")

    def get_balance(self) -> float:
        info = mt5.account_info()
        return float(info.balance) if info else 0.0

    def place_trade(
        self,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
        lot_size: float,
        comment: str = "",
    ) -> Optional[str]:
        """
        Always places a GTC limit order at the entry price.
        No distance check, no expiry — the order waits until filled.
        """
        order_type = (
            mt5.ORDER_TYPE_BUY_LIMIT
            if direction == "BUY"
            else mt5.ORDER_TYPE_SELL_LIMIT
        )
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": config.MT5_SYMBOL,
            "volume": lot_size,
            "type": order_type,
            "price": entry,
            "sl": sl,
            "tp": tp,
            "deviation": config.DEVIATION_POINTS,
            "magic": config.MAGIC_NUMBER,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log_event(
                f"MT5: limit failed: {result.retcode} — {result.comment}", level="ERROR"
            )
            return None
        log_event(
            f"MT5: GTC limit #{result.order} | {direction} {lot_size} @ {entry:.2f}"
        )
        return str(result.order)

    def close_partial(
        self, trade_id: str, lot_fraction: float, comment: str = ""
    ) -> bool:
        ticket = int(trade_id)
        position = self._get_pos(ticket)
        if not position:
            return False
        lots = max(config.MIN_LOT_SIZE, round(position.volume * lot_fraction, 2))
        close_type = (
            mt5.ORDER_TYPE_SELL
            if position.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": config.MT5_SYMBOL,
            "volume": lots,
            "type": close_type,
            "position": ticket,
            "price": price,
            "deviation": config.DEVIATION_POINTS,
            "magic": config.MAGIC_NUMBER,
            "comment": comment,
        }
        result = mt5.order_send(req)
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        if not ok:
            log_event(f"MT5: partial close failed: {result.retcode}", level="ERROR")
        return ok

    def close_full(self, trade_id: str, comment: str = "") -> bool:
        return self.close_partial(trade_id, 1.0, comment)

    def modify_sl(self, trade_id: str, new_sl: float) -> bool:
        return self._sltp(int(trade_id), new_sl=new_sl)

    def modify_tp(self, trade_id: str, new_tp: float) -> bool:
        return self._sltp(int(trade_id), new_tp=new_tp)

    def _get_pos(self, ticket: int):
        p = mt5.positions_get(ticket=ticket)
        return p[0] if p else None

    def _sltp(self, ticket: int, new_sl=None, new_tp=None) -> bool:
        pos = self._get_pos(ticket)
        if not pos:
            return False
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": new_sl if new_sl is not None else pos.sl,
            "tp": new_tp if new_tp is not None else pos.tp,
        }
        result = mt5.order_send(req)
        ok = result.retcode == mt5.TRADE_RETCODE_DONE
        if not ok:
            log_event(f"MT5: SL/TP modify failed: {result.retcode}", level="ERROR")
        return ok
