"""
logger.py — Trade and event logging.

Two log outputs:
  trades.csv  — One row per completed trade, suitable for Excel analysis.
  events.log  — Timestamped text log of all bot activity.
"""

import csv
import os
from datetime import datetime

import config


# ─────────────────────────────────────────────────────────────────────────────
# Trade log CSV schema
# ─────────────────────────────────────────────────────────────────────────────

TRADE_FIELDS = [
    "trade_id",
    "timestamp",
    "symbol",
    "direction",
    "trade_type",
    "swept_tf",
    "ob_tf",
    "fvg_tf",
    "entry",
    "sl",
    "tp1",
    "tp2",
    "lot_size",
    "risk_xauusd_percent",
    "risk_amount_usd",
    "outcome",
    "partial_closed",
    "exit_price",
    "exit_time",
    "pnl_pips",
    "pnl_usd",
    "balance_before",
    "balance_after",
    "setup_chart_path",
    "notes",
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def log_trade(row: dict):
    """
    Append a single trade record to the trade log CSV.

    Any fields not provided in `row` are written as empty strings.
    The CSV header is created automatically on first write.
    """
    filepath = _trade_log_path()
    _ensure_csv_header(filepath)

    full_row = {field: row.get(field, "") for field in TRADE_FIELDS}

    with open(filepath, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        writer.writerow(full_row)


def log_event(message: str, level: str = "INFO"):
    """
    Write a timestamped event to both the console and the event log file.

    Args:
        message: The log message.
        level:   "INFO", "WARNING", or "ERROR".
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level:7s}] {message}"
    print(line)

    log_path = os.path.join(config.LOG_DIRECTORY, config.EVENT_LOG_FILE)
    os.makedirs(config.LOG_DIRECTORY, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _trade_log_path() -> str:
    os.makedirs(config.LOG_DIRECTORY, exist_ok=True)
    return os.path.join(config.LOG_DIRECTORY, config.TRADE_LOG_FILE)


def _ensure_csv_header(filepath: str):
    """Write the CSV header row if the file does not yet exist."""
    if not os.path.exists(filepath):
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
            writer.writeheader()
