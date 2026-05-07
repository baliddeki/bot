"""
run_live.py — Main live trading loop.

Connects to OANDA for data and MT5 for execution,
then scans for signals on a configurable interval.

Usage:
    python run_live.py
"""

import signal as os_signal
import sys
import time

import pandas as pd

import config
from oanda_client import fetch_all_timeframes
from signal_generator import generate_signal
from trade_manager import TradeManager
from risk_manager import (
    is_daily_loss_limit_hit,
    is_open_risk_limit_hit,
    is_max_trades_reached,
)
import mt5_executor
from logger import log_event


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    _print_banner()

    # ── Connect to MT5 ─────────────────────────────────────────────────────
    if not mt5_executor.connect():
        log_event("Cannot connect to MT5. Is the terminal running? Exiting.", level="ERROR")
        sys.exit(1)

    # ── Show account summary and ask for confirmation ──────────────────────
    balance = mt5_executor.get_balance()
    profile = config.get_account_profile(balance)

    print(f"\n{'─' * 50}")
    print(f"  Account balance : ${balance:>12,.2f}")
    print(f"  Active profile  : {profile['description']}")
    print(f"  Risk per trade  : {profile['risk_xauusd_percent']}%  (XAUUSD)")
    print(f"  Max daily loss  : {profile['max_daily_loss_percent']}%")
    print(f"  Max open trades : {profile['max_concurrent_trades']}")
    print(f"  Symbol          : {config.SYMBOL}  →  {config.MT5_SYMBOL}")
    print(f"  Scan interval   : {config.CHECK_INTERVAL_SECONDS}s")
    print(f"{'─' * 50}\n")

    confirm = input("  Type  YES  to start live trading: ").strip().upper()
    if confirm != "YES":
        log_event("Live trading cancelled by user.")
        mt5_executor.disconnect()
        sys.exit(0)

    # ── Set up graceful shutdown on Ctrl+C ─────────────────────────────────
    running = {"active": True}
    def _on_shutdown(sig, frame):
        log_event("Shutdown signal received. Stopping bot after this cycle...")
        running["active"] = False
    os_signal.signal(os_signal.SIGINT,  _on_shutdown)
    os_signal.signal(os_signal.SIGTERM, _on_shutdown)

    # ── Main loop ──────────────────────────────────────────────────────────
    manager             = TradeManager()
    day_start_balance   = balance
    log_event(f"Bot live. Scanning every {config.CHECK_INTERVAL_SECONDS}s. Press Ctrl+C to stop.")

    while running["active"]:
        try:
            balance = mt5_executor.get_balance()
            profile = config.get_account_profile(balance)

            # ── Daily loss limit ───────────────────────────────────────────
            if is_daily_loss_limit_hit(day_start_balance, balance, profile):
                log_event(
                    f"Daily loss limit reached "
                    f"({profile['max_daily_loss_percent']}%). "
                    "No new trades until tomorrow.",
                    level="WARNING",
                )
                _manage_then_sleep(manager, None, None, balance, running)
                continue

            # ── Max concurrent trades ──────────────────────────────────────
            if is_max_trades_reached(len(manager.open_trades), balance, profile):
                log_event(
                    f"Max concurrent trades ({profile['max_concurrent_trades']}) reached. "
                    "Managing open trades only."
                )
                _manage_then_sleep(manager, None, None, balance, running)
                continue

            # ── Fetch fresh data ───────────────────────────────────────────
            log_event("Fetching candle data...")
            candle_data  = fetch_all_timeframes(config.SYMBOL)
            current_time = pd.Timestamp.now(tz="UTC")

            # ── Manage existing trades ─────────────────────────────────────
            manager.on_candle_close(candle_data, balance)

            # ── Open risk check before looking for new signals ─────────────
            if is_open_risk_limit_hit(manager.total_open_risk, balance, profile):
                log_event(
                    f"Open risk limit ({profile['max_open_risk_percent']}%) reached. "
                    "Skipping new signal scan."
                )
                _interruptible_sleep(config.CHECK_INTERVAL_SECONDS, running)
                continue

            # ── Scan for new signal ────────────────────────────────────────
            signal = generate_signal(candle_data, current_time)

            if signal:
                log_event(
                    f"Signal detected: {signal.direction} {signal.trade_type} | "
                    f"Swept {signal.swept_tf} swing @ {signal.swept_swing.price:.2f} | "
                    f"OB on {signal.ob_tf} | FVG on {signal.fvg_tf} | "
                    f"Entry {signal.entry:.2f} | SL {signal.sl:.2f} | TP1 {signal.tp1}"
                )
                manager.open_trade(signal, candle_data, balance)
            else:
                log_event("No valid setup found this cycle.")

        except KeyboardInterrupt:
            running["active"] = False
            break

        except Exception as exc:
            log_event(f"Unexpected error: {exc}", level="ERROR")
            import traceback
            traceback.print_exc()

        _interruptible_sleep(config.CHECK_INTERVAL_SECONDS, running)

    # ── Cleanup ────────────────────────────────────────────────────────────
    log_event(f"Bot stopped. {len(manager.open_trades)} trade(s) still open in MT5.")
    mt5_executor.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _manage_then_sleep(
    manager:     TradeManager,
    candle_data,
    current_time,
    balance:     float,
    running:     dict,
):
    """Fetch data, run trade management, then sleep."""
    if candle_data is None:
        candle_data = fetch_all_timeframes(config.SYMBOL)
    manager.on_candle_close(candle_data, balance)
    _interruptible_sleep(config.CHECK_INTERVAL_SECONDS, running)


def _interruptible_sleep(seconds: int, running: dict):
    """Sleep in 1-second increments so Ctrl+C is responsive."""
    for _ in range(seconds):
        if not running["active"]:
            break
        time.sleep(1)


def _print_banner():
    print("\n" + "═" * 50)
    print("   XAUUSD Gold Trading Bot")
    print("   OANDA data  ·  MT5 execution")
    print("═" * 50)
    log_event("Bot initialising...")


if __name__ == "__main__":
    main()
