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


class PaymentsUnavailable(SubscriptionError):
    """Payments cannot be taken right now (keys missing, or a placeholder price on a live
    key). The message is for the server log; customers get a generic answer."""


def is_live_key(*keys: str | None) -> bool:
    return any(key and (key.startswith("sk_live") or key.startswith("pk_live")) for key in keys)


def purchasable(plan: dict, *, live: bool) -> bool:
    # A placeholder price exists to exercise the sandbox; it must never take real money.
    return bool(plan.get("is_active")) and not (live and plan.get("is_placeholder_price"))


def list_plans(store, *, live: bool = False) -> list[dict]:
    return [{**plan, "purchasable": purchasable(plan, live=live)} for plan in store.list_active_plans()]


def get_status(store, user_id: str) -> dict:
    status = store.subscription_status(user_id)
    subscription = store.get_latest_subscription(user_id)
    return {"status": status, "subscription": subscription}


# Where a 3-D Secure payment may come back to besides Taseer itself: Farq's own plans page,
# which now hosts Taseer's customer side. Exact URLs only - an open redirect after a payment
# is a phishing page's dream.
FARQ_CALLBACKS = frozenset({
    "https://www.farq.sa/taseer/plans/callback",
    "https://farq.sa/taseer/plans/callback",
})


def callback_for(public_base_url: str, return_to: str | None) -> str:
    """The page Moyasar sends a redirected (3-D Secure) payment back to."""
    if return_to is None or return_to == "":
        return f"{public_base_url.rstrip('/')}/subscribe/callback"
    if return_to not in FARQ_CALLBACKS:
        raise SubscriptionError("return_to is not an allowed callback")
    return return_to


def start_checkout(store, *, user_id: str, plan_code: str, publishable_key: str | None, public_base_url: str, live: bool = False, return_to: str | None = None) -> CheckoutResult:
    if not publishable_key:
        raise PaymentsUnavailable("payments are not configured yet (MOYASAR_PUBLISHABLE_KEY missing)")
    callback_url = callback_for(public_base_url, return_to)
    plan = store.get_plan(plan_code)
    if plan is None or not plan["is_active"]:
        raise SubscriptionError("unknown or inactive plan")
    if not purchasable(plan, live=live):
        raise PaymentsUnavailable(f"plan {plan_code} has a placeholder price and the Moyasar key is live")
    payment = store.create_pending_payment(user_id, plan_code, plan["price_amount"], plan["currency"])
    metadata = {"farq_payment_id": payment["id"], "farq_user_id": user_id, "farq_plan": plan_code}
    return CheckoutResult(
        payment_id=payment["id"],
        plan=plan,
        publishable_key=publishable_key,
        amount=plan["price_amount"],
        currency=plan["currency"],
        callback_url=callback_url,
        metadata=metadata,
    )


def _settle_from_moyasar(store, moyasar: MoyasarClient, *, farq_payment_id: str, moyasar_payment_id: str) -> dict:
    try:
        fetched = moyasar.fetch_payment(moyasar_payment_id)
    except MoyasarNotConfigured as exc:
        raise PaymentsUnavailable(str(exc)) from exc
    except MoyasarError as exc:
        return {"ok": False, "reason": "moyasar_unreachable", "detail": str(exc)}

    if fetched.metadata.get("farq_payment_id") != farq_payment_id:
        return {"ok": False, "reason": "payment_id_mismatch"}

    # A refund can arrive either before a pending payment was ever settled,
    # or after it already activated a subscription - handle both here so a
    # refunded charge never grants entitlement, and a refunded term is taken back.
    if fetched.refunded:
        return store.refund_payment(farq_payment_id, fetched.id, fetched.refunded_amount)

    return store.settle_payment(
        payment_id=farq_payment_id,
        provider_payment_id=fetched.id,
        provider_status=fetched.status,
        paid_amount=fetched.amount,
        paid_currency=fetched.currency,
    )


def verify_checkout(store, moyasar: MoyasarClient, *, user_id: str, payment_id: str, moyasar_payment_id: str) -> dict:
    """Called by the frontend after Moyasar.js reports completion and from the
    /subscribe/callback page. Redirect/callback alone is never treated as success -
    this always re-verifies with Moyasar before the caller can see an active state.
    A payment still in 3-D Secure ("initiated") comes back pending and stays settleable.
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
    if not result.get("ok") or result.get("pending"):
        # Not settled yet (Moyasar unreachable, or the payment is still in 3-D Secure):
        # leave the event unrecorded so a redelivery of it is processed again.
        result["duplicate_event"] = False
        return result

    is_new = store.record_webhook_event(event_id, event_type, moyasar_payment_id)
    result["duplicate_event"] = not is_new
    return result


def new_idempotency_id() -> str:
    return str(uuid4())
