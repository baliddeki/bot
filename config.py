"""
config.py — Central configuration for the XAUUSD Gold Trading Bot.

All strategy parameters, risk settings, and account modes are defined here.
To customise the bot, change values in this file only — no code changes needed.
"""

from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENT
# ─────────────────────────────────────────────────────────────────────────────

SYMBOL     = "XAU_USD"    # OANDA instrument code
MT5_SYMBOL = "XAUUSDm"   # MT5 symbol (varies per broker: XAUUSD, XAUUSDm, etc.)
PIP_SIZE   = 0.10         # 1 pip = $0.10 for Gold


# ─────────────────────────────────────────────────────────────────────────────
# OANDA API  (data source — never used for execution)
# ─────────────────────────────────────────────────────────────────────────────

OANDA_API_KEY      = ""           # Set here or via env var OANDA_API_KEY
OANDA_ACCOUNT_ID   = ""           # Your OANDA account ID
OANDA_ENVIRONMENT  = "practice"   # "practice" (demo) or "live"
OANDA_CANDLE_LIMIT = 5000         # Max candles per single OANDA request


# ─────────────────────────────────────────────────────────────────────────────
# TIMEFRAMES
# Maps our internal labels to OANDA granularity strings.
# ─────────────────────────────────────────────────────────────────────────────

TIMEFRAMES = {
    "M3":  "M3",
    "M5":  "M5",
    "M15": "M15",
    "H1":  "H1",
    "H2":  "H2",
    "H4":  "H4",
    "D1":  "D",
    "W1":  "W",
    "MN":  "M",
}

# How many candles to fetch per timeframe on each scan cycle
CANDLE_HISTORY = {
    "M3":  500,
    "M5":  500,
    "M15": 500,
    "H1":  300,
    "H2":  300,
    "H4":  200,
    "D1":  100,
    "W1":  52,
    "MN":  24,
}


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — LIQUIDATION SCANNING
# Timeframes monitored for swing highs/lows that may be swept.
# ─────────────────────────────────────────────────────────────────────────────

LIQUIDATION_TIMEFRAMES = ["MN", "W1", "D1", "H4"]

# How many recent candles to scan on each TF when looking for sweeps
SWEEP_SCAN_LOOKBACK = 50

# How many candles back a sweep is still considered "recent" (not stale)
SWEEP_RECENCY_CANDLES = 5


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — ORDER BLOCK TIMEFRAME RULES
# For each swept TF, which TFs are valid for OB identification.
# ─────────────────────────────────────────────────────────────────────────────

OB_TIMEFRAME_RULES = {
    "MN": ["W1", "D1"],
    "W1": ["W1", "D1"],
    "D1": ["D1", "H4", "H2"],
    "H4": ["H4", "H2", "H1"],
}


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — FVG SEARCH ORDER
# Searched lowest-to-highest timeframe for FVG within the OB zone.
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
# Two-stage exit: partial close at TP1, full close at TP2.
# ─────────────────────────────────────────────────────────────────────────────

INTRADAY_TP1_PIPS          = 150   # Close this % of the position at TP1
INTRADAY_TP1_CLOSE_PERCENT = 50    # Percentage to close at TP1
INTRADAY_TP2_PIPS          = 250   # Close remaining position at TP2


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — SWING TAKE PROFIT
# TP is the nearest swing high/low on the same TF as the swept level.
# Can be updated dynamically as new qualifying swings form during the trade.
# ─────────────────────────────────────────────────────────────────────────────

SWING_TP_DYNAMIC = True   # Set False to lock TP at signal time and never update


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY — SWING RE-ENTRY
# Allow additional entries when new OBs form between price and target.
# ─────────────────────────────────────────────────────────────────────────────

SWING_REENTRY_ENABLED       = True
SWING_REENTRY_PERMITTED_TFS = ["H4", "H2"]  # TFs where new OBs are watched
SWING_REENTRY_MAX_ENTRIES   = 2             # Max additional entries per swing setup


# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGEMENT — ACCOUNT PROFILES
# Each profile sets risk limits for a specific trading context.
# ─────────────────────────────────────────────────────────────────────────────

# Set ACCOUNT_MODE to "auto" to select profile by balance,
# or force a specific profile: "PROP", "LIVE_SMALL", "LIVE_BIG"
ACCOUNT_MODE = "auto"

# Balance below this threshold → LIVE_SMALL profile (auto mode only)
AUTO_SMALL_ACCOUNT_THRESHOLD = 500

ACCOUNT_PROFILES = {

    # ── Prop firm challenges ──────────────────────────────────────────────
    "PROP": {
        "description":            "Prop firm — ultra conservative",
        "risk_per_trade_percent": 1.0,    # Base risk for non-gold pairs
        "risk_xauusd_percent":    1.5,    # Gold-specific risk
        "max_daily_loss_percent": 3.5,    # Stop trading if daily loss hits this
        "max_open_risk_percent":  2.5,    # Max combined open risk at any time
        "max_concurrent_trades":  2,
    },

    # ── Small personal account (< $500) ──────────────────────────────────
    "LIVE_SMALL": {
        "description":            "Small personal account (<$500) — aggressive",
        "risk_per_trade_percent": 6.0,
        "risk_xauusd_percent":    8.0,
        "max_daily_loss_percent": 15.0,
        "max_open_risk_percent":  15.0,
        "max_concurrent_trades":  3,
    },

    # ── Standard personal account (≥ $500) ───────────────────────────────
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
# Applied regardless of account profile.
# ─────────────────────────────────────────────────────────────────────────────

MIN_LOT_SIZE = 0.01
MAX_LOT_SIZE = 10.0
LOT_STEP     = 0.01


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION — HYBRID ORDER LOGIC  (Market vs Limit)
# ─────────────────────────────────────────────────────────────────────────────

MARKET_ORDER_MAX_DISTANCE_PIPS = 20   # ≤ this → market order
LIMIT_ORDER_MAX_DISTANCE_PIPS  = 80   # ≤ this → limit order; beyond → skip signal
LIMIT_ORDER_EXPIRY_HOURS       = 8    # Cancel unfilled limit orders after this


# ─────────────────────────────────────────────────────────────────────────────
# BOT OPERATION
# ─────────────────────────────────────────────────────────────────────────────

CHECK_INTERVAL_SECONDS = 60       # Scan frequency
MAGIC_NUMBER           = 20260506 # Unique MT5 order identifier for this bot
DEVIATION_POINTS       = 20       # Max slippage on market orders (MT5 points)


# ─────────────────────────────────────────────────────────────────────────────
# CHART GENERATION
# ─────────────────────────────────────────────────────────────────────────────

CHART_OUTPUT_DIR     = "charts"
CHART_CANDLES_BEFORE = 30      # Candles of context before entry in the "before" panel
CHART_CANDLES_AFTER  = 20      # Candles of context after entry in the "after" panel
CHART_TIMEFRAME      = "H1"    # Timeframe used for chart candlesticks


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
        raise ValueError(f"Unknown account mode: {mode!r}. "
                         f"Valid options: {list(ACCOUNT_PROFILES.keys())}")

    profile = ACCOUNT_PROFILES[mode].copy()
    profile["mode"] = mode
    return profile
