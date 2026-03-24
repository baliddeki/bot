from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Literal

# ─────────────────────────────────────────────
#  SYMBOL – hardcoded to XAUUSD only
# ─────────────────────────────────────────────
SYMBOL = "XAUUSDm"

# ─────────────────────────────────────────────
#  TIMEFRAME LITERALS
# ─────────────────────────────────────────────
Timeframe = Literal["M5", "M15", "M30", "H1", "H2"]


# ─────────────────────────────────────────────
#  STRATEGY CONFIGURATION
#  Edit these values to tune the strategy
# ─────────────────────────────────────────────
@dataclass(frozen=True)
class StrategyConfig:
    symbol: str = SYMBOL

    # 2H is used exclusively for swing detection
    swing_tf: Timeframe = "H2"

    # Flag pattern timeframes (lowest to highest priority)
    # Flags are searched on H1, M30, M15 only
    flag_tfs: List[Timeframe] = field(default_factory=lambda: ["H2","H1", "M30", "M15"])

    # LTF timeframes to search for FVG during the engulfing candle
    # Ordered lowest → highest (3M is checked first)
    fvg_tfs: List[Timeframe] = field(
        default_factory=lambda: ["M5", "M15", "M30", "H1"]
    )

    # Swing fractal look-left / look-right bars on H2
    swing_fractal_left: int = 2
    swing_fractal_right: int = 2

    # Require a wick sweep (close back on the other side)
    require_wick_sweep: bool = True

    # Max bars to scan for a flag pattern after a sweep
    flag_scan_max_bars: int = 80

    # Max bars to wait for fill after FVG zone is formed
    fill_max_bars: int = 200

    # ── TP / SL ──────────────────────────────
    # For XAUUSD: 1 pip = 0.1 price units (e.g. 300 pips = $30 move)
    tp_pips: float = 150.0  # Fixed TP distance in pips
    sl_pips: float = 100.0  # Fixed SL distance in pips

    # Entry: True = midpoint of FVG zone, False = zone edge
    entry_at_midpoint: bool = True


# ─────────────────────────────────────────────
#  RISK CONFIGURATION
#  Edit these values to tune risk management
# ─────────────────────────────────────────────
@dataclass(frozen=True)
class RiskConfig:
    # ── Lot limits ───────────────────────────
    min_lot: float = 0.01
    max_lot: float = 5.0
    lot_step: float = 0.01

    # ── Small account (balance ≤ small_acct_max_balance) ──
    small_acct_max_balance: float = 500.0
    small_risk_pct: float = 0.02  # 2% risk per trade
    small_max_daily_loss_pct: float = 0.06  # 6% max daily loss

    # ── Large account (balance ≥ big_acct_min_balance) ──
    big_acct_min_balance: float = 1000.0
    big_risk_pct: float = 0.01  # 1% risk per trade
    big_max_daily_loss_pct: float = 0.04  # 4% max daily loss

    # Between small_acct_max_balance and big_acct_min_balance
    # risk is linearly interpolated automatically


# ─────────────────────────────────────────────
#  DEFAULTS (used by run_backtest / run_live)
# ─────────────────────────────────────────────
def default_strategy_config() -> StrategyConfig:
    return StrategyConfig()


def default_risk_config() -> RiskConfig:
    return RiskConfig()
