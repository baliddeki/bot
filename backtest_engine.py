"""
backtest_engine.py — Walk-forward candle-by-candle simulation.

How it works:
  1. Steps through H1 candles as the simulation clock
  2. At each step, slices all TF DataFrames to only include data
     up to and including the current candle close time
  3. Checks open virtual trades for SL/TP hits using the candle's
     high and low (intra-candle simulation)
  4. Runs the full signal pipeline on the sliced data
  5. Opens new virtual positions via BacktestExecutor
  6. Generates a setup chart for every closed trade

Intra-candle SL/TP priority:
  SL is checked before TP (conservative — avoids overstating wins).
  If the candle's range sweeps through both levels, SL wins.

Returns:
  A list of ClosedTrade records for the summary report.
"""

from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

import config
from backtest_executor import BacktestExecutor, VirtualPosition
from signal_generator import generate_signal, Signal
from chart_generator import generate_setup_chart, TradeResult
from risk_manager import (
    calculate_lot_size,
    is_daily_loss_limit_hit,
    is_max_trades_reached,
)
from logger import log_event, log_trade

# ─────────────────────────────────────────────────────────────────────────────
# Result records
# ─────────────────────────────────────────────────────────────────────────────


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
    tp1: float
    tp2: Optional[float]
    lot_size: float
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    exit_price: float
    outcome: str  # "TP1_HIT", "TP2_HIT", "SL_HIT", "SL_HIT_AFTER_PARTIAL"
    pnl_pips: float
    pnl_usd: float
    partial_closed: bool
    chart_path: str


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class BacktestEngine:
    """
    Simulates the live bot's behaviour on historical data.

    Usage:
        engine  = BacktestEngine(candle_data, initial_balance=10_000)
        results = engine.run()
    """

    def __init__(
        self,
        candle_data: dict,  # Full historical data: {tf: DataFrame}
        initial_balance: float = config.BACKTEST_INITIAL_BALANCE,
        step_tf: str = "H1",  # Timeframe used as the simulation clock
    ):
        self.candle_data = candle_data
        self.initial_balance = initial_balance
        self.step_tf = step_tf
        self.executor = BacktestExecutor(initial_balance)

        # Active trades during the simulation: trade_id → OpenBacktestTrade
        self._open: dict[str, OpenBacktestTrade] = {}

        # Completed trades — returned at the end of run()
        self._closed: list[ClosedTrade] = []

    # ── Public API ────────────────────────────────────────────────────────

    def run(self) -> list[ClosedTrade]:
        """
        Run the full walk-forward simulation.
        Returns a list of ClosedTrade records.
        """
        clock_df = self.candle_data.get(self.step_tf)
        if clock_df is None or clock_df.empty:
            log_event(f"Backtest: no {self.step_tf} data — cannot run.", level="ERROR")
            return []

        balance = self.initial_balance
        day_start_balance = balance
        current_day = None

        log_event(
            f"Backtest: starting walk-forward over {len(clock_df)} {self.step_tf} candles."
        )

        for idx in range(3, len(clock_df)):  # Start at 3 so detectors have context
            candle = clock_df.iloc[idx]
            current_time = pd.Timestamp(candle["time"])

            # ── Reset daily balance tracker on new trading day ────────────
            candle_day = current_time.date()
            if candle_day != current_day:
                current_day = candle_day
                day_start_balance = balance

            # ── Slice all TF data up to the current candle close time ─────
            sliced = self._slice_data(current_time)

            # ── Check open trades for SL/TP hits on this candle ──────────
            balance = self._check_open_trades(candle, current_time, sliced, balance)

            # ── Risk gates ───────────────────────────────────────────────
            profile = config.get_account_profile(balance)

            if is_daily_loss_limit_hit(day_start_balance, balance, profile):
                continue

            if is_max_trades_reached(len(self._open), balance, profile):
                continue

            # ── Signal scan ───────────────────────────────────────────────
            signal = generate_signal(sliced, current_time)

            if signal:
                lot_size = calculate_lot_size(balance, signal.entry, signal.sl, profile)
                trade_id = self.executor.place_trade(
                    direction=signal.direction,
                    entry=signal.entry,
                    sl=signal.sl,
                    tp=signal.tp1 or 0.0,
                    lot_size=lot_size,
                    comment=f"BT_{signal.trade_type[:4]}_{signal.swept_tf}",
                )
                if trade_id:
                    self._open[trade_id] = OpenBacktestTrade(
                        trade_id=trade_id,
                        signal=signal,
                        entry_time=current_time,
                        initial_lots=lot_size,
                        balance_at_open=balance,
                        candles_at_open=sliced,
                    )
                    log_event(
                        f"BT OPEN  {trade_id} | {signal.direction} {signal.trade_type} | "
                        f"Entry {signal.entry:.2f} | SL {signal.sl:.2f} | TP1 {signal.tp1}"
                    )

        # ── Force-close any remaining open trades at last candle close ────
        last_candle = clock_df.iloc[-1]
        last_time = pd.Timestamp(last_candle["time"])
        last_price = float(last_candle["close"])
        sliced_last = self._slice_data(last_time)

        for trade_id, trade in list(self._open.items()):
            self._close_trade(
                trade=trade,
                exit_price=last_price,
                exit_time=last_time,
                outcome="BACKTEST_END",
                balance=balance,
                candle_data=sliced_last,
            )
            del self._open[trade_id]

        log_event(
            f"Backtest complete. {len(self._closed)} trades closed. "
            f"Final balance: ${balance:,.2f}"
        )
        return self._closed

    # ── Data slicing ──────────────────────────────────────────────────────

    def _slice_data(self, up_to: pd.Timestamp) -> dict:
        """
        Return a copy of candle_data with each DataFrame trimmed to
        only include candles whose close time is ≤ up_to.
        """
        sliced = {}
        for tf, df in self.candle_data.items():
            if df is None:
                sliced[tf] = None
                continue
            sliced[tf] = df[df["time"] <= up_to].copy().reset_index(drop=True)
        return sliced

    # ── Intra-candle SL/TP simulation ─────────────────────────────────────

    def _check_open_trades(
        self,
        candle: pd.Series,
        current_time: pd.Timestamp,
        sliced: dict,
        balance: float,
    ) -> float:
        """
        Check all open trades against the current candle's high/low.
        SL is checked before TP (conservative).
        Returns updated balance.
        """
        candle_high = float(candle["high"])
        candle_low = float(candle["low"])
        candle_close = float(candle["close"])

        for trade_id, trade in list(self._open.items()):
            sig = trade.signal
            direction = sig.direction

            # ── Update swing TP dynamically if enabled ────────────────────
            if sig.trade_type == "SWING" and config.SWING_TP_DYNAMIC:
                new_tp = self._find_swing_tp(sig, sliced)
                if new_tp and new_tp != sig.tp1:
                    sig.tp1 = new_tp

            # ── Intraday partial close check (TP1) ────────────────────────
            if (
                sig.trade_type == "INTRADAY"
                and not trade.partial_closed
                and sig.tp1 is not None
            ):
                tp1_hit = (direction == "BUY" and candle_high >= sig.tp1) or (
                    direction == "SELL" and candle_low <= sig.tp1
                )
                if tp1_hit:
                    self.executor.close_partial(trade_id, 0.5, "TP1")
                    trade.partial_closed = True
                    log_event(f"BT TP1   {trade_id} | closed 50% @ {sig.tp1:.2f}")

            # ── SL check (priority over TP) ───────────────────────────────
            sl_hit = (direction == "BUY" and candle_low <= sig.sl) or (
                direction == "SELL" and candle_high >= sig.sl
            )
            if sl_hit:
                outcome = "SL_HIT_AFTER_PARTIAL" if trade.partial_closed else "SL_HIT"
                balance = self._close_trade(
                    trade=trade,
                    exit_price=sig.sl,
                    exit_time=current_time,
                    outcome=outcome,
                    balance=balance,
                    candle_data=sliced,
                )
                del self._open[trade_id]
                continue

            # ── TP2 check (intraday) ──────────────────────────────────────
            if sig.trade_type == "INTRADAY" and sig.tp2 is not None:
                tp2_hit = (direction == "BUY" and candle_high >= sig.tp2) or (
                    direction == "SELL" and candle_low <= sig.tp2
                )
                if tp2_hit:
                    balance = self._close_trade(
                        trade=trade,
                        exit_price=sig.tp2,
                        exit_time=current_time,
                        outcome="TP2_HIT",
                        balance=balance,
                        candle_data=sliced,
                    )
                    del self._open[trade_id]
                    continue

            # ── TP1 check (swing or intraday after partial) ───────────────
            if sig.tp1 is not None:
                tp1_hit = (direction == "BUY" and candle_high >= sig.tp1) or (
                    direction == "SELL" and candle_low <= sig.tp1
                )
                if tp1_hit:
                    balance = self._close_trade(
                        trade=trade,
                        exit_price=sig.tp1,
                        exit_time=current_time,
                        outcome="TP1_HIT",
                        balance=balance,
                        candle_data=sliced,
                    )
                    del self._open[trade_id]

        return balance

    # ── Trade closing and recording ───────────────────────────────────────

    def _close_trade(
        self,
        trade: OpenBacktestTrade,
        exit_price: float,
        exit_time: pd.Timestamp,
        outcome: str,
        balance: float,
        candle_data: dict,
    ) -> float:
        """Record a closed trade, generate its chart, log it, and return updated balance."""
        sig = trade.signal
        raw_move = (
            exit_price - sig.entry if sig.direction == "BUY" else sig.entry - exit_price
        )
        pnl_pips = raw_move / config.PIP_SIZE
        pnl_usd = pnl_pips * config.PIP_SIZE * trade.initial_lots * 100
        balance = round(balance + pnl_usd, 2)

        self.executor.set_balance(balance)
        self.executor.close_full(trade.trade_id, outcome)

        # ── Generate chart ────────────────────────────────────────────────
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
        chart_df = candle_data.get(config.CHART_TIMEFRAME) or candle_data.get("H1")
        if chart_df is not None and not chart_df.empty:
            chart_path = generate_setup_chart(chart_df, result)

        # ── Write trade log row ───────────────────────────────────────────
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
            f"{pnl_pips:+.1f} pips | ${pnl_usd:+.2f} | balance ${balance:,.2f}"
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

    # ── Swing TP helper ───────────────────────────────────────────────────

    @staticmethod
    def _find_swing_tp(signal: Signal, candle_data: dict) -> Optional[float]:
        """Look for the nearest qualifying swing on the swept TF for TP update."""
        from swing_detector import find_swing_highs, find_swing_lows

        df = candle_data.get(signal.swept_tf)
        if df is None or df.empty:
            return None
        if signal.direction == "BUY":
            highs = find_swing_highs(df)
            candidates = [h.price for h in highs if h.price > signal.entry]
            return min(candidates) if candidates else None
        else:
            lows = find_swing_lows(df)
            candidates = [l.price for l in lows if l.price < signal.entry]
            return max(candidates) if candidates else None
