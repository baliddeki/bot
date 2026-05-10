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
from datetime import datetime

import pandas as pd

import config
from deriv_client import fetch_all_timeframes
from backtest_engine import BacktestEngine, ClosedTrade
from logger import log_event

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main():
    _print_banner()

    date_from = config.BACKTEST_DATE_FROM
    date_to = config.BACKTEST_DATE_TO
    balance = config.BACKTEST_INITIAL_BALANCE

    print(f"  Symbol        : {config.SYMBOL}")
    print(f"  Period        : {date_from.date()}  →  {date_to.date()}")
    print(f"  Start balance : ${balance:,.2f}")
    print(f"  Account mode  : {config.ACCOUNT_MODE}")
    print()

    # ── Fetch historical data ─────────────────────────────────────────────
    log_event("Fetching historical data from Deriv...")
    candle_data = fetch_all_timeframes(
        symbol=config.SYMBOL,
        date_from=date_from,
        date_to=date_to,
    )

    # Abort if the primary timeframe is empty
    if candle_data.get("H1") is None or candle_data["H1"].empty:
        log_event("No H1 data returned — check date range and symbol.", level="ERROR")
        sys.exit(1)

    h1_count = len(candle_data["H1"])
    log_event(f"Data ready. H1 candles: {h1_count}")

    # ── Run simulation ────────────────────────────────────────────────────
    engine = BacktestEngine(candle_data, initial_balance=balance)
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
        return

    # ── Aggregate stats ───────────────────────────────────────────────────
    total = len(trades)
    wins = [t for t in trades if "TP" in t.outcome]
    losses = [t for t in trades if "SL" in t.outcome]
    partials = [t for t in trades if t.partial_closed]

    total_pips = sum(t.pnl_pips for t in trades)
    total_usd = sum(t.pnl_usd for t in trades)
    win_pips = sum(t.pnl_pips for t in wins)
    loss_pips = sum(t.pnl_pips for t in losses)
    avg_win_pips = win_pips / len(wins) if wins else 0
    avg_los_pips = loss_pips / len(losses) if losses else 0
    win_rate = len(wins) / total * 100 if total else 0

    final_balance = initial_balance + total_usd
    return_pct = (total_usd / initial_balance) * 100

    # ── Max drawdown ──────────────────────────────────────────────────────
    running = initial_balance
    peak = initial_balance
    max_dd = 0.0
    for t in trades:
        running += t.pnl_usd
        peak = max(peak, running)
        drawdown = (peak - running) / peak * 100
        max_dd = max(max_dd, drawdown)

    # ── Profit factor ─────────────────────────────────────────────────────
    gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # ── By trade type ─────────────────────────────────────────────────────
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
        intraday_wins = sum(1 for t in intraday if "TP" in t.outcome)
        print(
            f"  Intraday      : {len(intraday)} trades | "
            f"{intraday_wins/len(intraday)*100:.1f}% win rate | "
            f"{sum(t.pnl_pips for t in intraday):+.1f} pips"
        )

    if swing:
        swing_wins = sum(1 for t in swing if "TP" in t.outcome)
        print(
            f"  Swing         : {len(swing)} trades | "
            f"{swing_wins/len(swing)*100:.1f}% win rate | "
            f"{sum(t.pnl_pips for t in swing):+.1f} pips"
        )

    print()
    print(f"  Charts saved  : {config.CHART_OUTPUT_DIR}/")
    print(f"  Trade log     : {config.LOG_DIRECTORY}/{config.TRADE_LOG_FILE}")
    print("═" * 55 + "\n")

    # ── Per-trade breakdown ───────────────────────────────────────────────
    print(
        f"  {'ID':<12} {'Dir':<5} {'Type':<9} {'Swept':<6} "
        f"{'Entry':>8} {'Exit':>8} {'Pips':>7} {'USD':>8}  Outcome"
    )
    print("  " + "─" * 80)
    for t in trades:
        pips_str = f"{t.pnl_pips:+.1f}"
        usd_str = f"${t.pnl_usd:+.2f}"
        print(
            f"  {t.trade_id:<12} {t.direction:<5} {t.trade_type:<9} {t.swept_tf:<6} "
            f"{t.entry:>8.2f} {t.exit_price:>8.2f} {pips_str:>7} {usd_str:>8}  {t.outcome}"
        )
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────


def _print_banner():
    print("\n" + "═" * 55)
    print("   XAUUSD Gold Trading Bot — Backtest")
    print("   Deriv data  ·  Walk-forward simulation")
    print("═" * 55 + "\n")


if __name__ == "__main__":
    main()
