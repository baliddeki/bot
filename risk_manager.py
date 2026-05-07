"""
risk_manager.py — Position sizing and risk limit enforcement.

All calculations are based on the active account profile from config.py.
Change profile settings in config.py — no code changes needed here.
"""

from typing import Optional
import config


# ─────────────────────────────────────────────────────────────────────────────
# Position sizing
# ─────────────────────────────────────────────────────────────────────────────

# For XAUUSD: 1 standard lot = 100 oz. Each pip ($0.10) move = $10 per lot.
_PIP_VALUE_PER_LOT = 10.0


def calculate_lot_size(
    balance:  float,
    entry:    float,
    sl:       float,
    profile:  Optional[dict] = None,
) -> float:
    """
    Calculate the correct lot size for a XAUUSD trade.

    Formula:
        risk_amount  = balance × risk_xauusd_percent / 100
        pip_distance = |entry - sl| / pip_size
        lot_size     = risk_amount / (pip_distance × pip_value_per_lot)

    Args:
        balance: Current account balance in USD.
        entry:   Planned entry price.
        sl:      Planned stop loss price.
        profile: Account profile dict. If None, auto-detected from balance.

    Returns:
        Lot size rounded to LOT_STEP and clamped to [MIN_LOT_SIZE, MAX_LOT_SIZE].
    """
    if profile is None:
        profile = config.get_account_profile(balance)

    risk_amount  = balance * (profile["risk_xauusd_percent"] / 100)
    pip_distance = abs(entry - sl) / config.PIP_SIZE

    if pip_distance == 0:
        return config.MIN_LOT_SIZE

    raw_lots = risk_amount / (pip_distance * _PIP_VALUE_PER_LOT)
    return _clamp_lots(raw_lots)


def _clamp_lots(lots: float) -> float:
    """Round lot size to the nearest step and enforce hard min/max limits."""
    step    = config.LOT_STEP
    rounded = round(round(lots / step) * step, 2)
    return max(config.MIN_LOT_SIZE, min(config.MAX_LOT_SIZE, rounded))


# ─────────────────────────────────────────────────────────────────────────────
# Daily loss limit
# ─────────────────────────────────────────────────────────────────────────────

def is_daily_loss_limit_hit(
    day_start_balance: float,
    current_balance:   float,
    profile:           Optional[dict] = None,
) -> bool:
    """
    Return True if the daily loss limit has been reached.
    Trading should stop for the rest of the day when this is True.
    """
    if profile is None:
        profile = config.get_account_profile(current_balance)

    if day_start_balance <= 0:
        return False

    loss_pct = ((day_start_balance - current_balance) / day_start_balance) * 100
    return loss_pct >= profile["max_daily_loss_percent"]


# ─────────────────────────────────────────────────────────────────────────────
# Open risk limit
# ─────────────────────────────────────────────────────────────────────────────

def is_open_risk_limit_hit(
    open_risk_amount: float,
    balance:          float,
    profile:          Optional[dict] = None,
) -> bool:
    """
    Return True if adding another trade would push total open risk over the limit.

    Args:
        open_risk_amount: Sum of (lot_size × sl_pips × pip_value_per_lot)
                          across all currently open trades, in USD.
    """
    if profile is None:
        profile = config.get_account_profile(balance)

    if balance <= 0:
        return True

    open_risk_pct = (open_risk_amount / balance) * 100
    return open_risk_pct >= profile["max_open_risk_percent"]


def calculate_open_risk(lot_size: float, sl_pips: float) -> float:
    """Calculate the dollar risk for a single trade position."""
    return lot_size * sl_pips * _PIP_VALUE_PER_LOT


# ─────────────────────────────────────────────────────────────────────────────
# Concurrent trade limit
# ─────────────────────────────────────────────────────────────────────────────

def is_max_trades_reached(
    current_trade_count: int,
    balance:             float,
    profile:             Optional[dict] = None,
) -> bool:
    """Return True if the maximum number of concurrent trades has been reached."""
    if profile is None:
        profile = config.get_account_profile(balance)
    return current_trade_count >= profile["max_concurrent_trades"]
