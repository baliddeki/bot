from __future__ import annotations

import datetime as dt
import time
import logging
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import MetaTrader5 as mt5

from config import StrategyConfig, RiskConfig
from data_loader import fetch_recent, get_symbol_spec
from strategy import build_trade_plan_from_history, TradePlan
from risk import tier_for_balance, DailyGuard, compute_lot_size

log = logging.getLogger(__name__)


@dataclass
class LiveState:
    daily_guard: Optional[DailyGuard] = None
    last_zone_time: Optional[pd.Timestamp] = None


# ─────────────────────────────────────────────────────────────────────────────
#  MT5 helpers
# ─────────────────────────────────────────────────────────────────────────────


def _has_open_position_or_order(symbol: str) -> bool:
    positions = mt5.positions_get(symbol=symbol)
    if positions and len(positions) > 0:
        return True
    orders = mt5.orders_get(symbol=symbol)
    return orders is not None and len(orders) > 0


def _place_limit_order(plan: TradePlan, lots: float) -> bool:
    order_type = (
        mt5.ORDER_TYPE_BUY_LIMIT
        if plan.direction == "BUY"
        else mt5.ORDER_TYPE_SELL_LIMIT
    )
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": plan.symbol,
        "volume": float(lots),
        "type": order_type,
        "price": float(plan.entry),
        "sl": float(plan.sl),
        "tp": float(plan.tp),
        "deviation": 20,
        "magic": 260224,
        "comment": f"ari_gaup {plan.flag_tf}/{plan.fvg_tf}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    result = mt5.order_send(request)
    if result is None:
        log.error("order_send returned None")
        return False
    if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        log.error(
            "order_send failed: retcode=%s comment=%s", result.retcode, result.comment
        )
        return False
    log.info(
        "Limit order placed: %s %s @ %.5f  SL=%.5f  TP=%.5f  lots=%.2f",
        plan.direction,
        plan.symbol,
        plan.entry,
        plan.sl,
        plan.tp,
        lots,
    )
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Live loop
# ─────────────────────────────────────────────────────────────────────────────


def run_live(
    cfg: StrategyConfig,
    risk_cfg: RiskConfig,
    balance: float,
    poll_seconds: int = 15,
) -> None:
    """
    Main live trading loop.

    Every `poll_seconds`:
    1. Refresh daily guard.
    2. Skip if daily loss limit hit.
    3. Skip if a position / pending order already exists.
    4. Fetch recent H2 + LTF bars.
    5. Build trade plan.
    6. Skip if we already placed an order for this zone.
    7. Compute lots and place a limit order.
    """
    symbol = cfg.symbol
    spec = get_symbol_spec(symbol)
    state = LiveState()

    log.info("Live runner started for %s. Poll every %ds.", symbol, poll_seconds)

    all_tfs = list(dict.fromkeys(cfg.flag_tfs + cfg.fvg_tfs))

    while True:
        try:
            today = dt.datetime.utcnow().date()

            # ── Daily guard ───────────────────────────────────────────
            risk_pct, max_loss = tier_for_balance(balance, risk_cfg)

            if state.daily_guard is None or state.daily_guard.day != today:
                state.daily_guard = DailyGuard(
                    day=today,
                    start_equity=balance,
                    max_loss_pct=max_loss,
                )
                log.info("New day guard: max_loss=%.1f%%", max_loss * 100)

            if not state.daily_guard.update_and_check(balance):
                log.warning("Daily loss limit hit. No new trades today.")
                time.sleep(poll_seconds)
                continue

            # ── Skip if already in a trade ────────────────────────────
            if _has_open_position_or_order(symbol):
                time.sleep(poll_seconds)
                continue

            # ── Fetch data ────────────────────────────────────────────
            df_2h = fetch_recent(symbol, "H2", 500)
            ltf_data = {tf: fetch_recent(symbol, tf, 1500) for tf in all_tfs}

            # ── Build plan ────────────────────────────────────────────
            plan = build_trade_plan_from_history(cfg, df_2h, ltf_data)
            if plan is None:
                time.sleep(poll_seconds)
                continue

            # ── Avoid duplicate orders for same zone ──────────────────
            if (
                state.last_zone_time is not None
                and plan.zone.formed_time <= state.last_zone_time
            ):
                time.sleep(poll_seconds)
                continue

            # ── Size and place ────────────────────────────────────────
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

            if _place_limit_order(plan, lots):
                state.last_zone_time = plan.zone.formed_time

        except KeyboardInterrupt:
            log.info("Live runner stopped by user.")
            return
        except Exception as exc:
            log.exception("Unhandled error in live loop: %s", exc)

        time.sleep(poll_seconds)
