"""Subscription checkout, verification and webhook handling.

Business logic only - talks to a `store` (SQLite Store or PgStore, same
method surface) and a `MoyasarClient`. Never trusts amounts/status coming
from the frontend: every activation is re-derived from a server-side
Moyasar Fetch Payment call, matched against the pending payment row this
same backend created at checkout time.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from farq.moyasar import MoyasarClient, MoyasarError, MoyasarNotConfigured


@dataclass
class CheckoutResult:
    payment_id: str
    plan: dict
    publishable_key: str
    amount: int
    currency: str
    callback_url: str
    metadata: dict


class SubscriptionError(ValueError):
    pass


def list_plans(store) -> list[dict]:
    return store.list_active_plans()


def get_status(store, user_id: str) -> dict:
    status = store.subscription_status(user_id)
    subscription = store.get_latest_subscription(user_id)
    return {"status": status, "subscription": subscription}


def start_checkout(store, *, user_id: str, plan_code: str, publishable_key: str | None, public_base_url: str) -> CheckoutResult:
    if not publishable_key:
        raise SubscriptionError("payments are not configured yet (MOYASAR_PUBLISHABLE_KEY missing)")
    plan = store.get_plan(plan_code)
    if plan is None or not plan["is_active"]:
        raise SubscriptionError("unknown or inactive plan")
    payment = store.create_pending_payment(user_id, plan_code, plan["price_amount"], plan["currency"])
    metadata = {"farq_payment_id": payment["id"], "farq_user_id": user_id, "farq_plan": plan_code}
    return CheckoutResult(
        payment_id=payment["id"],
        plan=plan,
        publishable_key=publishable_key,
        amount=plan["price_amount"],
        currency=plan["currency"],
        callback_url=f"{public_base_url.rstrip('/')}/subscribe/callback",
        metadata=metadata,
    )


def _settle_from_moyasar(store, moyasar: MoyasarClient, *, farq_payment_id: str, moyasar_payment_id: str) -> dict:
    try:
        fetched = moyasar.fetch_payment(moyasar_payment_id)
    except MoyasarNotConfigured as exc:
        raise SubscriptionError(str(exc)) from exc
    except MoyasarError as exc:
        return {"ok": False, "reason": "moyasar_unreachable", "detail": str(exc)}

    if fetched.metadata.get("farq_payment_id") != farq_payment_id:
        return {"ok": False, "reason": "payment_id_mismatch"}

    # A refund can arrive either before a pending payment was ever settled,
    # or after it already activated a subscription - handle both here so a
    # refunded charge never grants (or keeps) entitlement.
    if fetched.refunded:
        return store.refund_payment(farq_payment_id, fetched.id)

    return store.settle_payment(
        payment_id=farq_payment_id,
        provider_payment_id=fetched.id,
        provider_status=fetched.status,
        paid_amount=fetched.amount,
        paid_currency=fetched.currency,
    )


def verify_checkout(store, moyasar: MoyasarClient, *, user_id: str, payment_id: str, moyasar_payment_id: str) -> dict:
    """Called by the frontend right after Moyasar.js reports completion.
    Redirect/callback alone is never treated as success - this always
    re-verifies with Moyasar before the caller can see an active state.
    """
    payment = store.get_payment(payment_id)
    if payment is None or payment["user_id"] != user_id:
        raise SubscriptionError("payment not found for this user")
    result = _settle_from_moyasar(store, moyasar, farq_payment_id=payment_id, moyasar_payment_id=moyasar_payment_id)
    return result


def handle_webhook(store, moyasar: MoyasarClient, *, event_id: str, event_type: str, moyasar_payment_id: str, farq_payment_id: str | None) -> dict:
    """Idempotent: settle_payment()/refund_payment() refuse to double-apply a
    payment that already left 'payment_pending', so replaying the same event
    is always safe.

    The event is only recorded as seen *after* a successful settlement
    attempt. Recording it first would mean a transient Moyasar outage marks
    the event "processed" while the payment is still pending - Moyasar's own
    retry of that exact event id would then be silently dropped as a
    duplicate, leaving a customer who actually paid stuck pending forever.
    """
    if farq_payment_id is None:
        # Payment created outside our checkout flow (shouldn't happen in
        # production, but don't crash the webhook endpoint over it).
        payment = store.get_payment_by_provider_id(moyasar_payment_id)
        farq_payment_id = payment["id"] if payment else None
    if farq_payment_id is None:
        return {"ok": False, "reason": "unknown_payment", "duplicate_event": False}

    result = _settle_from_moyasar(store, moyasar, farq_payment_id=farq_payment_id, moyasar_payment_id=moyasar_payment_id)
    if not result.get("ok"):
        result["duplicate_event"] = False
        return result

    is_new = store.record_webhook_event(event_id, event_type, moyasar_payment_id)
    result["duplicate_event"] = not is_new
    return result


def new_idempotency_id() -> str:
    return str(uuid4())
