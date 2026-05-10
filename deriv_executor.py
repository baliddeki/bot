"""
deriv_executor.py — Deriv Multipliers trade execution via WebSocket API.

Implements BaseExecutor using Deriv's native Multipliers product.

What are Multipliers?
  Deriv Multipliers are leveraged CFD-like contracts where:
  - You put up a stake (e.g. $10)
  - Your P&L is: stake × multiplier × % price move
  - You can set stop loss and take profit in price terms
  - Max loss is capped at your stake (no margin calls)

Position sizing:
  The BaseExecutor interface takes lot_size. This executor converts it to
  a stake amount using:
    stake = risk_amount / (sl_pips × pip_value_per_unit)
  where pip_value_per_unit is derived from the multiplier.

Deriv API docs: https://api.deriv.com/
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Optional

import websockets

import config
from executor_base import BaseExecutor
from logger import log_event


# ─────────────────────────────────────────────────────────────────────────────
# Internal trade record (kept in memory — Deriv has no persistent state API)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _DerivTrade:
    contract_id:   int       # Deriv contract ID
    direction:     str       # "BUY" or "SELL"
    entry_price:   float
    stake:         float     # Original stake in USD
    sl:            float
    tp:            float


class DerivMultipliersExecutor(BaseExecutor):
    """
    Executes trades via Deriv's Multipliers product.

    Trade IDs returned are internal UUID strings that map to Deriv contract IDs.
    The internal registry (_trades) tracks open positions for the bot's lifetime.
    On restart, open positions on Deriv's side will still exist but won't be
    tracked — handle manually or persist _trades to disk for robustness.
    """

    def __init__(self):
        # Internal registry: bot trade ID → _DerivTrade
        self._trades: dict[str, _DerivTrade] = {}

    # ── Connection lifecycle ──────────────────────────────────────────────

    def connect(self) -> bool:
        """Authenticate with Deriv and confirm the account is reachable."""
        try:
            response = self._run(self._authorize())
            account  = response.get("authorize", {})
            balance  = account.get("balance", "?")
            currency = account.get("currency", "USD")
            loginid  = account.get("loginid", "?")
            log_event(
                f"Deriv connected: account {loginid} | "
                f"balance {currency} {balance}"
            )
            return True
        except Exception as exc:
            log_event(f"Deriv connect failed: {exc}", level="ERROR")
            return False

    def disconnect(self):
        """No persistent connection to close — logs shutdown."""
        log_event("Deriv executor disconnected.")

    def get_balance(self) -> float:
        """Fetch the current account balance via the Deriv API."""
        try:
            response = self._run(self._ws_call({"balance": 1, "subscribe": 0}))
            return float(response.get("balance", {}).get("balance", 0.0))
        except Exception as exc:
            log_event(f"Deriv get_balance failed: {exc}", level="ERROR")
            return 0.0

    # ── Order placement ───────────────────────────────────────────────────

    def place_trade(
        self,
        direction: str,
        entry:     float,
        sl:        float,
        tp:        float,
        lot_size:  float,
        comment:   str = "",
    ) -> Optional[str]:
        """
        Open a Deriv Multipliers contract.

        lot_size is converted to a USD stake amount internally.
        SL and TP are passed as price values — Deriv handles them natively.
        """
        stake = self._lot_size_to_stake(lot_size, entry, sl)
        if stake is None:
            return None

        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

        payload = {
            "buy": 1,
            "price": stake,
            "parameters": {
                "contract_type":    contract_type,
                "symbol":           config.SYMBOL,
                "multiplier":       config.DERIV_MULTIPLIER,
                "stop_loss":        abs(entry - sl),     # Distance in price units
                "take_profit":      abs(tp - entry),     # Distance in price units
                "basis":            "stake",
                "currency":         "USD",
            },
        }

        try:
            response    = self._run(self._authenticated_call(payload))
            buy_receipt = response.get("buy", {})
            contract_id = buy_receipt.get("contract_id")

            if not contract_id:
                log_event(
                    f"Deriv: buy failed — no contract_id in response: {response}",
                    level="ERROR",
                )
                return None

            trade_id = uuid.uuid4().hex[:10].upper()
            self._trades[trade_id] = _DerivTrade(
                contract_id = contract_id,
                direction   = direction,
                entry_price = float(buy_receipt.get("buy_price", entry)),
                stake       = stake,
                sl          = sl,
                tp          = tp,
            )

            log_event(
                f"Deriv: contract opened | ID {contract_id} | "
                f"{direction} {config.SYMBOL} | stake ${stake:.2f} | "
                f"multiplier ×{config.DERIV_MULTIPLIER}"
            )
            return trade_id

        except Exception as exc:
            log_event(f"Deriv: place_trade failed: {exc}", level="ERROR")
            return None

    # ── Position management ───────────────────────────────────────────────

    def close_partial(self, trade_id: str, lot_fraction: float, comment: str = "") -> bool:
        """
        Deriv Multipliers does not support partial closes natively.

        This closes the full position. Partial close logic in trade_manager.py
        will not be executed at the broker level — the position fully closes
        and a new position should be opened for the remaining portion if needed.

        For true partial close support, switch to EXECUTION_BROKER = "mt5".
        """
        log_event(
            f"Deriv: partial close not supported on Multipliers — "
            f"closing full position for {trade_id}."
        )
        return self.close_full(trade_id, comment)

    def close_full(self, trade_id: str, comment: str = "") -> bool:
        """Sell (close) a Deriv Multipliers contract."""
        trade = self._trades.get(trade_id)
        if not trade:
            log_event(
                f"Deriv: close failed — trade ID {trade_id} not in registry.",
                level="ERROR",
            )
            return False

        payload = {"sell": trade.contract_id, "price": 0}   # price=0 = sell at market

        try:
            response = self._run(self._authenticated_call(payload))
            sold     = response.get("sell", {})

            if "contract_id" not in sold:
                log_event(
                    f"Deriv: close failed — unexpected response: {response}",
                    level="ERROR",
                )
                return False

            sold_price = sold.get("sold_for", "?")
            log_event(
                f"Deriv: contract {trade.contract_id} closed @ {sold_price} | {comment}"
            )
            del self._trades[trade_id]
            return True

        except Exception as exc:
            log_event(f"Deriv: close_full failed: {exc}", level="ERROR")
            return False

    def modify_sl(self, trade_id: str, new_sl: float) -> bool:
        """Update the stop loss on an open Multipliers contract."""
        return self._update_contract(trade_id, new_sl=new_sl)

    def modify_tp(self, trade_id: str, new_tp: float) -> bool:
        """Update the take profit on an open Multipliers contract."""
        return self._update_contract(trade_id, new_tp=new_tp)

    # ── Private helpers ───────────────────────────────────────────────────

    def _update_contract(
        self,
        trade_id: str,
        new_sl:   Optional[float] = None,
        new_tp:   Optional[float] = None,
    ) -> bool:
        """Send a contract_update request to modify SL or TP."""
        trade = self._trades.get(trade_id)
        if not trade:
            return False

        payload: dict = {"contract_update": 1, "contract_id": trade.contract_id}

        if new_sl is not None:
            payload["limit_order"] = {
                "stop_loss": {"order_type": "stop", "order_amount": abs(trade.entry_price - new_sl)}
            }
            trade.sl = new_sl

        if new_tp is not None:
            payload.setdefault("limit_order", {})
            payload["limit_order"]["take_profit"] = {
                "order_type": "limit",
                "order_amount": abs(new_tp - trade.entry_price),
            }
            trade.tp = new_tp

        try:
            self._run(self._authenticated_call(payload))
            log_event(
                f"Deriv: contract {trade.contract_id} updated — "
                f"SL={trade.sl:.2f} TP={trade.tp:.2f}"
            )
            return True
        except Exception as exc:
            log_event(f"Deriv: contract_update failed: {exc}", level="ERROR")
            return False

    def _lot_size_to_stake(
        self, lot_size: float, entry: float, sl: float
    ) -> Optional[float]:
        """
        Convert a lot-based position size to a Deriv stake amount.

        Formula:
          pip_distance = |entry - sl| / pip_size
          risk_per_lot = pip_distance × pip_value_per_lot   ($10 per pip per lot)
          total_risk   = lot_size × risk_per_lot
          stake        = total_risk   (the stake IS the max loss on a Multiplier)

        The multiplier amplifies the P&L, not the max loss.
        """
        pip_distance = abs(entry - sl) / config.PIP_SIZE
        if pip_distance == 0:
            log_event("Deriv: zero pip distance — cannot size position.", level="ERROR")
            return None

        pip_value_per_lot = 10.0
        stake = lot_size * pip_distance * pip_value_per_lot

        stake = max(config.DERIV_MIN_STAKE, min(config.DERIV_MAX_STAKE, round(stake, 2)))
        return stake

    # ── WebSocket transport ───────────────────────────────────────────────

    def _run(self, coro) -> dict:
        """Run an async coroutine synchronously."""
        return asyncio.run(coro)

    async def _ws_call(self, payload: dict) -> dict:
        """Send one unauthenticated WebSocket request."""
        url = f"{config.DERIV_WS_URL}?app_id={config.DERIV_APP_ID}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps(payload))
            response = json.loads(await ws.recv())
        if "error" in response:
            raise RuntimeError(response["error"].get("message", str(response["error"])))
        return response

    async def _authorize(self) -> dict:
        """Authenticate with the Deriv API token."""
        url = f"{config.DERIV_WS_URL}?app_id={config.DERIV_APP_ID}"
        async with websockets.connect(url) as ws:
            await ws.send(json.dumps({"authorize": config.DERIV_API_TOKEN}))
            response = json.loads(await ws.recv())
        if "error" in response:
            raise RuntimeError(response["error"].get("message", str(response["error"])))
        return response

    async def _authenticated_call(self, payload: dict) -> dict:
        """Authenticate then send one request on the same WebSocket connection."""
        url = f"{config.DERIV_WS_URL}?app_id={config.DERIV_APP_ID}"
        async with websockets.connect(url) as ws:
            # Step 1 — authorize
            await ws.send(json.dumps({"authorize": config.DERIV_API_TOKEN}))
            auth_response = json.loads(await ws.recv())
            if "error" in auth_response:
                raise RuntimeError(
                    auth_response["error"].get("message", str(auth_response["error"]))
                )

            # Step 2 — actual request
            await ws.send(json.dumps(payload))
            response = json.loads(await ws.recv())

        if "error" in response:
            raise RuntimeError(response["error"].get("message", str(response["error"])))
        return response
