"""
run_backtest.py — Backtest entry point.

Fetches historical data from Deriv, runs the walk-forward simulation,
and prints a full summary report.

Charts are saved to config.CHART_OUTPUT_DIR (one PNG per trade).
Trades are logged to config.TRADE_LOG_FILE (CSV).

Usage:
    python run_backtest.py
"""

import sys
from datetime import datetime, timedelta

import pandas as pd

import config
from deriv_client import fetch_all_timeframes
from backtest_engine import BacktestEngine, ClosedTrade
from logger import log_event

# ─────────────────────────────────────────────────────────────────────────────
# How many extra days to fetch BEFORE the backtest start date.
# This gives the signal pipeline historical OBs and swings to work with
# from day one — without it, no OBs exist at the start of the simulation.
# ─────────────────────────────────────────────────────────────────────────────

CONTEXT_DAYS = 120  # 4 months of pre-period context


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main():
    _print_banner()

    date_from = config.BACKTEST_DATE_FROM
    date_to = config.BACKTEST_DATE_TO
    balance = config.BACKTEST_INITIAL_BALANCE
    context_from = date_from - timedelta(days=CONTEXT_DAYS)

    print(f"  Symbol        : {config.SYMBOL}")
    print(f"  Test period   : {date_from.date()}  →  {date_to.date()}")
    print(
        f"  Context fetch : {context_from.date()}  →  {date_to.date()}  "
        f"({CONTEXT_DAYS} days pre-period for OB/swing context)"
    )
    print(f"  Start balance : ${balance:,.2f}")
    print(f"  Account mode  : {config.ACCOUNT_MODE}")
    print()

    # ── Fetch data including the pre-period context ───────────────────────
    log_event(
        f"Fetching data from {context_from.date()} → {date_to.date()} "
        f"(includes {CONTEXT_DAYS}-day context window)..."
    )
    candle_data = fetch_all_timeframes(
        symbol=config.SYMBOL,
        date_from=context_from,
        date_to=date_to,
    )

    # Abort if the primary timeframe is empty
    h1_df = candle_data.get("H1")
    if h1_df is None or h1_df.empty:
        log_event("No H1 data returned — check date range and symbol.", level="ERROR")
        sys.exit(1)

    backtest_start = pd.Timestamp(date_from, tz="UTC")

    log_event(
        f"Data ready. Total H1: {len(h1_df)} candles | "
        f"Simulation starts: {date_from.date()}"
    )

    # ── Run simulation ────────────────────────────────────────────────────
    engine = BacktestEngine(
        candle_data=candle_data,
        initial_balance=balance,
        sim_start=backtest_start,
    )
    results = engine.run()

    # ── Print summary report ──────────────────────────────────────────────
    _print_summary(results, balance)


# ─────────────────────────────────────────────────────────────────────────────
# Summary report
# ─────────────────────────────────────────────────────────────────────────────


def _print_summary(trades: list[ClosedTrade], initial_balance: float):
    print("\n" + "═" * 55)
    print("   BACKTEST RESULTS")
    print("═" * 55)

    if not trades:
        print("  No trades taken during the backtest period.")
        print("  Tip: run  python debug_signal.py  to diagnose.\n")
        return

    total = len(trades)
    wins = [t for t in trades if "TP" in t.outcome]
    losses = [t for t in trades if "SL" in t.outcome]
    partials = [t for t in trades if t.partial_closed]

    total_pips = sum(t.pnl_pips for t in trades)
    total_usd = sum(t.pnl_usd for t in trades)
    avg_win_pips = sum(t.pnl_pips for t in wins) / len(wins) if wins else 0
    avg_los_pips = sum(t.pnl_pips for t in losses) / len(losses) if losses else 0
    win_rate = len(wins) / total * 100 if total else 0

    final_balance = initial_balance + total_usd
    return_pct = (total_usd / initial_balance) * 100

    running = initial_balance
    peak = initial_balance
    max_dd = 0.0
    for t in trades:
        running += t.pnl_usd
        peak = max(peak, running)
        max_dd = max(max_dd, (peak - running) / peak * 100)

    gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    intraday = [t for t in trades if t.trade_type == "INTRADAY"]
    swing = [t for t in trades if t.trade_type == "SWING"]

    print(
        f"  Period        : {trades[0].entry_time.date()}  →  {trades[-1].exit_time.date()}"
    )
    print(f"  Total trades  : {total}")
    print(f"  Wins / Losses : {len(wins)} / {len(losses)}")
    print(f"  Win rate      : {win_rate:.1f}%")
    print(f"  Partial TP1s  : {len(partials)}")
    print()
    print(f"  Total pips    : {total_pips:+.1f}")
    print(f"  Avg win       : {avg_win_pips:+.1f} pips")
    print(f"  Avg loss      : {avg_los_pips:+.1f} pips")
    print(f"  Profit factor : {profit_factor:.2f}")
    print()
    print(f"  Start balance : ${initial_balance:,.2f}")
    print(f"  Final balance : ${final_balance:,.2f}")
    print(f"  Net P&L       : ${total_usd:+,.2f}  ({return_pct:+.2f}%)")
    print(f"  Max drawdown  : {max_dd:.2f}%")
    print()

    if intraday:
        iw = sum(1 for t in intraday if "TP" in t.outcome)
        print(
            f"  Intraday      : {len(intraday)} trades | "
            f"{iw/len(intraday)*100:.1f}% win | "
            f"{sum(t.pnl_pips for t in intraday):+.1f} pips"
        )

    if swing:
        sw = sum(1 for t in swing if "TP" in t.outcome)
        print(
            f"  Swing         : {len(swing)} trades | "
            f"{sw/len(swing)*100:.1f}% win | "
            f"{sum(t.pnl_pips for t in swing):+.1f} pips"
        )

    print()
    print(f"  Charts saved  : {config.CHART_OUTPUT_DIR}/")
    print(f"  Trade log     : {config.LOG_DIRECTORY}/{config.TRADE_LOG_FILE}")
    print("═" * 55 + "\n")

    print(
        f"  {'ID':<12} {'Dir':<5} {'Type':<9} {'Swept':<6} "
        f"{'Entry':>8} {'Exit':>8} {'Pips':>7} {'USD':>9}  Outcome"
    )
    print("  " + "─" * 82)
    for t in trades:
        print(
            f"  {t.trade_id:<12} {t.direction:<5} {t.trade_type:<9} {t.swept_tf:<6} "
            f"{t.entry:>8.2f} {t.exit_price:>8.2f} "
            f"{t.pnl_pips:>+7.1f} {t.pnl_usd:>+9.2f}  {t.outcome}"
        )
    print()


def _print_banner():
    print("\n" + "═" * 55)
    print("   XAUUSD Gold Trading Bot — Backtest")
    print("   Deriv data  ·  Walk-forward simulation")
    print("═" * 55 + "\n")


if __name__ == "__main__":
    main()
