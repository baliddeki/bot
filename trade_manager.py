"""
Trade Manager
==============
Monitors open positions and manages:
1. Partial close at TP1 (80%)
2. Move SL to breakeven + offset after TP1
3. Track which positions have already been partially closed

Runs on each bot cycle to check positions.
"""

import config


class TradeManager:
    def __init__(self, executor):
        """
        Args:
            executor: TradeExecutor instance
        """
        self.executor = executor

        # Track tickets that already had TP1 partial close
        # So we don't try to close them again
        self.tp1_closed_tickets = set()

    def check_positions(self):
        """
        Main method: check all open positions and manage them.
        Call this on every bot cycle.

        Returns:
            List of actions taken (for logging)
        """
        positions = self.executor.get_bot_positions()
        actions = []

        for pos in positions:
            action = self._manage_position(pos)
            if action:
                actions.append(action)

        return actions

    def _manage_position(self, position):
        """
        Check a single position for TP1 hit.

        Logic:
        - If position hasn't had TP1 partial close yet
        - And current price has reached TP1 level
        - Then close 80% and move SL to BE + offset
        """
        ticket = position.ticket

        # Already handled TP1 for this ticket?
        if ticket in self.tp1_closed_tickets:
            return None

        # Get current price
        bid, ask = self.executor.connection.get_current_price()
        if bid is None:
            return None

        entry_price = position.price_open
        current_price = bid if position.type == 0 else ask  # 0=BUY, 1=SELL

        tp1_points = config.pips_to_points(config.TP1_PIPS)
        be_offset_points = config.pips_to_points(config.BE_OFFSET_PIPS)
        tp2_points = config.pips_to_points(config.TP2_PIPS)

        # Check if TP1 level reached
        tp1_hit = False
        if position.type == 0:  # BUY
            tp1_price = entry_price + tp1_points
            tp1_hit = current_price >= tp1_price
        else:  # SELL
            tp1_price = entry_price - tp1_points
            tp1_hit = current_price <= tp1_price

        if not tp1_hit:
            return None

        # TP1 hit! Close 80%
        print(f"\nTP1 HIT on ticket {ticket} at {current_price}")
        result = self.executor.close_partial(position, config.TP1_CLOSE_PERCENT)

        if result is None:
            print(f"Failed to close partial on {ticket}")
            return None

        self.tp1_closed_tickets.add(ticket)

        # Move SL to breakeven + offset
        if position.type == 0:  # BUY
            new_sl = entry_price + be_offset_points
            new_tp = entry_price + tp2_points
        else:  # SELL
            new_sl = entry_price - be_offset_points
            new_tp = entry_price - tp2_points

        self.executor.modify_sl(position, round(new_sl, 2))

        # Also update TP to TP2 for the remaining position
        self._update_tp(position, round(new_tp, 2), round(new_sl, 2))

        action_desc = (
            f"TP1 partial close on {ticket}: "
            f"closed {config.TP1_CLOSE_PERCENT}%, "
            f"SL moved to {new_sl:.2f}, TP moved to {new_tp:.2f}"
        )
        print(f"  {action_desc}")
        return action_desc

    def _update_tp(self, position, new_tp, new_sl):
        """Update both SL and TP on remaining position."""
        import MetaTrader5 as mt5

        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.executor.connection.real_symbol,
            "position": position.ticket,
            "sl": float(new_sl),
            "tp": float(new_tp),
            "magic": config.MAGIC_NUMBER,
        }

        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            print(f"  TP updated to {new_tp} on ticket {position.ticket}")
        else:
            print(f"  TP update failed on {position.ticket}")

    def cleanup_expired_pending(self):
        """
        Cancel pending orders that have expired.
        MT5 usually handles this, but we double-check.
        """
        orders = self.executor.get_bot_pending_orders()
        cancelled = 0
        for order in orders:
            # If order has an expiry time and it's passed
            if hasattr(order, "time_expiration") and order.time_expiration > 0:
                import time

                if time.time() > order.time_expiration:
                    self.executor.cancel_pending_order(order.ticket)
                    cancelled += 1
        return cancelled

    def get_status(self):
        """Get summary of managed positions."""
        positions = self.executor.get_bot_positions()
        pending = self.executor.get_bot_pending_orders()

        return {
            "open_positions": len(positions),
            "pending_orders": len(pending),
            "tp1_closed_tickets": len(self.tp1_closed_tickets),
        }
