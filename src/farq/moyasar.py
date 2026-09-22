"""Server-side Moyasar client. The secret key never leaves this process.

Payments are created client-side (Moyasar.js, using the publishable key) so
Apple Pay's merchant-validation flow works, but nothing here trusts that. The
backend always re-fetches the payment from Moyasar with the secret key before
touching a subscription. See docs/payments_setup.md for the required env vars.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx


class MoyasarError(RuntimeError):
    pass


class MoyasarNotConfigured(MoyasarError):
    pass


@dataclass(frozen=True)
class MoyasarPayment:
    id: str
    status: str
    amount: int
    currency: str
    fee: int | None
    refunded: bool
    metadata: dict
    source_type: str | None
    raw: dict

    @classmethod
    def from_json(cls, data: dict) -> "MoyasarPayment":
        source = data.get("source") or {}
        return cls(
            id=data["id"],
            status=data["status"],
            amount=data["amount"],
            currency=data["currency"],
            fee=data.get("fee"),
            refunded=bool(data.get("refunded")),
            metadata=data.get("metadata") or {},
            source_type=source.get("type"),
            raw=data,
        )


class MoyasarClient:
    def __init__(self, secret_key: str | None, base_url: str = "https://api.moyasar.com/v1", timeout: float = 15.0):
        self._secret_key = secret_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _auth_header(self) -> dict:
        if not self._secret_key:
            raise MoyasarNotConfigured("MOYASAR_SECRET_KEY is not set")
        token = base64.b64encode(f"{self._secret_key}:".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def fetch_payment(self, payment_id: str) -> MoyasarPayment:
        """GET /v1/payments/{id} - the only source of truth for payment state."""
        try:
            response = httpx.get(
                f"{self._base_url}/payments/{payment_id}",
                headers=self._auth_header(),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise MoyasarError(f"could not reach Moyasar: {exc}") from exc
        if response.status_code == 404:
            raise MoyasarError("payment not found at Moyasar")
        if response.status_code >= 400:
            raise MoyasarError(f"Moyasar fetch_payment failed: {response.status_code} {response.text[:300]}")
        return MoyasarPayment.from_json(response.json())

    def create_payment(self, *, amount: int, currency: str, description: str, callback_url: str, source: dict, metadata: dict, idempotency_id: str) -> MoyasarPayment:
        """POST /v1/payments - used by the test suite and any server-initiated
        flow. The primary production path lets Moyasar.js create the payment
        client-side (required for Apple Pay's merchant validation); this
        exists so the full flow can be exercised without a browser.
        """
        payload = {
            "id": idempotency_id,
            "amount": amount,
            "currency": currency,
            "description": description,
            "callback_url": callback_url,
            "source": source,
            "metadata": metadata,
        }
        try:
            response = httpx.post(
                f"{self._base_url}/payments",
                headers=self._auth_header(),
                json=payload,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise MoyasarError(f"could not reach Moyasar: {exc}") from exc
        if response.status_code >= 400:
            raise MoyasarError(f"Moyasar create_payment failed: {response.status_code} {response.text[:300]}")
        return MoyasarPayment.from_json(response.json())


def verify_webhook_secret(payload: dict, headers: dict, configured_secret: str | None) -> bool:
    """Moyasar echoes a shared secret_token in the webhook body (and, on newer
    accounts, an x-moyasar-token header). Compare against whichever is
    configured; if nothing is configured, refuse everything rather than
    silently trust an unauthenticated webhook.
    """
    if not configured_secret:
        return False
    body_token = str(payload.get("secret_token") or "")
    header_token = str(headers.get("x-moyasar-token") or headers.get("X-Moyasar-Token") or "")
    import hmac

    return hmac.compare_digest(body_token, configured_secret) or hmac.compare_digest(header_token, configured_secret)
