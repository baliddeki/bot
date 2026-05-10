"""
config.py — Central configuration for the XAUUSD Gold Trading Bot.

All strategy parameters, risk settings, and account modes are defined here.
To customise the bot, change values in this file only — no code changes needed.
"""

from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENT
# ─────────────────────────────────────────────────────────────────────────────

SYMBOL        = "frxXAUUSD"   # Deriv symbol for Gold
MT5_SYMBOL    = "XAUUSDm"     # MT5 symbol (varies per broker: XAUUSD, XAUUSDm, etc.)
PIP_SIZE      = 0.10          # 1 pip = $0.10 for Gold


# ─────────────────────────────────────────────────────────────────────────────
# DERIV API  (data source + optional execution)
# ─────────────────────────────────────────────────────────────────────────────

DERIV_APP_ID   = ""           # Register a free app at https://api.deriv.com/
DERIV_API_TOKEN = ""          # Generated from your Deriv account (for execution)
DERIV_WS_URL   = "wss://ws.binaryws.com/websockets/v3"


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION BROKER
# Controls which executor places and manages trades.
#
#   "mt5"               — MetaTrader 5 (any broker: Deriv MT5, IC Markets, etc.)
#   "deriv_multipliers" — Deriv's native Multipliers product via WebSocket
#
# To use Deriv's own MT5 server, set EXECUTION_BROKER = "mt5" and point
# your MT5 terminal at Deriv's MT5 gateway. No code changes needed.
# ─────────────────────────────────────────────────────────────────────────────

EXECUTION_BROKER = "deriv_multipliers"   # "mt5" | "deriv_multipliers"


# ─────────────────────────────────────────────────────────────────────────────
# MT5 SETTINGS  (only used when EXECUTION_BROKER = "mt5")
# ─────────────────────────────────────────────────────────────────────────────

MT5_LOGIN    = 0      # Account number
MT5_PASSWORD = ""     # Account password
MT5_SERVER   = ""     # Broker server name (e.g. "Deriv-Server", "ICMarkets-Live01")


# ─────────────────────────────────────────────────────────────────────────────
# DERIV MULTIPLIERS SETTINGS  (only used when EXECUTION_BROKER = "deriv_multipliers")
# Multipliers are Deriv's leveraged CFD-style product with SL/TP support.
# ─────────────────────────────────────────────────────────────────────────────

DERIV_MULTIPLIER        = 100    # Leverage multiplier (10, 20, 50, 100, 200, 500)
DERIV_COMMISSION_PCT    = 0.05   # Commission per trade as % of stake (check your account)
DERIV_STOP_OUT_LEVEL    = 0.10   # Position force-closed if equity drops to this % of stake


# ─────────────────────────────────────────────────────────────────────────────
# TIMEFRAMES
# Maps internal labels → Deriv granularity in seconds.
# W1 and MN are not native on Deriv — resampled from D1 candles.
# ─────────────────────────────────────────────────────────────────────────────

TIMEFRAMES = {
    "M3":  180,
    "M5":  300,
    "M15": 900,
    "H1":  3600,
    "H2":  7200,
    "H4":  14400,
    "D1":  86400,
    "W1":  None,   # Resampled from D1
    "MN":  None,   # Resampled from D1
}

# How many candles to fetch per timeframe on each scan cycle
CANDLE_HISTORY = {
    "M3":  500,
    "M5":  500,
    "M15": 500,
    "H1":  300,
    "H2":  300,
    "H4":  200,
    "D1":  200,    # Fetch extra D1 to cover W1/MN resampling
    "W1":  52,     # Target candle count after resampling
    "MN":  24,     # Target candle count after resampling
}


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — LIQUIDATION SCANNING
# ─────────────────────────────────────────────────────────────────────────────

LIQUIDATION_TIMEFRAMES = ["MN", "W1", "D1", "H4"]
SWEEP_SCAN_LOOKBACK    = 50
SWEEP_RECENCY_CANDLES  = 5


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — ORDER BLOCK TIMEFRAME RULES
# ─────────────────────────────────────────────────────────────────────────────

OB_TIMEFRAME_RULES = {
    "MN": ["W1", "D1"],
    "W1": ["W1", "D1"],
    "D1": ["D1", "H4", "H2"],
    "H4": ["H4", "H2", "H1"],
}


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — FVG SEARCH ORDER
# ─────────────────────────────────────────────────────────────────────────────

FVG_SEARCH_ORDER = ["M3", "M5", "M15", "H1", "H2", "H4", "D1"]


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — TRADE TYPE CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

INTRADAY_SWEPT_TIMEFRAMES = ["H4"]
SWING_SWEPT_TIMEFRAMES    = ["D1", "W1", "MN"]


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — STOP LOSS  (fixed for all trades)
# ─────────────────────────────────────────────────────────────────────────────

SL_PIPS = 100


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — INTRADAY TAKE PROFIT
# ─────────────────────────────────────────────────────────────────────────────

INTRADAY_TP1_PIPS          = 150
INTRADAY_TP1_CLOSE_PERCENT = 50
INTRADAY_TP2_PIPS          = 250


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — SWING TAKE PROFIT
# ─────────────────────────────────────────────────────────────────────────────

SWING_TP_DYNAMIC = True


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — SWING RE-ENTRY
# ─────────────────────────────────────────────────────────────────────────────

SWING_REENTRY_ENABLED       = True
SWING_REENTRY_PERMITTED_TFS = ["H4", "H2"]
SWING_REENTRY_MAX_ENTRIES   = 2


# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT — ACCOUNT PROFILES
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT_MODE                 = "auto"
AUTO_SMALL_ACCOUNT_THRESHOLD = 500

ACCOUNT_PROFILES = {

    "PROP": {
        "description":            "Prop firm — ultra conservative",
        "risk_per_trade_percent": 1.0,
        "risk_xauusd_percent":    1.5,
        "max_daily_loss_percent": 3.5,
        "max_open_risk_percent":  2.5,
        "max_concurrent_trades":  2,
    },

    "LIVE_SMALL": {
        "description":            "Small personal account (<$500) — aggressive",
        "risk_per_trade_percent": 6.0,
        "risk_xauusd_percent":    8.0,
        "max_daily_loss_percent": 15.0,
        "max_open_risk_percent":  15.0,
        "max_concurrent_trades":  3,
    },

    "LIVE_BIG": {
        "description":            "Standard personal account (≥$500) — moderate",
        "risk_per_trade_percent": 2.0,
        "risk_xauusd_percent":    5.0,
        "max_daily_loss_percent": 10.0,
        "max_open_risk_percent":  10.0,
        "max_concurrent_trades":  3,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT — POSITION SIZING HARD LIMITS
# ─────────────────────────────────────────────────────────────────────────────

MIN_LOT_SIZE = 0.01
MAX_LOT_SIZE = 10.0
LOT_STEP     = 0.01

# Deriv Multipliers: min/max stake in USD
DERIV_MIN_STAKE = 1.0
DERIV_MAX_STAKE = 2000.0


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION — HYBRID ORDER LOGIC  (only applies to MT5 executor)
# ─────────────────────────────────────────────────────────────────────────────

MARKET_ORDER_MAX_DISTANCE_PIPS = 20
LIMIT_ORDER_MAX_DISTANCE_PIPS  = 80
LIMIT_ORDER_EXPIRY_HOURS       = 8


# ─────────────────────────────────────────────────────────────────────────────
# BOT OPERATION
# ─────────────────────────────────────────────────────────────────────────────

CHECK_INTERVAL_SECONDS = 60
MAGIC_NUMBER           = 20260506   # MT5 only
DEVIATION_POINTS       = 20         # MT5 only


# ─────────────────────────────────────────────────────────────────────────────
# CHART GENERATION
# ─────────────────────────────────────────────────────────────────────────────

CHART_OUTPUT_DIR     = "charts"
CHART_CANDLES_BEFORE = 30
CHART_CANDLES_AFTER  = 20
CHART_TIMEFRAME      = "H1"


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIRECTORY  = "logs"
TRADE_LOG_FILE = "trades.csv"
EVENT_LOG_FILE = "events.log"


# ─────────────────────────────────────────────────────────────────────────────
# BACKTESTING
# ─────────────────────────────────────────────────────────────────────────────

BACKTEST_INITIAL_BALANCE = 10_000
BACKTEST_DATE_FROM       = datetime(2024, 1, 1)
BACKTEST_DATE_TO         = datetime(2025, 1, 1)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def pips_to_price(pips: float) -> float:
    """Convert a pip count to a gold price movement."""
    return pips * PIP_SIZE


def price_to_pips(price_move: float) -> float:
    """Convert a gold price movement to pips."""
    return price_move / PIP_SIZE


def get_account_profile(balance: float) -> dict:
    """
    Return the active account profile dict based on ACCOUNT_MODE and balance.
    Always includes a 'mode' key indicating which profile is active.
    """
    if ACCOUNT_MODE == "auto":
        mode = "LIVE_SMALL" if balance < AUTO_SMALL_ACCOUNT_THRESHOLD else "LIVE_BIG"
    else:
        mode = ACCOUNT_MODE

    if mode not in ACCOUNT_PROFILES:
        raise ValueError(
            f"Unknown account mode: {mode!r}. "
            f"Valid options: {list(ACCOUNT_PROFILES.keys())}"
        )

    profile = ACCOUNT_PROFILES[mode].copy()
    profile["mode"] = mode
    return profile
