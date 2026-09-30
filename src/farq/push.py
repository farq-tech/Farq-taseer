"""Phone notifications (Web Push) to the customer when a supplier replies.

Keys come from the server settings VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY (and
VAPID_SUBJECT). Without them nothing is sent; the in-app unread badge still works.
"""

from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import urlsplit

# The browsers' own push services. Anything else is refused, so the server never posts to
# an address a client chose (blind SSRF).
PUSH_HOSTS = {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")


def allowed_endpoint(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password or len(endpoint) > 1024:
        return False
    return host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES)


def public_key() -> str | None:
    return os.environ.get("VAPID_PUBLIC_KEY") or None


def reply_notifications_enabled() -> bool:
    """The recorded reply notification (in-app row + delivery record). Off by default: it is
    not announced to customers until a real send has been tested end to end."""
    return os.environ.get("TASEER_REPLY_NOTIFICATIONS", "").strip().lower() in ("1", "true", "on", "yes")


def _web_push(store, request_id: str, seller_id: str | None, body: str) -> tuple[int, int]:
    """Web Push to the owner's browsers. Returns (devices tried, devices that took it)."""
    private_key = os.environ.get("VAPID_PRIVATE_KEY")
    if not private_key or not public_key():
        return 0, 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return 0, 0
    name = store.recipient_name(request_id, seller_id) or "جهة"
    payload = json.dumps(
        {"title": name, "body": (body or "")[:180], "url": f"/?r={request_id}", "tag": request_id},
        ensure_ascii=False,
    )
    tried = delivered = 0
    for sub in store.push_subscriptions_for_request(request_id):
        if not allowed_endpoint(sub["endpoint"]):
            continue
        tried += 1
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:tech@farq.sa")},
                ttl=86400,
                timeout=10,
            )
            delivered += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                store.remove_push_subscription(sub["endpoint"])
        except Exception:  # noqa: BLE001 - one device must not stop the others
            continue
    return tried, delivered


def notify_reply(store, request_id: str, seller_id: str | None, body: str, message_id: str | None = None) -> int:
    """Notify every device the request's owner turned notifications on for. Returns how many
    browsers took it; the Farq app's phone is reached separately, through the Farq API.

    With TASEER_REPLY_NOTIFICATIONS on, the reply is first written as an in-app notification
    row (state ``queued``), and the row then records what carried it:
      sent        at least one phone or browser accepted the push (acceptance, not "read")
      no_channel  nothing to push to (no linked Farq phone, no browser subscription, or no keys)
      failed      a channel existed and every attempt was refused
    A repeat of the same reply (same message) is not notified twice."""
    from farq import farq_push

    record = None
    if reply_notifications_enabled():
        owner = store.request_owner(request_id)
        if owner:
            text = " ".join((body or "").split())[:180] or "وصلك رد جديد على طلب التسعير"
            key = f"supplier_reply:{message_id}" if message_id else f"supplier_reply:{request_id}:{seller_id or ''}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
            record = store.enqueue_customer_notification(
                user_id=owner, event="supplier_reply", dedupe_key=key,
                title=store.recipient_name(request_id, seller_id) or "مورد", body=text,
                url=f"/taseer?r={request_id}", request_id=request_id, seller_id=seller_id,
            )
            if record is None:
                return 0
    farq_tried = farq_push.configured() and bool(store.farq_user_for_request(request_id))
    farq_took = farq_push.notify_farq_reply(store, request_id, seller_id, body)
    tried, delivered = _web_push(store, request_id, seller_id, body)
    if record is not None:
        if farq_took or delivered:
            state = "sent"
        elif not farq_tried and not tried:
            state = "no_channel"
        else:
            state = "failed"
        store.finish_customer_notification(
            record["id"], state, {"in_app": True, "farq_app": bool(farq_took), "web_push": delivered, "web_push_tried": tried}
        )
    return delivered
