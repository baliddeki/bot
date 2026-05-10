"""
trade_manager.py — Trade lifecycle management.

Responsibilities:
  - Opening trades from Signals via MT5
  - Intraday: partial close at TP1 (50%), full close at TP2
  - Swing: dynamic TP updates as new swings form on the swept TF
  - Swing re-entry: open additional trades when new OBs form before target
  - Generating setup charts on trade close
  - Logging all trade activity
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional, Union

import pandas as pd

import config
from signal_generator import Signal, _calculate_entry, _calculate_sl
from ob_detector import OrderBlock, find_order_blocks, get_most_recent_ob, price_inside_ob
from fvg_detector import FVG, search_fvg_across_timeframes
from swing_detector import find_swing_highs, find_swing_lows
from risk_manager import (
    calculate_lot_size, is_daily_loss_limit_hit,
    is_open_risk_limit_hit, is_max_trades_reached, calculate_open_risk,
)
from chart_generator import generate_setup_chart, TradeResult
from logger import log_event, log_trade
from executor_base import BaseExecutor


# ─────────────────────────────────────────────────────────────────────────────
# Open trade record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OpenTrade:
    setup_id:        str
    signal:          Signal
    ticket:          int
    initial_lots:    float       # Lot size at trade open
    current_lots:    float       # Lot size remaining (reduces after partial close)
    entry_time:      pd.Timestamp
    balance_at_open: float
    partial_closed:  bool  = False   # True once TP1 is hit on intraday trades
    reentry_count:   int   = 0       # Number of re-entries taken on this swing setup
    candles_at_open: dict  = field(default_factory=dict)  # Snapshot used for chart


# ─────────────────────────────────────────────────────────────────────────────
# Trade manager
# ─────────────────────────────────────────────────────────────────────────────

class TradeManager:
    """
    Manages all open trades from entry through to close.

    Usage:
        manager = TradeManager()
        setup_id = manager.open_trade(signal, candle_data, balance)
        manager.on_candle_close(candle_data, balance)
    """

    def __init__(self, executor: BaseExecutor):
        # Maps setup_id → OpenTrade for all currently open trades
        self.executor = executor
        self.open_trades: dict[str, OpenTrade] = {}

    # ── Public interface ──────────────────────────────────────────────────

    def open_trade(
        self,
        signal:      Signal,
        candle_data: dict,
        balance:     float,
    ) -> Optional[str]:
        """
        Execute a trade from a Signal and record it.

        Returns the setup_id string on success, or None if execution failed.
        """
        profile  = config.get_account_profile(balance)
        lot_size = calculate_lot_size(balance, signal.entry, signal.sl, profile)

        # Use TP1 for the MT5 order; TP2 will be monitored manually
        mt5_tp = signal.tp1 or 0.0

        ticket = self.executor.place_trade(
            direction = signal.direction,
            entry     = signal.entry,
            sl        = signal.sl,
            tp        = mt5_tp,
            lot_size  = lot_size,
            comment   = f"XAUBOT_{signal.trade_type[:4]}_{signal.swept_tf}",
        )

        if ticket is None:
            return None

        setup_id = _make_setup_id(signal)
        self.open_trades[setup_id] = OpenTrade(
            setup_id        = setup_id,
            signal          = signal,
            ticket          = ticket,
            initial_lots    = lot_size,
            current_lots    = lot_size,
            entry_time      = pd.Timestamp.now(tz="UTC"),
            balance_at_open = balance,
            candles_at_open = candle_data,
        )

        log_event(
            f"TRADE OPEN  {setup_id} | {signal.direction} {signal.trade_type} | "
            f"Swept {signal.swept_tf} | OB {signal.ob_tf} | FVG {signal.fvg_tf} | "
            f"Entry {signal.entry:.2f} | SL {signal.sl:.2f} | TP1 {signal.tp1}"
        )
        return setup_id

    def on_candle_close(self, candle_data: dict, balance: float):
        """
        Run all trade management logic on each new candle close.

        Call this once per bot scan cycle after fresh data is fetched.
        """
        to_close = []

        for setup_id, trade in self.open_trades.items():
            current_price = _get_latest_price(candle_data)
            if current_price is None:
                continue

            if trade.signal.trade_type == "INTRADAY":
                closed = self._manage_intraday(trade, current_price, candle_data, balance)
            else:
                closed = self._manage_swing(trade, current_price, candle_data, balance)

            if closed:
                to_close.append(setup_id)

        for sid in to_close:
            del self.open_trades[sid]

    @property
    def total_open_risk(self) -> float:
        """Total dollar risk across all open trades."""
        total = 0.0
        for trade in self.open_trades.values():
            sl_pips = abs(trade.signal.entry - trade.signal.sl) / config.PIP_SIZE
            total  += calculate_open_risk(trade.current_lots, sl_pips)
        return total

    # ── Intraday management ───────────────────────────────────────────────

    def _manage_intraday(
        self,
        trade:         OpenTrade,
        current_price: float,
        candle_data:   dict,
        balance:       float,
    ) -> bool:
        """
        Intraday logic:
          - TP1 at 150 pips: close 50%, keep 50% running
          - TP2 at 250 pips: close remaining 50%
          - SL: close 100%

        Returns True if trade is now fully closed.
        """
        sig = trade.signal
        direction = sig.direction

        # ── TP1 partial close ─────────────────────────────────────────────
        if not trade.partial_closed and sig.tp1:
            tp1_hit = (
                (direction == "BUY"  and current_price >= sig.tp1) or
                (direction == "SELL" and current_price <= sig.tp1)
            )
            if tp1_hit:
                close_fraction = config.INTRADAY_TP1_CLOSE_PERCENT / 100
                if self.executor.close_partial(trade.ticket, close_fraction, "TP1"):
                    trade.current_lots  = round(trade.current_lots * (1 - close_fraction), 2)
                    trade.partial_closed = True
                    log_event(
                        f"{trade.setup_id}: TP1 hit — closed {close_fraction*100:.0f}% "
                        f"@ {current_price:.2f}"
                    )

        # ── TP2 full close ────────────────────────────────────────────────
        if sig.tp2:
            tp2_hit = (
                (direction == "BUY"  and current_price >= sig.tp2) or
                (direction == "SELL" and current_price <= sig.tp2)
            )
            if tp2_hit:
                self.executor.close_full(trade.ticket, "TP2")
                self._close_and_record(trade, candle_data, "TP2_HIT", current_price, balance)
                return True

        # ── SL ────────────────────────────────────────────────────────────
        if self._sl_hit(sig, current_price):
            self.executor.close_full(trade.ticket, "SL")
            outcome = "SL_HIT_AFTER_PARTIAL" if trade.partial_closed else "SL_HIT"
            self._close_and_record(trade, candle_data, outcome, current_price, balance)
            return True

        return False

    # ── Swing management ─────────────────────────────────────────────────

    def _manage_swing(
        self,
        trade:         OpenTrade,
        current_price: float,
        candle_data:   dict,
        balance:       float,
    ) -> bool:
        """
        Swing logic:
          - Dynamically update TP as new qualifying swings form (if enabled)
          - TP hit: close 100%
          - SL hit: close 100%
          - Check for re-entry opportunities

        Returns True if trade is fully closed.
        """
        sig = trade.signal

        # ── Dynamic TP update ─────────────────────────────────────────────
        if config.SWING_TP_DYNAMIC:
            new_tp = _find_latest_swing_tp(sig, candle_data)
            if new_tp and new_tp != sig.tp1:
                if self.executor.modify_tp(trade.ticket, new_tp):
                    sig.tp1 = new_tp
                    log_event(f"{trade.setup_id}: TP updated → {new_tp:.2f}")

        # ── TP hit ────────────────────────────────────────────────────────
        if sig.tp1:
            tp_hit = (
                (sig.direction == "BUY"  and current_price >= sig.tp1) or
                (sig.direction == "SELL" and current_price <= sig.tp1)
            )
            if tp_hit:
                self.executor.close_full(trade.ticket, "TP")
                self._close_and_record(trade, candle_data, "TP1_HIT", current_price, balance)
                return True

        # ── SL hit ────────────────────────────────────────────────────────
        if self._sl_hit(sig, current_price):
            self.executor.close_full(trade.ticket, "SL")
            self._close_and_record(trade, candle_data, "SL_HIT", current_price, balance)
            return True

        # ── Re-entry check ────────────────────────────────────────────────
        if (
            config.SWING_REENTRY_ENABLED
            and trade.reentry_count < config.SWING_REENTRY_MAX_ENTRIES
        ):
            self._attempt_reentry(trade, candle_data, balance)

        return False

    def _attempt_reentry(
        self,
        trade:       OpenTrade,
        candle_data: dict,
        balance:     float,
    ):
        """
        Check if a new OB has formed on a permitted TF between
        current price and the target. If so, open an additional entry.
        """
        sig           = trade.signal
        current_price = _get_latest_price(candle_data)

        if current_price is None or sig.tp1 is None:
            return

        for tf in config.SWING_REENTRY_PERMITTED_TFS:
            df = candle_data.get(tf)
            if df is None:
                continue

            blocks = find_order_blocks(df, tf)
            ob     = get_most_recent_ob(blocks, sig.direction)

            if ob is None:
                continue

            # OB must be between current price and target TP
            in_range = (
                sig.direction == "BUY"
                and ob.zone_low > current_price
                and ob.zone_high < sig.tp1
            ) or (
                sig.direction == "SELL"
                and ob.zone_high < current_price
                and ob.zone_low > sig.tp1
            )

            if not in_range:
                continue

            # Price must be inside the new OB zone
            if not price_inside_ob(current_price, ob):
                continue

            # Find FVG within this new OB
            fvg = search_fvg_across_timeframes(
                candle_data  = candle_data,
                ob_low       = ob.zone_low,
                ob_high      = ob.zone_high,
                direction    = sig.direction,
                search_order = config.FVG_SEARCH_ORDER,
            )
            if fvg is None:
                continue

            # Build re-entry prices
            entry = _calculate_entry(fvg, sig.direction)
            sl    = _calculate_sl(entry, sig.direction)

            profile  = config.get_account_profile(balance)
            lot_size = calculate_lot_size(balance, entry, sl, profile)

            ticket = self.executor.place_trade(
                direction = sig.direction,
                entry     = entry,
                sl        = sl,
                tp        = sig.tp1,
                lot_size  = lot_size,
                comment   = f"XAUBOT_REENTRY_{trade.reentry_count + 1}",
            )

            if ticket:
                trade.reentry_count += 1
                log_event(
                    f"{trade.setup_id}: Re-entry #{trade.reentry_count} "
                    f"opened @ {entry:.2f} on {tf} OB"
                )
                break  # One re-entry per candle; re-check next candle close

    # ── Trade close recording ─────────────────────────────────────────────

    def _close_and_record(
        self,
        trade:         OpenTrade,
        candle_data:   dict,
        outcome:       str,
        exit_price:    float,
        balance_after: float,
    ):
        """Generate the setup chart and write the trade log entry."""
        sig      = trade.signal
        pnl_pips = _calculate_pnl_pips(sig.direction, sig.entry, exit_price)
        pnl_usd  = pnl_pips * config.PIP_SIZE * trade.initial_lots * 100  # approx

        result = TradeResult(
            setup_id       = trade.setup_id,
            direction      = sig.direction,
            trade_type     = sig.trade_type,
            entry          = sig.entry,
            sl             = sig.sl,
            tp1            = sig.tp1,
            tp2            = sig.tp2,
            ob_low         = sig.ob.zone_low,
            ob_high        = sig.ob.zone_high,
            fvg_low        = sig.fvg.gap_low,
            fvg_high       = sig.fvg.gap_high,
            swept_level    = sig.swept_swing.price,
            entry_time     = trade.entry_time,
            outcome        = outcome,
            partial_closed = trade.partial_closed,
            exit_time      = pd.Timestamp.now(tz="UTC"),
            exit_price     = exit_price,
            pnl_pips       = pnl_pips,
        )

        # Generate chart using the most visible TF candles
        chart_path = ""
        chart_df   = candle_data.get(config.CHART_TIMEFRAME) or candle_data.get("H1")
        if chart_df is not None:
            chart_path = generate_setup_chart(chart_df, result)

        # Write trade log row
        log_trade({
            "trade_id":            trade.setup_id,
            "timestamp":           str(trade.entry_time),
            "symbol":              config.SYMBOL,
            "direction":           sig.direction,
            "trade_type":          sig.trade_type,
            "swept_tf":            sig.swept_tf,
            "ob_tf":               sig.ob_tf,
            "fvg_tf":              sig.fvg_tf,
            "entry":               sig.entry,
            "sl":                  sig.sl,
            "tp1":                 sig.tp1 or "",
            "tp2":                 sig.tp2 or "",
            "lot_size":            trade.initial_lots,
            "outcome":             outcome,
            "partial_closed":      trade.partial_closed,
            "exit_price":          exit_price,
            "exit_time":           str(pd.Timestamp.now(tz="UTC")),
            "pnl_pips":            round(pnl_pips, 2),
            "pnl_usd":             round(pnl_usd, 2),
            "balance_before":      trade.balance_at_open,
            "balance_after":       balance_after,
            "setup_chart_path":    chart_path,
        })

        log_event(
            f"TRADE CLOSE {trade.setup_id} | {outcome} @ {exit_price:.2f} | "
            f"{pnl_pips:+.1f} pips | chart: {chart_path}"
        )

    # ── Static helpers ────────────────────────────────────────────────────

    @staticmethod
    def _sl_hit(signal: Signal, current_price: float) -> bool:
        return (
            (signal.direction == "BUY"  and current_price <= signal.sl) or
            (signal.direction == "SELL" and current_price >= signal.sl)
        )


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_latest_price(candle_data: dict) -> Optional[float]:
    for tf in ["H1", "H4", "D1"]:
        df = candle_data.get(tf)
        if df is not None and not df.empty:
            return float(df.iloc[-1]["close"])
    return None


def _find_latest_swing_tp(signal: Signal, candle_data: dict) -> Optional[float]:
    """Look for the nearest qualifying swing on the swept TF for TP update."""
    df = candle_data.get(signal.swept_tf)
    if df is None:
        return None

    if signal.direction == "BUY":
        highs      = find_swing_highs(df)
        candidates = [h.price for h in highs if h.price > signal.entry]
        return min(candidates) if candidates else None
    else:
        lows       = find_swing_lows(df)
        candidates = [l.price for l in lows if l.price < signal.entry]
        return max(candidates) if candidates else None


def _calculate_pnl_pips(direction: str, entry: float, exit_price: float) -> float:
    raw = exit_price - entry if direction == "BUY" else entry - exit_price
    return raw / config.PIP_SIZE


def _make_setup_id(signal: Signal) -> str:
    short_id = uuid.uuid4().hex[:6].upper()
    return f"{signal.direction[0]}{signal.trade_type[:4]}_{signal.swept_tf}_{short_id}"
