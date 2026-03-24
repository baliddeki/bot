"""
Live Trading Bot
=================
Main loop that:
1. Scans for OB + FVG signals on 2H
2. Places limit orders
3. Manages open positions (partial close, SL to BE)
4. Tracks daily P&L for risk limits
5. Logs everything to Excel

Usage:
    python run_live.py
"""

import time
from datetime import datetime
import config
from mt5_connection import MT5Connection
from signal_engine import scan_for_signal, format_signal
from risk_manager import RiskManager
from executor import TradeExecutor
from trade_manager import TradeManager
from trade_logger import TradeLogger


class LiveBot:
    def __init__(self):
        self.connection = MT5Connection()
        self.risk_manager = RiskManager()
        self.executor = None
        self.trade_manager = None
        self.logger = None
        self.running = False

        # Track which OBs we've already traded (avoid duplicates)
        # Key = (ob_type, candle_c_time)
        self.traded_obs = set()

    def start(self):
        """Connect and start the main loop."""
        if not self.connection.connect():
            return

        self.executor = TradeExecutor(self.connection)
        self.trade_manager = TradeManager(self.executor)
        self.logger = TradeLogger()

        balance = self.connection.get_balance()
        tier = config.get_risk_tier(balance)

        print(f"\n{'='*60}")
        print(f"OB + FVG BOT STARTED")
        print(f"{'='*60}")
        print(f"Symbol:    {config.SYMBOL}")
        print(f"Balance:   ${balance:.2f}")
        print(f"Tier:      {tier['description']}")
        print(f"Risk:      {tier['risk_per_trade']}% per trade")
        print(f"Max Daily: {tier['max_daily_loss']}% loss")
        print(
            f"SL:        {config.SL_PIPS} pips ({config.pips_to_points(config.SL_PIPS)} pts)"
        )
        print(f"TP1:       {config.TP1_PIPS} pips (close {config.TP1_CLOSE_PERCENT}%)")
        print(f"TP2:       {config.TP2_PIPS} pips (remaining)")
        print(f"Check every {config.CHECK_INTERVAL_SECONDS}s")
        print(f"{'='*60}\n")

        self.running = True
        self._main_loop()

    def stop(self):
        """Stop the bot."""
        self.running = False
        print("\nBot stopping...")
        self.connection.disconnect()

    def _main_loop(self):
        """Main trading loop."""
        try:
            while self.running:
                self._cycle()
                time.sleep(config.CHECK_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nKeyboard interrupt received")
        finally:
            self.stop()

    def _cycle(self):
        """One cycle of the bot: check positions, scan for signals."""
        now = datetime.now().strftime("%H:%M:%S")
        balance = self.connection.get_balance()

        # Step 1: Update daily P&L
        self.risk_manager.update_daily_pnl_from_mt5(self.connection)

        # Step 2: Manage existing positions (check for TP1 hits)
        actions = self.trade_manager.check_positions()
        for action in actions:
            print(f"  [MANAGE] {action}")

        # Clean up expired pending orders
        cancelled = self.trade_manager.cleanup_expired_pending()
        if cancelled > 0:
            print(f"  [CLEANUP] Cancelled {cancelled} expired orders")

        # Step 3: Check if we can trade
        can_trade, reason = self.risk_manager.can_trade(balance)
        if not can_trade:
            print(f"[{now}] Cannot trade: {reason}")
            return

        # Step 4: Skip if we already have an active position/order
        if self.executor.has_active_order_or_position():
            status = self.trade_manager.get_status()
            print(
                f"[{now}] Active: {status['open_positions']} positions, "
                f"{status['pending_orders']} pending"
            )
            return

        # Step 5: Scan for new signal
        print(f"[{now}] Scanning... (balance: ${balance:.2f})")
        signal = scan_for_signal(self.connection)

        if signal is None:
            print(f"[{now}] No signal")
            return

        # Step 6: Check if we already traded this OB
        ob_key = (signal["ob"]["type"], str(signal["ob"]["candle_c_time"]))
        if ob_key in self.traded_obs:
            print(f"[{now}] Already traded this OB, skipping")
            return

        # Step 7: Calculate lot size
        lot_size, risk_amount, tier = self.risk_manager.calculate_lot_size(balance)
        risk_pct = tier["risk_per_trade"]

        print(f"\n{format_signal(signal)}")
        print(f"  Lot: {lot_size} | Risk: ${risk_amount:.2f} ({risk_pct}%)")

        # Step 8: Place limit order
        result = self.executor.place_limit_order(signal, lot_size)
        if result is None:
            self.logger.log_rejection(signal, "Order placement failed")
            return

        # Step 9: Log and track
        self.logger.log_signal(
            signal, lot_size, risk_amount, risk_pct, balance, notes=signal["reason"]
        )
        self.traded_obs.add(ob_key)

        print(f"  Order placed! Ticket: {result.order}")

    def _check_for_fills(self):
        """
        Check if any pending orders have been filled.
        Called in the management phase.
        """
        # This is handled by trade_manager.check_positions()
        # When a limit order fills, it becomes a position automatically in MT5
        pass
