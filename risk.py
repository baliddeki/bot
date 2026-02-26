from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Tuple

from config import RiskConfig
from data_loader import SymbolSpec


@dataclass
class DailyGuard:
    """
    Tracks intra-day drawdown and blocks trading once the
    daily loss limit is hit.
    """

    day: dt.date
    start_equity: float
    max_loss_pct: float  # e.g. 0.06 = 6%
    blocked: bool = False

    def update_and_check(self, equity_now: float) -> bool:
        """
        Call once per iteration with current equity.
        Returns True if trading is allowed, False if blocked.
        """
        if self.blocked:
            return False
        drawdown = (self.start_equity - equity_now) / max(self.start_equity, 1e-9)
        if drawdown >= self.max_loss_pct:
            self.blocked = True
            return False
        return True


def tier_for_balance(balance: float, cfg: RiskConfig) -> Tuple[float, float]:
    """
    Returns (risk_pct_per_trade, max_daily_loss_pct) based on account size.
    Between thresholds the values are linearly interpolated.
    """
    if balance <= cfg.small_acct_max_balance:
        return cfg.small_risk_pct, cfg.small_max_daily_loss_pct

    if balance >= cfg.big_acct_min_balance:
        return cfg.big_risk_pct, cfg.big_max_daily_loss_pct

    # Linear blend
    t = (balance - cfg.small_acct_max_balance) / (
        cfg.big_acct_min_balance - cfg.small_acct_max_balance
    )
    risk_pct = cfg.small_risk_pct + t * (cfg.big_risk_pct - cfg.small_risk_pct)
    max_loss = cfg.small_max_daily_loss_pct + t * (
        cfg.big_max_daily_loss_pct - cfg.small_max_daily_loss_pct
    )
    return risk_pct, max_loss


def _clamp(x: float, lo: float, hi: float, step: float) -> float:
    x = max(lo, min(hi, x))
    if step > 0:
        steps = round((x - lo) / step)
        x = lo + steps * step
    return round(x, 8)


def compute_lot_size(
    balance: float,
    risk_pct: float,
    entry: float,
    sl: float,
    spec: SymbolSpec,
    fallback_min: float = 0.01,
    fallback_max: float = 5.0,
    fallback_step: float = 0.01,
) -> float:
    """
    Lot size so that a full SL hit loses exactly risk_pct of balance.

    Formula:
        risk_amount   = balance * risk_pct
        risk_per_lot  = sl_distance * value_per_price_unit_per_lot
        lots          = risk_amount / risk_per_lot
    """
    risk_amount = balance * risk_pct
    sl_dist = abs(entry - sl)
    vppu = spec.value_per_price_unit_per_lot

    if vppu <= 0 or sl_dist <= 0:
        return fallback_min

    risk_per_lot = sl_dist * vppu
    lots = risk_amount / risk_per_lot

    return _clamp(
        lots,
        spec.volume_min or fallback_min,
        spec.volume_max or fallback_max,
        spec.volume_step or fallback_step,
    )
