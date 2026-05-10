"""
executor_factory.py — Returns the configured trade executor.

This is the only place in the codebase that knows which executor classes exist.
All other modules receive a BaseExecutor and never import a specific executor.

To add a new broker:
  1. Create your_broker_executor.py implementing BaseExecutor
  2. Add an entry to _EXECUTORS below
  3. Set EXECUTION_BROKER in config.py — nothing else needs to change
"""

import config
from executor_base import BaseExecutor


# ── Registry of available executors ──────────────────────────────────────────
# Imported lazily so that missing optional dependencies (e.g. MetaTrader5)
# don't crash the bot when a different executor is selected.

def _load_mt5():
    from mt5_executor import MT5Executor
    return MT5Executor()

def _load_deriv_multipliers():
    from deriv_executor import DerivMultipliersExecutor
    return DerivMultipliersExecutor()


_EXECUTORS = {
    "mt5":                _load_mt5,
    "deriv_multipliers":  _load_deriv_multipliers,
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_executor() -> BaseExecutor:
    """
    Return a ready-to-use executor instance based on config.EXECUTION_BROKER.

    Raises:
        ValueError if EXECUTION_BROKER is not a recognised key.
    """
    broker = config.EXECUTION_BROKER

    loader = _EXECUTORS.get(broker)
    if loader is None:
        valid = list(_EXECUTORS.keys())
        raise ValueError(
            f"Unknown EXECUTION_BROKER: {broker!r}. "
            f"Valid options: {valid}"
        )

    return loader()
