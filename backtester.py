"""
Backtester
===========
Simulates the OB + FVG strategy on historical data.
Uses the same detection logic as live trading.

Outputs results to Excel and console.
"""

import os
from datetime import datetime, timedelta
import pandas as pd
import config
from order_block import detect_order_blocks
from fvg_detector import detect_fvgs


class Backtester:
    def __init__(self, connection):
        """
        Args:
            connection: MT5Connection instance (for fetching historical data)
        """
        self.connection = connection
        self.trades = []
        self.balance = config.BACKTEST_INITIAL_BALANCE

    def run(self, date_from=None, date_to=None):
        """
        Run backtest over the given date range.

        Args:
            date_from: Start date (default: config.BACKTEST_DATE_FROM)
            date_to: End date (default: config.BACKTEST_DATE_TO)
        """
        date_from = date_from or config.BACKTEST_DATE_FROM
        date_to = date_to or config.BACKTEST_DATE_TO

        print(f"\nBacktest: {date_from.date()} to {date_to.date()}")
        print(f"Initial balance: ${self.balance:.2f}")
        print(f"Symbol: {config.SYMBOL}")
        print("-" * 50)

        # Fetch all 2H candles for the period
        candles_2h = self.connection.get_candles(
            config.OB_TIMEFRAME, from_time=date_from, to_time=date_to
        )

        if candles_2h is None or len(candles_2h) < 10:
            print("Not enough 2H data for backtest")
            return

        print(f"Loaded {len(candles_2h)} 2H candles")

        # Detect all order blocks
        obs = detect_order_blocks(candles_2h)
        print(f"Found {len(obs)} order blocks")

        # Process each OB
        for ob in obs:
            self._process_ob(ob)

        self._print_results()
        self._save_results()

    def _process_ob(self, ob):
        """Try to find an FVG and simulate the trade."""
        direction = ob["type"]
        start_time = ob["candle_c_time"]
        end_time = ob["candle_c_end"]

        # Scan lower timeframes for FVG
        fvg = self._find_fvg(direction, start_time, end_time)
        if fvg is None:
            return

        # Calculate entry, SL, TP
        sl_points = config.pips_to_points(config.SL_PIPS)
        tp1_points = config.pips_to_points(config.TP1_PIPS)
        tp2_points = config.pips_to_points(config.TP2_PIPS)

        if direction == "bullish":
            entry = fvg["zone_top"]
            sl = entry - sl_points
            tp1 = entry + tp1_points
            tp2 = entry + tp2_points
        else:
            entry = fvg["zone_bottom"]
            sl = entry + sl_points
            tp1 = entry - tp1_points
            tp2 = entry - tp2_points

        # Simulate: did price reach entry? Then did it hit SL or TP1 first?
        outcome = self._simulate_trade(direction, entry, sl, tp1, tp2, end_time)

        if outcome is None:
            return  # Price never reached entry

        # Calculate P&L
        tier = config.get_risk_tier(self.balance)
        risk_pct = tier["risk_per_trade"]
        risk_amount = self.balance * (risk_pct / 100.0)

        if outcome["result"] == "TP1":
            # Won TP1: 80% at TP1, 20% runner
            pnl_tp1 = risk_amount * (config.TP1_PIPS / config.SL_PIPS) * 0.8
            # Runner: could go to TP2 or BE+offset
            if outcome.get("tp2_hit"):
                pnl_runner = risk_amount * (config.TP2_PIPS / config.SL_PIPS) * 0.2
            else:
                # Stopped at BE + offset
                pnl_runner = (
                    risk_amount * (config.BE_OFFSET_PIPS / config.SL_PIPS) * 0.2
                )
            pnl = pnl_tp1 + pnl_runner
        elif outcome["result"] == "SL":
            pnl = -risk_amount
        else:
            pnl = 0

        self.balance += pnl

        trade = {
            "time": ob["candle_c_time"],
            "direction": direction,
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2),
            "result": outcome["result"],
            "tp2_hit": outcome.get("tp2_hit", False),
            "pnl": round(pnl, 2),
            "balance": round(self.balance, 2),
            "risk_pct": risk_pct,
            "fvg_tf": fvg.get("timeframe", ""),
        }
        self.trades.append(trade)

    def _find_fvg(self, direction, start_time, end_time):
        """Find FVG in the candle C window, checking TFs in order."""
        for tf in config.FVG_TIMEFRAMES:
            candles = self.connection.get_candles_in_window(tf, start_time, end_time)
            if candles is None or len(candles) < 3:
                continue
            fvgs = detect_fvgs(candles, direction)
            if fvgs:
                fvgs.sort(key=lambda x: x["time"])
                best = fvgs[0]
                best["timeframe"] = tf
                return best
        return None

    def _simulate_trade(self, direction, entry, sl, tp1, tp2, ob_end_time):
        """
        Simulate whether price reached entry, then check SL vs TP1 vs TP2.
        Uses 1-minute candles for accuracy.
        """
        # Look forward from OB end time (up to 5 days)
        sim_end = ob_end_time + timedelta(days=5)
        candles = self.connection.get_candles(
            "M1", from_time=ob_end_time, to_time=sim_end
        )

        if candles is None or len(candles) == 0:
            return None

        entry_filled = False
        tp1_hit = False

        for _, row in candles.iterrows():
            if not entry_filled:
                # Check if price reached our limit order
                if direction == "bullish" and row["low"] <= entry:
                    entry_filled = True
                elif direction == "bearish" and row["high"] >= entry:
                    entry_filled = True
                continue

            # Entry is filled, check SL and TP
            if direction == "bullish":
                if row["low"] <= sl:
                    if tp1_hit:
                        # Already took TP1, stopped at BE (still profitable)
                        return {"result": "TP1", "tp2_hit": False}
                    return {"result": "SL"}
                if not tp1_hit and row["high"] >= tp1:
                    tp1_hit = True
                    # Move SL to BE + offset
                    sl = entry + config.pips_to_points(config.BE_OFFSET_PIPS)
                if tp1_hit and row["high"] >= tp2:
                    return {"result": "TP1", "tp2_hit": True}

            else:  # bearish
                if row["high"] >= sl:
                    if tp1_hit:
                        return {"result": "TP1", "tp2_hit": False}
                    return {"result": "SL"}
                if not tp1_hit and row["low"] <= tp1:
                    tp1_hit = True
                    sl = entry - config.pips_to_points(config.BE_OFFSET_PIPS)
                if tp1_hit and row["low"] <= tp2:
                    return {"result": "TP1", "tp2_hit": True}

        # If we got here, trade is still open at end of sim window
        if tp1_hit:
            return {"result": "TP1", "tp2_hit": False}
        return None  # Never filled

    def _print_results(self):
        """Print backtest summary to console."""
        if not self.trades:
            print("\nNo trades executed")
            return

        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] < 0]
        total_pnl = sum(t["pnl"] for t in self.trades)
        win_rate = len(wins) / len(self.trades) * 100 if self.trades else 0

        print(f"\n{'='*50}")
        print(f"BACKTEST RESULTS")
        print(f"{'='*50}")
        print(f"Total trades:   {len(self.trades)}")
        print(f"Wins:           {len(wins)}")
        print(f"Losses:         {len(losses)}")
        print(f"Win rate:       {win_rate:.1f}%")
        print(f"Total P&L:      ${total_pnl:.2f}")
        print(f"Starting bal:   ${config.BACKTEST_INITIAL_BALANCE:.2f}")
        print(f"Final bal:      ${self.balance:.2f}")
        print(
            f"Return:         {((self.balance / config.BACKTEST_INITIAL_BALANCE) - 1) * 100:.1f}%"
        )

        if wins:
            avg_win = sum(t["pnl"] for t in wins) / len(wins)
            print(f"Avg win:        ${avg_win:.2f}")
        if losses:
            avg_loss = sum(t["pnl"] for t in losses) / len(losses)
            print(f"Avg loss:       ${avg_loss:.2f}")

        # Max drawdown
        peak = config.BACKTEST_INITIAL_BALANCE
        max_dd = 0
        bal = config.BACKTEST_INITIAL_BALANCE
        for t in self.trades:
            bal += t["pnl"]
            peak = max(peak, bal)
            dd = (peak - bal) / peak * 100
            max_dd = max(max_dd, dd)
        print(f"Max drawdown:   {max_dd:.1f}%")
        print(f"{'='*50}")

    def _save_results(self):
        """Save trade list to Excel."""
        if not self.trades:
            return

        os.makedirs(config.LOG_DIRECTORY, exist_ok=True)
        filepath = os.path.join(config.LOG_DIRECTORY, "backtest_results.xlsx")

        df = pd.DataFrame(self.trades)
        df.to_excel(filepath, index=False, sheet_name="Backtest")
        print(f"\nResults saved to {filepath}")
