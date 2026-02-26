from __future__ import annotations

import argparse
import logging

from config import default_strategy_config, default_risk_config
from data_loader import connect, shutdown
from live_mt5 import run_live

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ari-Gaup Gold Live Trader")
    parser.add_argument(
        "--balance",
        type=float,
        default=500.0,
        help="Your current account balance (used for position sizing).",
    )
    parser.add_argument("--login", type=int, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument("--server", type=str, default=None)
    parser.add_argument(
        "--poll", type=int, default=15, help="Poll interval in seconds."
    )
    args = parser.parse_args()

    connect(login=args.login, password=args.password, server=args.server)
    try:
        cfg = default_strategy_config()
        risk_cfg = default_risk_config()
        run_live(cfg, risk_cfg, balance=args.balance, poll_seconds=args.poll)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
