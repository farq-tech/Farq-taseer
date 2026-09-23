"""Phone notifications (Web Push) to the customer when a supplier replies.

Keys come from the server settings VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY (and
VAPID_SUBJECT). Without them nothing is sent; the in-app unread badge still works.
"""

from __future__ import annotations

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


def notify_reply(store, request_id: str, seller_id: str | None, body: str) -> int:
    """Notify every device the request's owner turned notifications on for. Returns how many took it."""
    private_key = os.environ.get("VAPID_PRIVATE_KEY")
    if not private_key or not public_key():
        return 0
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return 0
    name = store.recipient_name(request_id, seller_id) or "جهة"
    payload = json.dumps(
        {"title": name, "body": (body or "")[:180], "url": f"/?r={request_id}", "tag": request_id},
        ensure_ascii=False,
    )
    delivered = 0
    for sub in store.push_subscriptions_for_request(request_id):
        if not allowed_endpoint(sub["endpoint"]):
            continue
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
    return delivered
