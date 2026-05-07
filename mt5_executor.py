"""
mt5_executor.py — MetaTrader 5 trade execution.

IMPORTANT: This module handles execution ONLY.
All market data is sourced exclusively from OANDA (oanda_client.py).

Execution logic uses a hybrid model:
  ≤ MARKET_ORDER_MAX_DISTANCE_PIPS  → Market order (immediate fill)
  ≤ LIMIT_ORDER_MAX_DISTANCE_PIPS   → Limit order  (expires after N hours)
  > LIMIT_ORDER_MAX_DISTANCE_PIPS   → Skip (signal too stale)
"""

import datetime
from typing import Optional

import MetaTrader5 as mt5

import config
from logger import log_event


# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────

def connect() -> bool:
    """Initialise the MT5 terminal connection. Returns True on success."""
    if not mt5.initialize():
        log_event(f"MT5 initialise failed: {mt5.last_error()}", level="ERROR")
        return False
    info = mt5.account_info()
    log_event(f"MT5 connected: account #{info.login}, balance ${info.balance:,.2f}")
    return True


def disconnect():
    """Cleanly shut down the MT5 connection."""
    mt5.shutdown()
    log_event("MT5 disconnected.")


def get_balance() -> float:
    """Return the current MT5 account balance in USD."""
    info = mt5.account_info()
    return float(info.balance) if info else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Trade placement
# ─────────────────────────────────────────────────────────────────────────────

def place_trade(
    direction: str,
    entry:     float,
    sl:        float,
    tp:        float,
    lot_size:  float,
    comment:   str = "",
) -> Optional[int]:
    """
    Place a trade using hybrid execution logic.

    Compares current price to the signal entry price and decides
    whether to use a market order, limit order, or skip the signal.

    Returns the MT5 ticket number on success, or None on failure/skip.
    """
    current_price = _get_current_price(direction)
    if current_price is None:
        log_event("Cannot place trade — failed to read current price from MT5.", level="ERROR")
        return None

    distance_pips = abs(current_price - entry) / config.PIP_SIZE

    if distance_pips <= config.MARKET_ORDER_MAX_DISTANCE_PIPS:
        log_event(f"Placing MARKET order ({distance_pips:.1f} pips from entry)")
        return _market_order(direction, sl, tp, lot_size, comment)

    elif distance_pips <= config.LIMIT_ORDER_MAX_DISTANCE_PIPS:
        log_event(f"Placing LIMIT order ({distance_pips:.1f} pips from entry)")
        return _limit_order(direction, entry, sl, tp, lot_size, comment)

    else:
        log_event(
            f"Signal skipped — price is {distance_pips:.1f} pips from entry "
            f"(max: {config.LIMIT_ORDER_MAX_DISTANCE_PIPS} pips)."
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Position management
# ─────────────────────────────────────────────────────────────────────────────

def close_partial(ticket: int, lots_to_close: float, comment: str = "") -> bool:
    """Close a partial portion of an open position."""
    position = _get_position(ticket)
    if not position:
        log_event(f"Partial close failed — ticket {ticket} not found.", level="ERROR")
        return False

    close_type = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

    request = {
        "action":    mt5.TRADE_ACTION_DEAL,
        "symbol":    config.MT5_SYMBOL,
        "volume":    lots_to_close,
        "type":      close_type,
        "position":  ticket,
        "price":     price,
        "deviation": config.DEVIATION_POINTS,
        "magic":     config.MAGIC_NUMBER,
        "comment":   comment,
    }
    result = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if not success:
        log_event(f"Partial close failed: code {result.retcode} — {result.comment}", level="ERROR")
    else:
        log_event(f"Partial close: {lots_to_close} lots closed on ticket {ticket}")
    return success


def close_full(ticket: int, comment: str = "") -> bool:
    """Close an entire open position."""
    position = _get_position(ticket)
    if not position:
        log_event(f"Full close failed — ticket {ticket} not found.", level="ERROR")
        return False
    return close_partial(ticket, position.volume, comment)


def modify_sl(ticket: int, new_sl: float) -> bool:
    """Modify the stop loss of an open position."""
    return _modify_sltp(ticket, new_sl=new_sl)


def modify_tp(ticket: int, new_tp: float) -> bool:
    """Modify the take profit of an open position."""
    return _modify_sltp(ticket, new_tp=new_tp)


def _modify_sltp(
    ticket: int,
    new_sl: Optional[float] = None,
    new_tp: Optional[float] = None,
) -> bool:
    position = _get_position(ticket)
    if not position:
        return False

    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl":       new_sl if new_sl is not None else position.sl,
        "tp":       new_tp if new_tp is not None else position.tp,
    }
    result  = mt5.order_send(request)
    success = result.retcode == mt5.TRADE_RETCODE_DONE

    if not success:
        log_event(f"SL/TP modify failed: code {result.retcode} — {result.comment}", level="ERROR")
    return success


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_current_price(direction: str) -> Optional[float]:
    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
    if not tick:
        return None
    return tick.ask if direction == "BUY" else tick.bid


def _get_position(ticket: int):
    positions = mt5.positions_get(ticket=ticket)
    return positions[0] if positions else None


def _market_order(
    direction: str, sl: float, tp: float, lots: float, comment: str
) -> Optional[int]:
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    tick  = mt5.symbol_info_tick(config.MT5_SYMBOL)
    price = tick.ask if direction == "BUY" else tick.bid

    request = {
        "action":       mt5.TRADE_ACTION_DEAL,
        "symbol":       config.MT5_SYMBOL,
        "volume":       lots,
        "type":         order_type,
        "price":        price,
        "sl":           sl,
        "tp":           tp,
        "deviation":    config.DEVIATION_POINTS,
        "magic":        config.MAGIC_NUMBER,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log_event(f"Market order failed: code {result.retcode} — {result.comment}", level="ERROR")
        return None

    log_event(f"Market order placed: ticket {result.order} | {lots} lots {direction} @ {price:.2f}")
    return result.order


def _limit_order(
    direction: str, entry: float, sl: float, tp: float, lots: float, comment: str
) -> Optional[int]:
    order_type = mt5.ORDER_TYPE_BUY_LIMIT if direction == "BUY" else mt5.ORDER_TYPE_SELL_LIMIT
    expiry     = datetime.datetime.now() + datetime.timedelta(
        hours=config.LIMIT_ORDER_EXPIRY_HOURS
    )

    request = {
        "action":       mt5.TRADE_ACTION_PENDING,
        "symbol":       config.MT5_SYMBOL,
        "volume":       lots,
        "type":         order_type,
        "price":        entry,
        "sl":           sl,
        "tp":           tp,
        "deviation":    config.DEVIATION_POINTS,
        "magic":        config.MAGIC_NUMBER,
        "comment":      comment,
        "type_time":    mt5.ORDER_TIME_SPECIFIED,
        "expiration":   int(expiry.timestamp()),
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    result = mt5.order_send(request)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log_event(f"Limit order failed: code {result.retcode} — {result.comment}", level="ERROR")
        return None

    log_event(f"Limit order placed: ticket {result.order} | {lots} lots {direction} @ {entry:.2f}")
    return result.order
