from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd

from config import default_strategy_config, default_risk_config
from data_loader import connect, shutdown
from backtest import run_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="Ari-Gaup Gold Backtester")
    parser.add_argument(
        "--from", dest="dt_from", required=True, help="Start date YYYY-MM-DD"
    )
    parser.add_argument(
        "--to", dest="dt_to", required=True, help="End date   YYYY-MM-DD"
    )
    parser.add_argument("--balance", type=float, default=500.0, help="Starting balance")
    parser.add_argument("--login", type=int, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--server", type=str, default=None)
    args = parser.parse_args()

    connect(login=args.login, password=args.password, server=args.server)
    try:
        cfg = default_strategy_config()
        risk_cfg = default_risk_config()

        report = run_backtest(
            cfg,
            risk_cfg,
            dt.datetime.fromisoformat(args.dt_from),
            dt.datetime.fromisoformat(args.dt_to),
            args.balance,
        )

        print("\n=== Ari-Gaup Gold Backtest ===")
        print(f"Period:        {args.dt_from}  →  {args.dt_to}")
        print(f"Trades:        {len(report.trades)}")
        print(f"Start balance: ${report.start_balance:,.2f}")
        print(f"End balance:   ${report.end_balance:,.2f}")
        print(f"Win rate:      {report.win_rate * 100:.1f}%")
        print(f"Total return:  {report.total_return_pct:.2f}%")
        print(f"Max drawdown:  {report.max_drawdown_pct:.2f}%\n")

        if report.trades:
            rows = []
            for t in report.trades:
                rows.append(
                    {
                        "entry_time": t.entry_time,
                        "dir": t.direction,
                        "flag_tf": t.flag_tf,
                        "fvg_tf": t.fvg_tf,
                        "entry": round(t.entry, 3),
                        "sl": round(t.sl, 3),
                        "tp": round(t.tp, 3),
                        "outcome": t.outcome,
                        "pnl": round(t.pnl, 2),
                        "lots": t.lots,
                    }
                )
            df = pd.DataFrame(rows)
            print(df.tail(50).to_string(index=False))
    finally:
        shutdown()


if __name__ == "__main__":
    main()
