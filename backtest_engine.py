"""
backtest_engine.py — Walk-forward candle-by-candle simulation.

How it works:
  1. Steps through H1 candles as the simulation clock
  2. At each step, slices all TF DataFrames to data up to that candle
  3. Checks PENDING limit orders — fills when candle range crosses entry
  4. Checks OPEN trades for SL/TP hits using candle high/low
  5. Runs the full signal pipeline; new signals create pending limit orders
  6. Pending orders are never cancelled — they wait until filled or backtest ends
  7. Generates a setup chart for every closed trade

Intra-candle priority per candle:
  1. Pending limit fill check
  2. SL check (before TP — conservative)
  3. TP2 check (intraday)
  4. TP1 check

Deduplication:
  A pending order is only created if no existing pending already covers
  the same direction + swept_tf + entry price (rounded to 1 dp).
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

import config
from backtest_executor import BacktestExecutor
from signal_generator import generate_signal, Signal
from chart_generator import generate_setup_chart, TradeResult
from risk_manager import (
    calculate_lot_size,
    is_daily_loss_limit_hit,
    is_max_trades_reached,
)
from logger import log_event, log_trade

# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class PendingLimit:
    """A limit order sitting at the FVG wick entry waiting for price to retrace."""

    signal: Signal
    lot_size: float
    balance_at_signal: float
    signal_time: pd.Timestamp
    candles_at_signal: dict = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{self.signal.direction}_{self.signal.swept_tf}_{self.signal.entry:.1f}"


@dataclass
class OpenBacktestTrade:
    trade_id: str
    signal: Signal
    entry_time: pd.Timestamp
    initial_lots: float
    balance_at_open: float
    partial_closed: bool = False
    candles_at_open: dict = field(default_factory=dict)


@dataclass
class ClosedTrade:
    trade_id: str
    direction: str
    trade_type: str
    swept_tf: str
    ob_tf: str
    fvg_tf: str
    entry: float
    sl: float
    tp1: Optional[float]
    tp2: Optional[float]
    lot_size: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    exit_price: float
    outcome: str
    pnl_pips: float
    pnl_usd: float
    partial_closed: bool
    chart_path: str


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class BacktestEngine:

    def __init__(
        self,
        candle_data: dict,
        initial_balance: float = config.BACKTEST_INITIAL_BALANCE,
        step_tf: str = "H1",
        sim_start: Optional[pd.Timestamp] = None,
    ):
        self.candle_data = candle_data
        self.initial_balance = initial_balance
        self.step_tf = step_tf
        self.sim_start = sim_start
        self.executor = BacktestExecutor(initial_balance)
        self._pending: dict[str, PendingLimit] = {}
        self._open: dict[str, OpenBacktestTrade] = {}
        self._closed: list[ClosedTrade] = []

    def run(self) -> list[ClosedTrade]:
        clock_df = self.candle_data.get(self.step_tf)
        if clock_df is None or clock_df.empty:
            log_event(f"Backtest: no {self.step_tf} data.", level="ERROR")
            return []

        balance = self.initial_balance
        day_start_balance = balance
        current_day = None

        log_event(f"Backtest: walking {len(clock_df)} {self.step_tf} candles.")

        for idx in range(3, len(clock_df)):
            candle = clock_df.iloc[idx]
            current_time = pd.Timestamp(candle["time"])
            in_sim = self.sim_start is None or current_time >= self.sim_start

            day = current_time.date()
            if day != current_day:
                current_day = day
                day_start_balance = balance

            sliced = self._slice_data(current_time)

            # 1. Fill pending limit orders
            balance = self._fill_pending(candle, current_time, sliced, balance)

            # 2. Check open trades
            balance = self._check_open_trades(candle, current_time, sliced, balance)

            if not in_sim:
                continue

            profile = config.get_account_profile(balance)
            if is_daily_loss_limit_hit(day_start_balance, balance, profile):
                continue
            if is_max_trades_reached(len(self._open), balance, profile):
                continue

            # 3. Signal scan → pending limit
            signal = generate_signal(sliced, current_time)
            if signal:
                self._add_pending(signal, balance, current_time, sliced, profile)

        # Force-close everything at end
        last = clock_df.iloc[-1]
        last_t = pd.Timestamp(last["time"])
        sliced_l = self._slice_data(last_t)

        for trade_id, trade in list(self._open.items()):
            balance = self._close_trade(
                trade, float(last["close"]), last_t, "BACKTEST_END", balance, sliced_l
            )
            del self._open[trade_id]

        log_event(
            f"Backtest complete. {len(self._closed)} trades. "
            f"{len(self._pending)} pending unfilled. "
            f"Final balance: ${balance:,.2f}"
        )
        return self._closed

    # ── Pending limit management ──────────────────────────────────────────

    def _add_pending(
        self,
        signal: Signal,
        balance: float,
        time: pd.Timestamp,
        sliced: dict,
        profile: dict,
    ):
        pending = PendingLimit(
            signal=signal,
            lot_size=calculate_lot_size(balance, signal.entry, signal.sl, profile),
            balance_at_signal=balance,
            signal_time=time,
            candles_at_signal=sliced,
        )
        if pending.dedup_key in self._pending:
            return
        self._pending[pending.dedup_key] = pending
        log_event(
            f"BT LIMIT {pending.dedup_key} | {signal.direction} {signal.trade_type} | "
            f"Entry {signal.entry:.2f} | SL {signal.sl:.2f} | TP1 {signal.tp1}"
        )

    def _fill_pending(
        self,
        candle: pd.Series,
        current_time: pd.Timestamp,
        sliced: dict,
        balance: float,
    ) -> float:
        """Fill pending limits when candle range crosses entry price."""
        high = float(candle["high"])
        low = float(candle["low"])
        filled = []

        for key, pending in self._pending.items():
            sig = pending.signal
            hit = (sig.direction == "BUY" and low <= sig.entry) or (
                sig.direction == "SELL" and high >= sig.entry
            )
            if not hit:
                continue

            trade_id = self.executor.place_trade(
                direction=sig.direction,
                entry=sig.entry,
                sl=sig.sl,
                tp=sig.tp1 or 0.0,
                lot_size=pending.lot_size,
                comment=f"BT_FILL_{sig.swept_tf}",
            )
            if trade_id:
                self._open[trade_id] = OpenBacktestTrade(
                    trade_id=trade_id,
                    signal=sig,
                    entry_time=current_time,
                    initial_lots=pending.lot_size,
                    balance_at_open=balance,
                    candles_at_open=sliced,
                )
                log_event(
                    f"BT FILL  {trade_id} | {sig.direction} | "
                    f"entry {sig.entry:.2f} filled @ {current_time}"
                )
                filled.append(key)

        for key in filled:
            del self._pending[key]

        return balance

    # ── Open trade SL/TP checking ─────────────────────────────────────────

    def _check_open_trades(
        self,
        candle: pd.Series,
        current_time: pd.Timestamp,
        sliced: dict,
        balance: float,
    ) -> float:
        high = float(candle["high"])
        low = float(candle["low"])

        for trade_id, trade in list(self._open.items()):
            sig = trade.signal
            d = sig.direction

            if sig.trade_type == "SWING" and config.SWING_TP_DYNAMIC:
                new_tp = self._find_swing_tp(sig, sliced)
                if new_tp and new_tp != sig.tp1:
                    sig.tp1 = new_tp

            # Intraday partial close at TP1
            if sig.trade_type == "INTRADAY" and not trade.partial_closed and sig.tp1:
                if (d == "BUY" and high >= sig.tp1) or (d == "SELL" and low <= sig.tp1):
                    self.executor.close_partial(trade_id, 0.5, "TP1")
                    trade.partial_closed = True
                    log_event(f"BT TP1   {trade_id} | 50% @ {sig.tp1:.2f}")

            # SL (priority)
            sl_hit = (d == "BUY" and low <= sig.sl) or (d == "SELL" and high >= sig.sl)
            if sl_hit:
                outcome = "SL_HIT_AFTER_PARTIAL" if trade.partial_closed else "SL_HIT"
                balance = self._close_trade(
                    trade, sig.sl, current_time, outcome, balance, sliced
                )
                del self._open[trade_id]
                continue

            # TP2 (intraday)
            if sig.trade_type == "INTRADAY" and sig.tp2:
                if (d == "BUY" and high >= sig.tp2) or (d == "SELL" and low <= sig.tp2):
                    balance = self._close_trade(
                        trade, sig.tp2, current_time, "TP2_HIT", balance, sliced
                    )
                    del self._open[trade_id]
                    continue

            # TP1
            if sig.tp1:
                if (d == "BUY" and high >= sig.tp1) or (d == "SELL" and low <= sig.tp1):
                    balance = self._close_trade(
                        trade, sig.tp1, current_time, "TP1_HIT", balance, sliced
                    )
                    del self._open[trade_id]

        return balance

    # ── Helpers ───────────────────────────────────────────────────────────

    def _slice_data(self, up_to: pd.Timestamp) -> dict:
        return {
            tf: (
                df[df["time"] <= up_to].reset_index(drop=True)
                if df is not None
                else None
            )
            for tf, df in self.candle_data.items()
        }

    def _close_trade(
        self,
        trade: OpenBacktestTrade,
        exit_price: float,
        exit_time: pd.Timestamp,
        outcome: str,
        balance: float,
        candle_data: dict,
    ) -> float:
        sig = trade.signal
        raw = (
            exit_price - sig.entry if sig.direction == "BUY" else sig.entry - exit_price
        )
        pnl_pips = raw / config.PIP_SIZE
        pnl_usd = pnl_pips * config.PIP_SIZE * trade.initial_lots * 100
        balance = round(balance + pnl_usd, 2)

        self.executor.set_balance(balance)
        self.executor.close_full(trade.trade_id, outcome)

        result = TradeResult(
            setup_id=trade.trade_id,
            direction=sig.direction,
            trade_type=sig.trade_type,
            entry=sig.entry,
            sl=sig.sl,
            tp1=sig.tp1,
            tp2=sig.tp2,
            ob_low=sig.ob.zone_low,
            ob_high=sig.ob.zone_high,
            fvg_low=sig.fvg.gap_low,
            fvg_high=sig.fvg.gap_high,
            swept_level=sig.swept_swing.price,
            entry_time=trade.entry_time,
            outcome=outcome,
            partial_closed=trade.partial_closed,
            exit_time=exit_time,
            exit_price=exit_price,
            pnl_pips=pnl_pips,
        )

        chart_path = ""
        chart_df = candle_data.get(config.CHART_TIMEFRAME)
        if chart_df is None or chart_df.empty:
            chart_df = candle_data.get("H1")
        if chart_df is not None and not chart_df.empty:
            chart_path = generate_setup_chart(chart_df, result)

        log_trade(
            {
                "trade_id": trade.trade_id,
                "timestamp": str(trade.entry_time),
                "symbol": config.SYMBOL,
                "direction": sig.direction,
                "trade_type": sig.trade_type,
                "swept_tf": sig.swept_tf,
                "ob_tf": sig.ob_tf,
                "fvg_tf": sig.fvg_tf,
                "entry": sig.entry,
                "sl": sig.sl,
                "tp1": sig.tp1 or "",
                "tp2": sig.tp2 or "",
                "lot_size": trade.initial_lots,
                "outcome": outcome,
                "partial_closed": trade.partial_closed,
                "exit_price": exit_price,
                "exit_time": str(exit_time),
                "pnl_pips": round(pnl_pips, 2),
                "pnl_usd": round(pnl_usd, 2),
                "balance_before": trade.balance_at_open,
                "balance_after": balance,
                "setup_chart_path": chart_path,
            }
        )

        log_event(
            f"BT CLOSE {trade.trade_id} | {outcome} @ {exit_price:.2f} | "
            f"{pnl_pips:+.1f} pips | ${pnl_usd:+.2f} | bal ${balance:,.2f}"
        )

        self._closed.append(
            ClosedTrade(
                trade_id=trade.trade_id,
                direction=sig.direction,
                trade_type=sig.trade_type,
                swept_tf=sig.swept_tf,
                ob_tf=sig.ob_tf,
                fvg_tf=sig.fvg_tf,
                entry=sig.entry,
                sl=sig.sl,
                tp1=sig.tp1,
                tp2=sig.tp2,
                lot_size=trade.initial_lots,
                entry_time=trade.entry_time,
                exit_time=exit_time,
                exit_price=exit_price,
                outcome=outcome,
                pnl_pips=pnl_pips,
                pnl_usd=pnl_usd,
                partial_closed=trade.partial_closed,
                chart_path=chart_path,
            )
        )
        return balance

    @staticmethod
    def _find_swing_tp(signal: Signal, candle_data: dict) -> Optional[float]:
        from swing_detector import find_swing_highs, find_swing_lows

        df = candle_data.get(signal.swept_tf)
        if df is None or df.empty:
            return None
        if signal.direction == "BUY":
            c = [h.price for h in find_swing_highs(df) if h.price > signal.entry]
            return min(c) if c else None
        c = [l.price for l in find_swing_lows(df) if l.price < signal.entry]
        return max(c) if c else None
