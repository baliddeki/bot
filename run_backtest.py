"""
Run Backtest
=============
Tests the OB + FVG strategy on historical data.
Requires MT5 to be running (for historical data).
"""

from datetime import datetime
from mt5_connection import MT5Connection
from backtester import Backtester
import config


def main():
    print("\n" + "=" * 60)
    print("  OB + FVG BACKTEST")
    print("=" * 60)
    print(f"Symbol:  {config.SYMBOL}")
    print(
        f"Period:  {config.BACKTEST_DATE_FROM.date()} to {config.BACKTEST_DATE_TO.date()}"
    )
    print(f"Balance: ${config.BACKTEST_INITIAL_BALANCE:.2f}")
    print("=" * 60)

    connection = MT5Connection()
    if not connection.connect():
        return

    try:
        bt = Backtester(connection)
        bt.run()
    finally:
        connection.disconnect()


if __name__ == "__main__":
    main()
