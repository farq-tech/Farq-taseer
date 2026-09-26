"""Farq's central credit ledger, spoken to server-to-server.

The ONLY authoritative ledger is billing.credit_ledger inside the Farq monorepo API
(api.farq.sa). Taseer holds no balance of its own: when TASEER_LEDGER_ENABLED is on,
every item debit in POST /v1/requests is a call to these endpoints, keyed by the
customer's *Farq* user id (users.farq_user_id) and idempotent per item
(item:<request_id>:<need> / reversal:item:<request_id>:<need>).

Fail closed: an answer that is not a clear yes (network trouble, a 5xx, a timeout)
means the item is NOT granted. The caller reverses whatever the same request already
consumed and refuses the request; a reverse that itself fails is logged loudly and is
safe to retry later because the keys are idempotent.

The GET balance is display-only - it decorates the entitlement so the app can show a
number - and is cached briefly in-process. It is never what decides a debit; only the
consume endpoint decides.

Off unless both FARQ_BILLING_URL and BILLING_S2S_SECRET are set.
"""

from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("farq.billing")

TIMEOUT_SECONDS = 5.0
BALANCE_CACHE_SECONDS = 30.0  # display-only; must stay under a minute
PRODUCT = "taseer"


class BillingUnavailable(Exception):
    """The central ledger did not give a clear answer. The item must not be granted."""


class Billing:
    def __init__(self, base_url: str | None = None, secret: str | None = None,
                 client: httpx.Client | None = None, timeout: float = TIMEOUT_SECONDS):
        self.base_url = (base_url if base_url is not None else os.environ.get("FARQ_BILLING_URL", "")).strip().rstrip("/")
        self.secret = (secret if secret is not None else os.environ.get("BILLING_S2S_SECRET", "")).strip()
        self.timeout = timeout
        # Tests hand in a client with an httpx.MockTransport speaking the same contract.
        self._client = client or httpx.Client(timeout=timeout)
        self._balance_cache: dict[str, tuple[int, float]] = {}

    def configured(self) -> bool:
        return bool(self.base_url and self.secret)

    def _post(self, path: str, payload: dict) -> httpx.Response:
        if not self.configured():
            raise BillingUnavailable("FARQ_BILLING_URL / BILLING_S2S_SECRET are not set")
        try:
            return self._client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {self.secret}", "Accept": "application/json"},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - no clear yes means not granted
            raise BillingUnavailable(f"billing unreachable: {exc}") from exc

    def consume(self, farq_user_id: str, idempotency_key: str, *, amount: int = 1,
                reference: dict | None = None) -> dict:
        """One item's debit. {"ok": True, "balance", "replayed"} when granted (a replay of
        the same key is a success, never a conflict); {"ok": False, "code", "balance"} when
        the central ledger says no (402 insufficient, 404 unknown user). Anything else
        raises BillingUnavailable - fail closed."""
        response = self._post(
            "/api/billing/credits/consume",
            {"user_id": farq_user_id, "product": PRODUCT, "amount": amount,
             "idempotency_key": idempotency_key, "reference": reference or {}},
        )
        self._balance_cache.pop(farq_user_id, None)
        if response.status_code == 200:
            data = self._json(response)
            return {"ok": True, "balance": data.get("balance"), "replayed": bool(data.get("replayed"))}
        if response.status_code == 402:
            data = self._json(response)
            return {"ok": False, "code": data.get("code") or "INSUFFICIENT_CREDITS", "balance": data.get("balance")}
        if response.status_code == 404:
            return {"ok": False, "code": "UNKNOWN_USER", "balance": None}
        raise BillingUnavailable(f"consume answered HTTP {response.status_code}")

    def reverse(self, farq_user_id: str, idempotency_key: str, consume_key: str) -> bool:
        """Give one consume back. True when reversed (or already reversed - idempotent);
        False when the central ledger never saw that consume (CONSUME_NOT_FOUND), which
        for a cleanup means there is nothing to give back. 5xx/network raises."""
        response = self._post(
            "/api/billing/credits/reverse",
            {"user_id": farq_user_id, "product": PRODUCT,
             "idempotency_key": idempotency_key, "consume_key": consume_key},
        )
        self._balance_cache.pop(farq_user_id, None)
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise BillingUnavailable(f"reverse answered HTTP {response.status_code}")

    def balance(self, farq_user_id: str) -> int | None:
        """The central balance, for DISPLAY only - never for a debit decision. Cached
        in-process for a few seconds; None when it cannot be read right now."""
        if not self.configured():
            return None
        kept = self._balance_cache.get(farq_user_id)
        if kept and kept[1] > time.monotonic():
            return kept[0]
        try:
            response = self._client.get(
                f"{self.base_url}/api/billing/credits/balance",
                params={"user_id": farq_user_id, "product": PRODUCT},
                headers={"Authorization": f"Bearer {self.secret}", "Accept": "application/json"},
                timeout=self.timeout,
            )
        except Exception:  # noqa: BLE001 - the display degrades, the debit path does not care
            log.warning("billing balance unreachable", exc_info=True)
            return None
        if response.status_code != 200:
            return None
        value = self._json(response).get("balance")
        if not isinstance(value, int):
            return None
        self._balance_cache[farq_user_id] = (value, time.monotonic() + BALANCE_CACHE_SECONDS)
        return value

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
