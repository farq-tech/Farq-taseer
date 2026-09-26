"""A supplier's reply rings the customer's phone through the Farq iOS app.

Taseer runs inside the Farq app as a frame, where Web Push (farq.push) cannot reach the
phone. The Farq API owns the phone's APNs token, so Taseer asks it, server-to-server,
to push one message to the request owner's Farq account:

    POST {FARQ_API_URL}/api/push/notify   (Authorization: Bearer BILLING_S2S_SECRET)

Same service credential as the credit ledger, but its own base URL setting
(FARQ_API_URL, default https://api.farq.sa): setting FARQ_BILLING_URL would also switch
the billing client on. Best-effort: a push that fails is logged and dropped - the in-app
unread badge still carries the reply. Off without the secret, and silent for a customer
who never linked a Farq account.
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("farq.farq_push")

TIMEOUT_SECONDS = 5.0


def api_url() -> str:
    return (os.environ.get("FARQ_API_URL", "").strip() or "https://api.farq.sa").rstrip("/")


def configured() -> bool:
    return bool(os.environ.get("BILLING_S2S_SECRET", "").strip())


def reply_payload(farq_user_id: str, request_id: str, sender: str, body: str) -> dict:
    return {
        "user_id": farq_user_id,
        "title": sender or "مورد",
        "body": (body or "").strip()[:180] or "وصلك رد جديد على طلب التسعير",
        "path": f"/taseer?r={request_id}",
        # One banner per conversation on the lock screen: a newer reply replaces the older.
        "collapse_id": f"taseer:{request_id}"[:64],
    }


def notify_farq_reply(store, request_id: str, seller_id: str | None, body: str,
                      *, client: httpx.Client | None = None) -> bool:
    """Ask Farq to push this reply to the owner's phone. Returns whether a phone took it."""
    if not configured():
        return False
    farq_user_id = store.farq_user_for_request(request_id)
    if not farq_user_id:
        return False
    base = api_url()
    payload = reply_payload(farq_user_id, request_id, store.recipient_name(request_id, seller_id) or "", body)
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = http.post(
            f"{base}/api/push/notify",
            json=payload,
            headers={"Authorization": f"Bearer {os.environ['BILLING_S2S_SECRET'].strip()}", "Accept": "application/json"},
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a push must never break the reply it announces
        log.warning("farq push for %s failed: %s", request_id, exc)
        return False
    finally:
        if client is None:
            http.close()
    if response.status_code != 200:
        log.warning("farq push for %s answered %s", request_id, response.status_code)
        return False
    try:
        return int((response.json().get("data") or {}).get("delivered") or 0) > 0
    except ValueError:
        return False
