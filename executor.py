"""
Trade Executor
===============
Places limit orders and market orders on MT5.
Handles filling modes, margin checks, and order management.
"""

import MetaTrader5 as mt5
from datetime import datetime, timedelta
import config


class TradeExecutor:
    def __init__(self, connection):
        """
        Args:
            connection: MT5Connection instance
        """
        self.connection = connection
        self.magic = config.MAGIC_NUMBER

    # ================================================================
    # LIMIT ORDER (primary entry method)
    # ================================================================

    def place_limit_order(self, signal, lot_size):
        """
        Place a limit order based on the signal.

        Args:
            signal: dict with action, entry, sl, tp1, tp2
            lot_size: calculated lot size

        Returns:
            MT5 order result or None
        """
        action = signal["action"]
        entry = float(signal["entry"])
        sl = float(signal["sl"])
        tp1 = float(signal["tp1"])  # We use TP1 as the initial TP on the order
        real_symbol = self.connection.real_symbol

        # Determine order type
        if action == "BUY":
            order_type = mt5.ORDER_TYPE_BUY_LIMIT
        elif action == "SELL":
            order_type = mt5.ORDER_TYPE_SELL_LIMIT
        else:
            print(f"Unknown action: {action}")
            return None

        # Get symbol info for volume rounding
        symbol_info = self.connection.get_symbol_info()
        if symbol_info is None:
            print(f"Cannot get symbol info for {real_symbol}")
            return None

        volume = self._round_volume(lot_size, symbol_info)
        if volume <= 0:
            print(f"Invalid volume after rounding: {volume}")
            return None

        # Set expiry
        expiry_time = datetime.now() + timedelta(hours=config.LIMIT_ORDER_EXPIRY_HOURS)

        # Build request
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": real_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": entry,
            "sl": sl,
            "tp": tp1,
            "deviation": config.DEVIATION,
            "magic": self.magic,
            "comment": f"OB_FVG_{action}",
            "type_filling": self._get_filling_mode(symbol_info),
            "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": int(expiry_time.timestamp()),
        }

        # Send order
        result = mt5.order_send(request)
        if result is None:
            print(f"order_send returned None: {mt5.last_error()}")
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Order failed: retcode={result.retcode} comment={result.comment}")
            self._log_error(result.retcode)
            return None

        print(
            f"LIMIT {action} placed: {volume} lots at {entry} "
            f"SL={sl} TP={tp1} ticket={result.order}"
        )
        return result

    # ================================================================
    # MARKET ORDER (fallback / immediate entry)
    # ================================================================

    def place_market_order(self, action, lot_size, sl, tp):
        """
        Place a market order (used if price is already at FVG level).

        Returns:
            MT5 order result or None
        """
        real_symbol = self.connection.real_symbol
        symbol_info = self.connection.get_symbol_info()
        if symbol_info is None:
            return None

        bid, ask = self.connection.get_current_price()
        if bid is None:
            return None

        price = ask if action == "BUY" else bid
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL

        volume = self._round_volume(lot_size, symbol_info)
        if volume <= 0:
            return None

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": real_symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": float(sl),
            "tp": float(tp),
            "deviation": config.DEVIATION,
            "magic": self.magic,
            "comment": f"OB_FVG_{action}",
            "type_filling": self._get_filling_mode(symbol_info),
            "type_time": mt5.ORDER_TIME_GTC,
        }

        result = mt5.order_send(request)
        if result is None:
            print(f"Market order failed: {mt5.last_error()}")
            return None

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(
                f"Market order failed: retcode={result.retcode} comment={result.comment}"
            )
            self._log_error(result.retcode)
            return None

        print(
            f"MARKET {action}: {volume} lots at {price} "
            f"SL={sl} TP={tp} ticket={result.order}"
        )
        return result

    # ================================================================
    # POSITION MANAGEMENT (partial close, modify SL)
    # ================================================================

    def close_partial(self, position, percent):
        """
        Close a percentage of an open position.

        Args:
            position: MT5 position object
            percent: 0-100, how much to close

        Returns:
            MT5 result or None
        """
        close_volume = round(position.volume * (percent / 100.0), 2)
        close_volume = max(close_volume, 0.01)  # Min lot

        if close_volume >= position.volume:
            close_volume = position.volume  # Close all

        real_symbol = self.connection.real_symbol
        symbol_info = self.connection.get_symbol_info()

        bid, ask = self.connection.get_current_price()
        if bid is None:
            return None

        # To close: opposite order type
        if position.type == mt5.ORDER_TYPE_BUY:
            close_type = mt5.ORDER_TYPE_SELL
            price = bid
        else:
            close_type = mt5.ORDER_TYPE_BUY
            price = ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": real_symbol,
            "volume": float(close_volume),
            "type": close_type,
            "position": position.ticket,
            "price": price,
            "deviation": config.DEVIATION,
            "magic": self.magic,
            "comment": f"PARTIAL_{percent}%",
            "type_filling": self._get_filling_mode(symbol_info),
            "type_time": mt5.ORDER_TIME_GTC,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(
                f"Closed {percent}% ({close_volume} lots) of ticket {position.ticket}"
            )
            return result
        else:
            print(f"Partial close failed: {result.retcode if result else 'None'}")
            return None

    def modify_sl(self, position, new_sl):
        """
        Move stop loss on an open position.

        Args:
            position: MT5 position object
            new_sl: New stop loss price
        """
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.connection.real_symbol,
            "position": position.ticket,
            "sl": float(new_sl),
            "tp": float(position.tp),
            "magic": self.magic,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"SL moved to {new_sl} on ticket {position.ticket}")
            return result
        else:
            print(f"SL modify failed: {result.retcode if result else 'None'}")
            return None

    def cancel_pending_order(self, ticket):
        """Cancel a pending order by ticket."""
        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": int(ticket),
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"Cancelled pending order {ticket}")
            return True
        return False

    def get_bot_positions(self):
        """Get open positions placed by this bot (matching magic number)."""
        positions = self.connection.get_open_positions()
        return [p for p in positions if p.magic == self.magic]

    def get_bot_pending_orders(self):
        """Get pending orders placed by this bot."""
        orders = self.connection.get_pending_orders()
        return [o for o in orders if o.magic == self.magic]

    def has_active_order_or_position(self):
        """Check if we already have an order or position open."""
        return (
            len(self.get_bot_positions()) > 0 or len(self.get_bot_pending_orders()) > 0
        )

    # ================================================================
    # HELPERS
    # ================================================================

    def _round_volume(self, volume, symbol_info):
        """Round volume to broker's lot step."""
        step = symbol_info.volume_step if symbol_info else 0.01
        if step <= 0:
            step = 0.01
        rounded = round(round(volume / step) * step, 2)
        min_vol = symbol_info.volume_min if symbol_info else config.MIN_LOT
        max_vol = symbol_info.volume_max if symbol_info else config.MAX_LOT
        return max(min_vol, min(max_vol, rounded))

    def _get_filling_mode(self, symbol_info):
        """Get appropriate filling mode."""
        if symbol_info is None:
            return mt5.ORDER_FILLING_IOC
        mode = getattr(symbol_info, "filling_mode", 0)
        if mode & mt5.ORDER_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_IOC

    def _log_error(self, retcode):
        """Print human-readable error for common MT5 return codes."""
        errors = {
            10019: "NO MONEY - margin insufficient",
            10014: "INVALID VOLUME - check lot limits",
            10017: "TRADING DISABLED - symbol unavailable",
            10016: "INVALID STOPS - SL/TP too close or wrong side",
            10006: "REJECTED - broker rejected the order",
            10015: "INVALID PRICE",
            10013: "INVALID REQUEST",
        }
        msg = errors.get(retcode, f"Unknown error code: {retcode}")
        print(f"  → {msg}")
