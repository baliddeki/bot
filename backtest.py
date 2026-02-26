from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Literal

import pandas as pd

from config import StrategyConfig, RiskConfig
from data_loader import fetch_rates, get_symbol_spec, pip_to_price
from swings import detect_fractal_swings, find_sweep_on_candle
from strategy import build_trade_plan_from_sweep
from risk import tier_for_balance, DailyGuard, compute_lot_size

Direction = Literal["BUY", "SELL"]


# ─────────────────────────────────────────────────────────────────────────────
#  Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class TradeResult:
    direction: Direction
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    exit_time: pd.Timestamp
    exit_price: float
    outcome: str  # "TP" or "SL"
    pnl: float  # in account currency
    lots: float
    flag_tf: str
    fvg_tf: str
    meta: dict = field(default_factory=dict)


@dataclass
class BacktestReport:
    trades: List[TradeResult]
    start_balance: float
    end_balance: float
    win_rate: float
    total_return_pct: float
    max_drawdown_pct: float


# ─────────────────────────────────────────────────────────────────────────────
#  Fill + exit simulation
# ─────────────────────────────────────────────────────────────────────────────


def _simulate_trade(
    df: pd.DataFrame,
    direction: Direction,
    entry: float,
    sl: float,
    tp: float,
    max_bars: int,
) -> Tuple[
    Optional[pd.Timestamp], Optional[pd.Timestamp], Optional[float], Optional[str]
]:
    """
    Walk forward candle by candle:
    1. Wait for price to touch `entry`.
    2. Then check SL/TP each bar (SL wins on same-candle conflict).

    Returns (entry_time, exit_time, exit_price, "TP"|"SL") or (None,None,None,None).
    """
    filled = False
    entry_time: Optional[pd.Timestamp] = None

    for i in range(min(len(df), max_bars)):
        c = df.iloc[i]
        hi = float(c.high)
        lo = float(c.low)
        t = c.time

        if not filled:
            if lo <= entry <= hi:
                filled = True
                entry_time = t
            continue  # skip exit check until filled

        # Check SL first (conservative)
        if direction == "BUY":
            if lo <= sl:
                return entry_time, t, sl, "SL"
            if hi >= tp:
                return entry_time, t, tp, "TP"
        else:
            if hi >= sl:
                return entry_time, t, sl, "SL"
            if lo <= tp:
                return entry_time, t, tp, "TP"

    return None, None, None, None


# ─────────────────────────────────────────────────────────────────────────────
#  Main backtest runner
# ─────────────────────────────────────────────────────────────────────────────


def run_backtest(
    cfg: StrategyConfig,
    risk_cfg: RiskConfig,
    dt_from: dt.datetime,
    dt_to: dt.datetime,
    start_balance: float,
) -> BacktestReport:
    symbol = cfg.symbol
    spec = get_symbol_spec(symbol)

    # ── Fetch all data ────────────────────────────────────────────────
    df_2h = fetch_rates(symbol, "H2", dt_from, dt_to)
    if df_2h.empty:
        return BacktestReport([], start_balance, start_balance, 0.0, 0.0, 0.0)

    # Fetch all TFs needed (flag TFs + fvg TFs, deduplicated)
    all_tfs = list(dict.fromkeys(cfg.flag_tfs + cfg.fvg_tfs))
    ltf_data = {tf: fetch_rates(symbol, tf, dt_from, dt_to) for tf in all_tfs}

    # ── Detect swings on H2 ───────────────────────────────────────────
    swings = detect_fractal_swings(
        df_2h, cfg.swing_fractal_left, cfg.swing_fractal_right
    )

    # ── Walk forward on H2 candles ────────────────────────────────────
    balance = start_balance
    equity = start_balance
    peak = start_balance
    max_dd = 0.0
    trades: List[TradeResult] = []
    daily_guard: Optional[DailyGuard] = None

    for i in range(1, len(df_2h)):
        c2h = df_2h.iloc[i]
        day = c2h.time.date()

        # ── Daily guard ───────────────────────────────────────────────
        risk_pct, max_loss = tier_for_balance(balance, risk_cfg)
        if daily_guard is None or daily_guard.day != day:
            daily_guard = DailyGuard(
                day=day,
                start_equity=equity,
                max_loss_pct=max_loss,
            )
        if not daily_guard.update_and_check(equity):
            continue

        # ── Sweep detection ───────────────────────────────────────────
        result = find_sweep_on_candle(
            swings,
            c2h,
            i,
            require_wick_sweep=cfg.require_wick_sweep,
        )
        if result is None:
            continue

        direction, swing = result
        sweep_time = c2h.time

        # ── Build trade plan ──────────────────────────────────────────
        plan = build_trade_plan_from_sweep(cfg, sweep_time, direction, ltf_data)
        if plan is None:
            continue

        # ── Simulate fill & exit on the FVG TF data ──────────────────
        df_exec = ltf_data.get(plan.fvg_tf)
        if df_exec is None or df_exec.empty:
            continue

        after_zone = df_exec[df_exec["time"] >= plan.zone.formed_time].reset_index(
            drop=True
        )
        if after_zone.empty:
            continue

        entry_t, exit_t, exit_price, outcome = _simulate_trade(
            after_zone,
            direction,
            plan.entry,
            plan.sl,
            plan.tp,
            cfg.fill_max_bars,
        )
        if outcome is None:
            continue

        # ── Position sizing ───────────────────────────────────────────
        lots = compute_lot_size(
            balance,
            risk_pct,
            plan.entry,
            plan.sl,
            spec,
            risk_cfg.min_lot,
            risk_cfg.max_lot,
            risk_cfg.lot_step,
        )

        # ── P&L ──────────────────────────────────────────────────────
        vppu = spec.value_per_price_unit_per_lot
        if direction == "BUY":
            pnl = (exit_price - plan.entry) * vppu * lots
        else:
            pnl = (plan.entry - exit_price) * vppu * lots

        balance += pnl
        equity = balance
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / max(peak, 1e-9))

        trades.append(
            TradeResult(
                direction=direction,
                entry_time=entry_t,
                entry=plan.entry,
                sl=plan.sl,
                tp=plan.tp,
                exit_time=exit_t,
                exit_price=exit_price,
                outcome=outcome,
                pnl=pnl,
                lots=lots,
                flag_tf=plan.flag_tf,
                fvg_tf=plan.fvg_tf,
                meta=plan.meta,
            )
        )

        # Mark swing taken so we don't trade it again
        swing.taken = True

    wins = sum(1 for t in trades if t.outcome == "TP")
    win_rate = wins / len(trades) if trades else 0.0
    total_return = (balance - start_balance) / max(start_balance, 1e-9)

    return BacktestReport(
        trades=trades,
        start_balance=start_balance,
        end_balance=balance,
        win_rate=win_rate,
        total_return_pct=total_return * 100.0,
        max_drawdown_pct=max_dd * 100.0,
    )
