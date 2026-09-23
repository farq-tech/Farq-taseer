"""The supplier notification ladder.

A registered supplier stops reading Haraj, so Farq has to own his attention or the capacity
gained by moving him in-app is paid back in silence. Every event climbs the same ladder:

    1. in-app   always written; the app shows it and counts it unread
    2. push     Web Push to his devices, when he has allowed it
    3. email    only when push did not land, and only if a provider is configured
    4. sms/whatsapp  not built - see NOT_BUILT below

Which rungs actually carried it is recorded on the row, so the ladder can be measured
instead of assumed. Rung 1 is "realtime" in the sense the platform allows: the API runs on
serverless functions, so the app polls on a short interval rather than holding a socket.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlsplit

from farq import mailer
from farq.push import allowed_endpoint, public_key

log = logging.getLogger("farq.notify")

# Rung four. Left out deliberately: it costs money per message and needs a provider and a
# registered sender, and the three rungs above have to be shown to be failing first.
NOT_BUILT = ("sms", "whatsapp")

# The seven things a supplier needs to hear about, in the words he reads.
EVENTS = {
    "request_new": {
        "title": "طلب تسعير جديد",
        "body": "وصلك طلب جديد في مجالك. افتحه وقدّم سعرك قبل غيرك.",
        "urgent": True,
    },
    "question_new": {
        "title": "سؤال من العميل",
        "body": "العميل يسأل عن طلبك. رد عليه عشان يمشي الطلب.",
        "urgent": True,
    },
    "buyer_reply": {
        "title": "رد من العميل",
        "body": "العميل رد على عرضك.",
        "urgent": True,
    },
    "request_updated": {
        "title": "تعديل على الطلب",
        "body": "العميل عدّل تفاصيل الطلب. راجع عرضك إذا احتاج تحديث.",
        "urgent": False,
    },
    "awarded": {
        "title": "🎉 تم اختيار عرضك",
        "body": "العميل اختارك لتنفيذ الطلب. نسّق معه من المحادثة.",
        "urgent": True,
    },
    "request_cancelled": {
        "title": "أُلغي الطلب",
        "body": "العميل ألغى هذا الطلب. ما عاد يستقبل عروض.",
        "urgent": False,
    },
    "closing_soon": {
        "title": "الفرصة قاربت تنتهي",
        "body": "العميل قارب يختار. إذا ما قدّمت سعرك، قدّمه الآن.",
        "urgent": True,
    },
}


def public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://taseer.farq.sa").rstrip("/")


def _link(token: str | None) -> str:
    base = public_base_url()
    return f"{base}/s/{token}" if token else f"{base}/supplier"


def _push(store, supplier_id: str, payload: dict) -> bool:
    """Rung two. Returns whether at least one device took it."""
    private_key = os.environ.get("VAPID_PRIVATE_KEY")
    if not private_key or not public_key():
        return False
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return False
    data = json.dumps(payload, ensure_ascii=False)
    delivered = 0
    for sub in store.supplier_push_subscriptions(supplier_id):
        if not allowed_endpoint(sub["endpoint"]):
            continue
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=data,
                vapid_private_key=private_key,
                vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:tech@farq.sa")},
                ttl=86400,
                timeout=10,
            )
            delivered += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                store.remove_supplier_push_subscription(sub["endpoint"])
        except Exception:  # noqa: BLE001 - one dead device must not stop the others
            continue
    return delivered > 0


def notify_supplier(store, supplier: dict, event: str, *, request_id: str | None = None,
                    seller_id: str | None = None, token: str | None = None,
                    need: str | None = None, body: str | None = None) -> dict:
    """Climb the ladder once for one supplier. Always returns which rungs carried it."""
    template = EVENTS.get(event)
    if template is None or not supplier:
        return {}
    title = template["title"]
    text = body or template["body"]
    if need:
        text = f"{need} — {text}"
    url = _link(token)

    # Rung 1. This is the notification; the rest are ways of pointing at it.
    row = store.add_supplier_notification(
        supplier_id=supplier["id"], request_id=request_id, seller_id=seller_id,
        event=event, title=title, body=text, url=url,
    )
    if row is None:  # already sent for a once-only event
        return {}
    delivered = {"in_app": True, "push": False, "email": "skipped"}

    # Rung 2.
    delivered["push"] = _push(store, supplier["id"], {"title": title, "body": text, "url": url, "tag": row["id"]})

    # Rung 3, only when rung 2 did not land, and only for something worth an email.
    if not delivered["push"] and template["urgent"]:
        delivered["email"] = mailer.send(supplier.get("email") or "", title, text, url=url)

    store.set_notification_delivery(row["id"], delivered)
    return delivered


def notify_sellers(store, seller_ids, event: str, *, request_id: str | None = None,
                   need: str | None = None, body: str | None = None) -> int:
    """Notify whichever of these Haraj sellers have an account. The rest are reached the
    old way, through Haraj, by the worker."""
    reached = 0
    for supplier in store.suppliers_for_sellers(seller_ids):
        token = store.reply_token_for(request_id, supplier["haraj_seller_id"]) if request_id else None
        if notify_supplier(store, supplier, event, request_id=request_id,
                           seller_id=supplier["haraj_seller_id"], token=token, need=need, body=body):
            reached += 1
    return reached
