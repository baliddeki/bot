"""
Configuration for OB + FVG Trading Bot (XAUUSD)
================================================
All settings in one place. Edit here, not in other files.
"""

from datetime import datetime

# ============================================================
# MT5 CONNECTION
# ============================================================
MT5_TERMINAL_PATH = (
    None  # Set to your MT5 path if needed, e.g. "C:/Program Files/MT5/terminal64.exe"
)

# ============================================================
# SYMBOL
# ============================================================
SYMBOL = "XAUUSD"

# Symbol-specific values (XAUUSD / Gold)
PIP_SIZE = 0.1  # 1 pip = 0.1 points for gold
PIP_VALUE_PER_LOT = 10.0  # $10 per pip per standard lot (broker-dependent)
MIN_LOT = 0.01
MAX_LOT = 100.0
POINT = 0.01  # Smallest price increment

# Some brokers name gold differently
SYMBOL_ALIASES = ["XAUUSD", "GOLD", "XAUUSDm", "XAUUSD.a", "Gold"]

# ============================================================
# TIMEFRAMES
# ============================================================
OB_TIMEFRAME = "H4"  # Order block detection
FVG_TIMEFRAMES = ["M3", "M5", "M15", "M30", "H1", "H2"]  # Scan order: lowest first

# ============================================================
# ORDER BLOCK RULES
# ============================================================
OB_LOOKBACK_CANDLES = 50  # How many 2H candles to scan for OBs
OB_MAX_AGE_CANDLES = 10  # Ignore OBs older than this many 2H candles

# ============================================================
# TRADE PARAMETERS (in pips, 1 pip = 0.1 points for gold)
# ============================================================
SL_PIPS = 100  # 12.0 points
TP1_PIPS = 400  # 30.0 points  (close 80%)
TP1_CLOSE_PERCENT = 80  # Close 80% at TP1
BE_OFFSET_PIPS = 20  # 2.0 points   (move SL to entry + this)
TP2_PIPS = 1000  # 100.0 points (remaining 20%)

# Limit order expiry (hours) - cancel if not filled
LIMIT_ORDER_EXPIRY_HOURS = 48

# ============================================================
# RISK MANAGEMENT (auto-detected from balance)
# ============================================================
# Account tiers
RISK_TIERS = {
    "small": {
        "max_balance": 1000,
        "risk_per_trade": 6.0,  # %
        "max_daily_loss": 12.0,  # %
        "description": "Small account (< $1,000)",
    },
    "standard": {
        "max_balance": float("inf"),
        "risk_per_trade": 2.0,
        "max_daily_loss": 4.0,
        "description": "Standard account (>= $1,000)",
    },
    "prop": {
        "max_balance": float("inf"),
        "risk_per_trade": 0.5,
        "max_daily_loss": 1.5,
        "description": "Prop firm account",
    },
}

# Set to "prop" to force prop firm mode, otherwise auto-detects small vs standard
ACCOUNT_MODE = "auto"  # "auto", "small", "standard", or "prop"

# ============================================================
# BOT SETTINGS
# ============================================================
CHECK_INTERVAL_SECONDS = 60  # How often to scan for signals
MAGIC_NUMBER = 20260325  # Unique ID for this bot's orders
DEVIATION = 20  # Max slippage in points

# ============================================================
# LOGGING
# ============================================================
LOG_DIRECTORY = "logs"
TRADE_LOG_FILE = "trade_log.xlsx"

# ============================================================
# BACKTESTING
# ============================================================
BACKTEST_INITIAL_BALANCE = 200
BACKTEST_DATE_FROM = datetime(2025, 6, 1)
BACKTEST_DATE_TO = datetime(2026, 3, 25)


# ============================================================
# HELPER FUNCTIONS
# ============================================================


def pips_to_points(pips):
    """Convert pips to price points for gold."""
    return pips * PIP_SIZE


def points_to_pips(points):
    """Convert price points to pips for gold."""
    return points / PIP_SIZE


def get_risk_tier(balance):
    """Auto-detect risk tier from balance."""
    if ACCOUNT_MODE == "prop":
        return RISK_TIERS["prop"]
    if ACCOUNT_MODE in ("small", "standard"):
        return RISK_TIERS[ACCOUNT_MODE]

    # Auto-detect
    if balance < 1000:
        return RISK_TIERS["small"]
    return RISK_TIERS["standard"]
