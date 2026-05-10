"""
deriv_executor.py — Deriv Multipliers trade execution via the new WebSocket API.

Authentication flow (new API):
  1. User logs in via OAuth2 → receives access_token (Bearer)
  2. POST /trading/v1/options/accounts/{accountId}/otp → receives a ready-to-use WebSocket URL
  3. All trading calls go through that authenticated WebSocket URL

Config required (config.py):
  DERIV_OAUTH_TOKEN  — your OAuth2 Bearer token (from logging in via auth.deriv.com)
  DERIV_ACCOUNT_ID   — your Options account ID (e.g. "DOT90004580")
  DERIV_APP_ID       — your registered App ID (sent in Deriv-App-ID header)

What are Multipliers?
  Deriv Multipliers are leveraged contracts where:
  - You stake an amount (e.g. $10)
  - P&L = stake × multiplier × % price move
  - Max loss is capped at your stake (no margin calls)
  - SL and TP are supported natively

Partial close limitation:
  Deriv Multipliers do NOT support partial closes.
  When close_partial() is called, it closes the full position.
  For true partial close support, use EXECUTION_BROKER = "mt5".
"""

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Optional

import requests
import websockets

import config
from executor_base import BaseExecutor
from logger import log_event


# ─────────────────────────────────────────────────────────────────────────────
# REST base URL
# ─────────────────────────────────────────────────────────────────────────────

_REST_BASE = "https://api.derivws.com"


# ─────────────────────────────────────────────────────────────────────────────
# Internal trade record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _DerivTrade:
    contract_id:  int
    direction:    str
    entry_price:  float
    stake:        float
    sl:           float
    tp:           float


class DerivMultipliersExecutor(BaseExecutor):
    """
    Executes trades via Deriv's Multipliers product using the new API.

    On startup, connect() obtains an authenticated WebSocket URL (OTP).
    All subsequent trade calls reuse auth for the session.
    """

    def __init__(self):
        self._ws_url:  Optional[str]              = None   # Authenticated WS URL from OTP
        self._trades:  dict[str, _DerivTrade]     = {}     # Bot trade ID → Deriv trade

    # ── Connection lifecycle ──────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Fetch account balance to verify credentials are valid,
        then obtain the authenticated WebSocket URL via OTP.
        """
        try:
            self._ws_url = self._get_otp_url()
            balance      = self.get_balance()
            log_event(
                f"Deriv connected: account {config.DERIV_ACCOUNT_ID} | "
                f"balance ${balance:,.2f}"
            )
            return True
        except Exception as exc:
            log_event(f"Deriv connect failed: {exc}", level="ERROR")
            return False

    def disconnect(self):
        self._ws_url = None
        log_event("Deriv executor disconnected.")

    def get_balance(self) -> float:
        """Fetch account balance via authenticated WebSocket."""
        try:
            response = self._run(self._trading_call({"balance": 1, "subscribe": 0}))
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
        SL and TP are passed as profit/loss distances from entry — Deriv handles them natively.
        """
        stake = self._lot_to_stake(lot_size, entry, sl)
        if stake is None:
            return None

        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        sl_distance   = abs(entry - sl)
        tp_distance   = abs(tp - entry)

        # Step 1 — get a price proposal
        proposal_payload = {
            "proposal": 1,
            "amount":          stake,
            "basis":           "stake",
            "contract_type":   contract_type,
            "currency":        "USD",
            "underlying_symbol": config.SYMBOL,
            "multiplier":      config.DERIV_MULTIPLIER,
            "limit_order": {
                "stop_loss":   sl_distance,
                "take_profit": tp_distance,
            },
        }

        try:
            proposal_response = self._run(self._trading_call(proposal_payload))
            proposal_id       = proposal_response.get("proposal", {}).get("id")

            if not proposal_id:
                log_event(
                    f"Deriv: no proposal ID in response: {proposal_response}",
                    level="ERROR",
                )
                return None

            # Step 2 — buy the contract
            buy_payload = {
                "buy":   proposal_id,
                "price": stake,
            }
            buy_response = self._run(self._trading_call(buy_payload))
            buy_receipt  = buy_response.get("buy", {})
            contract_id  = buy_receipt.get("contract_id")

            if not contract_id:
                log_event(
                    f"Deriv: buy failed — no contract_id: {buy_response}",
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
                f"{direction} {config.SYMBOL} | stake ${stake:.2f} | ×{config.DERIV_MULTIPLIER}"
            )
            return trade_id

        except Exception as exc:
            log_event(f"Deriv: place_trade failed: {exc}", level="ERROR")
            return None

    # ── Position management ───────────────────────────────────────────────

    def close_partial(self, trade_id: str, lot_fraction: float, comment: str = "") -> bool:
        """
        Deriv Multipliers does not support partial closes.
        Closes the full position instead and logs a warning.
        Switch to EXECUTION_BROKER = "mt5" for true partial close support.
        """
        log_event(
            f"Deriv: partial close not supported on Multipliers — "
            f"closing full position ({trade_id}).",
            level="WARNING",
        )
        return self.close_full(trade_id, comment)

    def close_full(self, trade_id: str, comment: str = "") -> bool:
        """Sell a Deriv Multipliers contract at market."""
        trade = self._trades.get(trade_id)
        if not trade:
            log_event(
                f"Deriv: close failed — trade {trade_id} not in registry.",
                level="ERROR",
            )
            return False

        payload = {"sell": trade.contract_id, "price": 0}   # price=0 → sell at market

        try:
            response = self._run(self._trading_call(payload))
            sold     = response.get("sell", {})

            if "contract_id" not in sold:
                log_event(f"Deriv: close failed — response: {response}", level="ERROR")
                return False

            log_event(
                f"Deriv: contract {trade.contract_id} closed @ {sold.get('sold_for', '?')} "
                f"| {comment}"
            )
            del self._trades[trade_id]
            return True

        except Exception as exc:
            log_event(f"Deriv: close_full failed: {exc}", level="ERROR")
            return False

    def modify_sl(self, trade_id: str, new_sl: float) -> bool:
        """Update stop loss on an open contract."""
        return self._update_contract(trade_id, new_sl=new_sl)

    def modify_tp(self, trade_id: str, new_tp: float) -> bool:
        """Update take profit on an open contract."""
        return self._update_contract(trade_id, new_tp=new_tp)

    # ── Private helpers ───────────────────────────────────────────────────

    def _update_contract(
        self,
        trade_id: str,
        new_sl:   Optional[float] = None,
        new_tp:   Optional[float] = None,
    ) -> bool:
        trade = self._trades.get(trade_id)
        if not trade:
            return False

        limit_order: dict = {}

        if new_sl is not None:
            limit_order["stop_loss"]   = abs(trade.entry_price - new_sl)
            trade.sl = new_sl

        if new_tp is not None:
            limit_order["take_profit"] = abs(new_tp - trade.entry_price)
            trade.tp = new_tp

        payload = {
            "contract_update": 1,
            "contract_id":     trade.contract_id,
            "limit_order":     limit_order,
        }

        try:
            self._run(self._trading_call(payload))
            log_event(
                f"Deriv: contract {trade.contract_id} updated — "
                f"SL={trade.sl:.2f} TP={trade.tp:.2f}"
            )
            return True
        except Exception as exc:
            log_event(f"Deriv: contract_update failed: {exc}", level="ERROR")
            return False

    def _lot_to_stake(self, lot_size: float, entry: float, sl: float) -> Optional[float]:
        """
        Convert lot-based position size to a Deriv stake amount.

        The stake IS the maximum loss on a Multipliers contract, so:
            stake = lot_size × pip_distance × pip_value_per_lot
        """
        pip_distance = abs(entry - sl) / config.PIP_SIZE
        if pip_distance == 0:
            log_event("Deriv: zero pip distance — cannot size position.", level="ERROR")
            return None

        stake = lot_size * pip_distance * 10.0   # $10 pip value per lot
        return max(config.DERIV_MIN_STAKE, min(config.DERIV_MAX_STAKE, round(stake, 2)))

    # ── OTP authentication ────────────────────────────────────────────────

    def _get_otp_url(self) -> str:
        """
        Call POST /trading/v1/options/accounts/{accountId}/otp via REST.
        Returns the ready-to-use authenticated WebSocket URL.
        """
        url     = f"{_REST_BASE}/trading/v1/options/accounts/{config.DERIV_ACCOUNT_ID}/otp"
        headers = {
            "Deriv-App-ID":  config.DERIV_APP_ID,
            "Authorization": f"Bearer {config.DERIV_OAUTH_TOKEN}",
        }
        response = requests.post(url, headers=headers, timeout=10)
        response.raise_for_status()

        ws_url = response.json().get("data", {}).get("url")
        if not ws_url:
            raise RuntimeError(f"No WebSocket URL in OTP response: {response.json()}")
        return ws_url

    def _run(self, coro) -> dict:
        """Run an async coroutine synchronously."""
        return asyncio.run(coro)

    async def _trading_call(self, payload: dict) -> dict:
        """
        Send one request over the authenticated WebSocket connection.
        Uses the OTP URL obtained during connect().
        """
        if not self._ws_url:
            raise RuntimeError("Not connected — call connect() first.")

        async with websockets.connect(self._ws_url) as ws:
            await ws.send(json.dumps(payload))
            response = json.loads(await ws.recv())

        if "error" in response:
            raise RuntimeError(response["error"].get("message", str(response["error"])))
        return response
