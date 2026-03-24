"""
MT5 Connection
==============
Handles connecting to MetaTrader 5 and fetching data.
"""

import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd
import config


# Map our timeframe strings to MT5 constants
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


class MT5Connection:
    def __init__(self):
        self.connected = False
        self.real_symbol = None  # The actual symbol name on the broker

    def connect(self):
        """Initialize MT5 and find the correct symbol name."""
        kwargs = {}
        if config.MT5_TERMINAL_PATH:
            kwargs["path"] = config.MT5_TERMINAL_PATH

        if not mt5.initialize(**kwargs):
            print(f"MT5 init failed: {mt5.last_error()}")
            return False

        # Find the correct symbol name on this broker
        self.real_symbol = self._resolve_symbol()
        if not self.real_symbol:
            print(f"Could not find symbol matching {config.SYMBOL}")
            mt5.shutdown()
            return False

        # Make sure symbol is visible in Market Watch
        if not mt5.symbol_select(self.real_symbol, True):
            print(f"Failed to select {self.real_symbol} in Market Watch")
            mt5.shutdown()
            return False

        self.connected = True
        info = mt5.account_info()
        print(f"Connected to MT5: {info.server}")
        print(f"Account: {info.login} | Balance: ${info.balance:.2f}")
        print(f"Symbol: {self.real_symbol}")
        return True

    def disconnect(self):
        """Shutdown MT5."""
        mt5.shutdown()
        self.connected = False
        print("MT5 disconnected")

    def _resolve_symbol(self):
        """Find the actual broker symbol name for XAUUSD."""
        # Try exact name first
        info = mt5.symbol_info(config.SYMBOL)
        if info is not None:
            return config.SYMBOL

        # Try aliases
        for alias in config.SYMBOL_ALIASES:
            info = mt5.symbol_info(alias)
            if info is not None:
                print(f"Using symbol alias: {alias}")
                return alias

        # Search all symbols
        all_symbols = mt5.symbols_get()
        if all_symbols:
            for s in all_symbols:
                name = s.name.upper()
                if "XAU" in name and "USD" in name:
                    print(f"Found symbol by search: {s.name}")
                    return s.name

        return None

    def get_candles(self, timeframe_str, count=100, from_time=None, to_time=None):
        """
        Fetch candles from MT5.
        Returns a DataFrame with columns: time, open, high, low, close, volume
        """
        tf = TIMEFRAME_MAP.get(timeframe_str)
        if tf is None:
            print(f"Unknown timeframe: {timeframe_str}")
            return None

        if from_time and to_time:
            rates = mt5.copy_rates_range(self.real_symbol, tf, from_time, to_time)
        elif from_time:
            rates = mt5.copy_rates_from(self.real_symbol, tf, from_time, count)
        else:
            rates = mt5.copy_rates_from_pos(self.real_symbol, tf, 0, count)

        if rates is None or len(rates) == 0:
            return None

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df[["time", "open", "high", "low", "close", "tick_volume"]].copy()
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        return df

    def get_candles_in_window(self, timeframe_str, start_time, end_time):
        """Fetch candles within a specific time window."""
        return self.get_candles(timeframe_str, from_time=start_time, to_time=end_time)

    def get_balance(self):
        """Get current account balance."""
        info = mt5.account_info()
        return info.balance if info else 0.0

    def get_equity(self):
        """Get current account equity."""
        info = mt5.account_info()
        return info.equity if info else 0.0

    def get_account_info(self):
        """Get full account info dict."""
        info = mt5.account_info()
        if not info:
            return {}
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "login": info.login,
            "server": info.server,
            "leverage": info.leverage,
        }

    def get_current_price(self):
        """Get current bid/ask."""
        tick = mt5.symbol_info_tick(self.real_symbol)
        if not tick:
            return None, None
        return tick.bid, tick.ask

    def get_symbol_info(self):
        """Get symbol info (for lot sizes, etc)."""
        return mt5.symbol_info(self.real_symbol)

    def get_open_positions(self):
        """Get all open positions for our symbol."""
        positions = mt5.positions_get(symbol=self.real_symbol)
        return list(positions) if positions else []

    def get_pending_orders(self):
        """Get all pending orders."""
        orders = mt5.orders_get()
        return list(orders) if orders else []

    def get_todays_closed_trades(self):
        """Get trades closed today (for daily P&L tracking)."""
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        deals = mt5.history_deals_get(today_start, datetime.now())
        if not deals:
            return []
        return [
            d for d in deals if d.symbol == self.real_symbol and d.entry == 1
        ]  # entry=1 means exit deal
