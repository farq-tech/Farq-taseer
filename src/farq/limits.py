"""Server-side limits on what a customer can make Taseer's Haraj account send.

Every quote request and message leaves from one Haraj account. A refusal from Haraj
(401/403/429) stops sending for every customer for 30 minutes and risks the account, so the
server, not the app, decides who may be written to and how often:

- recipients must be Haraj sellers a search showed this customer (search_sellers);
- sellers per item: 6 on the free trial, a hard ceiling for subscribers;
- the free trial covers 50 items (requests);
- a daily ceiling on requests and on messages per account.

Numbers are defaults, read from the environment like SearchConfig.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from farq.config import _env_bool, _env_int
from farq.store import seller_key


@dataclass(frozen=True)
class Limits:
    trial_items: int = field(default_factory=lambda: _env_int("FARQ_TRIAL_ITEMS", 50))
    trial_sellers_per_item: int = field(default_factory=lambda: _env_int("FARQ_TRIAL_SELLERS_PER_ITEM", 6))
    max_sellers_per_item: int = field(default_factory=lambda: _env_int("FARQ_MAX_SELLERS_PER_ITEM", 20))
    daily_requests: int = field(default_factory=lambda: _env_int("FARQ_DAILY_REQUEST_LIMIT", 30))
    daily_messages: int = field(default_factory=lambda: _env_int("FARQ_DAILY_MESSAGE_LIMIT", 200))
    recipients_from_search: bool = field(default_factory=lambda: _env_bool("FARQ_RECIPIENTS_FROM_SEARCH", True))
    search_memory_days: int = field(default_factory=lambda: _env_int("FARQ_SEARCH_MEMORY_DAYS", 7))


class LimitExceeded(Exception):
    """Refused before anything was queued. ``detail`` is what the app shows."""

    def __init__(self, status: int, code: str, message: str, limit: int | None = None):
        super().__init__(code)
        self.status = status
        self.detail = {"code": code, "message": message, "limit": limit}


def _since(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def check_new_request(store, limits: Limits, user_id: str, recipients, default_need: str | None, trace_id: str | None = None) -> None:
    """recipients: objects with seller_id and need. Raises LimitExceeded."""
    subscribed = store.is_subscribed(user_id)
    if not subscribed and store.count_requests(user_id) >= limits.trial_items:
        raise LimitExceeded(402, "TRIAL_ITEMS_EXHAUSTED", f"انتهت التجربة المجانية ({limits.trial_items} بند). اشترك لإرسال طلبات جديدة.", limits.trial_items)
    per_item: dict[str, set[str]] = {}
    for item in recipients:
        per_item.setdefault(item.need or default_need or "", set()).add(seller_key(item.seller_id))
    cap = limits.max_sellers_per_item if subscribed else limits.trial_sellers_per_item
    if any(len(sellers) > cap for sellers in per_item.values()):
        message = f"يمكن إرسال الطلب إلى {cap} موردين كحد أقصى لكل بند" + ("." if subscribed else " في التجربة المجانية.")
        raise LimitExceeded(403, "TOO_MANY_SELLERS", message, cap)
    if store.count_requests(user_id, since=_since(days=1)) >= limits.daily_requests:
        raise LimitExceeded(429, "DAILY_REQUEST_LIMIT", f"وصلت للحد اليومي ({limits.daily_requests} طلب). حاول مرة ثانية بكرة.", limits.daily_requests)
    if limits.recipients_from_search:
        shown = store.searched_sellers(user_id, trace_id, _since(days=limits.search_memory_days))
        if any(seller_key(item.seller_id) not in shown for item in recipients):
            raise LimitExceeded(403, "UNKNOWN_RECIPIENT", "اختر الموردين من نتائج البحث ثم أرسل الطلب.")


def check_new_message(store, limits: Limits, user_id: str) -> None:
    if store.count_customer_messages(user_id, _since(days=1)) >= limits.daily_messages:
        raise LimitExceeded(429, "DAILY_MESSAGE_LIMIT", f"وصلت للحد اليومي للرسائل ({limits.daily_messages} رسالة). حاول مرة ثانية بكرة.", limits.daily_messages)
