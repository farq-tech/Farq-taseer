"""Server-side limits on what a customer can make Taseer's Haraj account send.

Every quote request and message leaves from one Haraj account. A refusal from Haraj
(401/403/429) stops sending for every customer for 30 minutes and risks the account, so the
server, not the app, decides who may be written to and how often:

- recipients must be Haraj sellers a search showed this customer (search_sellers);
- an allowance is spent in **items**, not requests. One request carries a distinct need per
  item, so counting requests would let ten items inside one request cost one;
- how many items, how many sellers per item, and how many supplier contacts a day come from
  the subscriber's own plan row (subscription_plans.monthly_items / sellers_per_item /
  daily_contacts). The free trial's numbers are the environment defaults below;
- daily_contacts exists because send capacity is shared and fixed: the 20-second spacing in
  worker.py is platform-wide, so the whole product sends three supplier contacts a minute.
  One customer must not be able to spend a day of it.

The monthly allowance resets against subscriptions.period_anchor, not starts_at: a renewal
updates the existing row and leaves starts_at at the original activation, so an allowance
measured from starts_at would never reset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from farq import mailer
from farq.config import _env_bool, _env_int
from farq.store import seller_key


@dataclass(frozen=True)
class Limits:
    """The free trial's allowance, plus ceilings no plan may exceed."""

    trial_items: int = field(default_factory=lambda: _env_int("FARQ_TRIAL_ITEMS", 10))
    trial_sellers_per_item: int = field(default_factory=lambda: _env_int("FARQ_TRIAL_SELLERS_PER_ITEM", 6))
    trial_daily_contacts: int = field(default_factory=lambda: _env_int("FARQ_TRIAL_DAILY_CONTACTS", 30))
    max_sellers_per_item: int = field(default_factory=lambda: _env_int("FARQ_MAX_SELLERS_PER_ITEM", 20))
    daily_requests: int = field(default_factory=lambda: _env_int("FARQ_DAILY_REQUEST_LIMIT", 30))
    daily_messages: int = field(default_factory=lambda: _env_int("FARQ_DAILY_MESSAGE_LIMIT", 200))
    recipients_from_search: bool = field(default_factory=lambda: _env_bool("FARQ_RECIPIENTS_FROM_SEARCH", True))
    search_memory_days: int = field(default_factory=lambda: _env_int("FARQ_SEARCH_MEMORY_DAYS", 7))
    # "auto" means: require it exactly when a verification email can be sent. Demanding a
    # confirmation nobody can receive would lock out every customer, so the gate follows
    # the mail provider rather than standing open or shut on its own. "on"/"off" override.
    require_email_verification: str = field(default_factory=lambda: (_env_str("FARQ_REQUIRE_EMAIL_VERIFICATION", "auto")).strip().lower())
    # The credit ledger (default off). On, the item allowance is a spendable balance in
    # Farq's CENTRAL billing ledger (see farq.billing) instead of a recount of this
    # period's rows: Taseer holds no balance of its own, every debit is an S2S consume
    # keyed by the customer's Farq user id, and what is granted survives the period. The
    # other caps (sellers per item, daily contacts, daily requests, recipients-from-search)
    # do not move to the ledger and keep working exactly as before.
    ledger_enabled: bool = field(default_factory=lambda: _env_bool("TASEER_LEDGER_ENABLED", False))

    def verification_required(self) -> bool:
        if self.require_email_verification == "on":
            return True
        if self.require_email_verification == "off":
            return False
        return mailer.configured()


class LimitExceeded(Exception):
    """Refused before anything was queued. ``detail`` is what the app shows."""

    def __init__(self, status: int, code: str, message: str, limit: int | None = None):
        super().__init__(code)
        self.status = status
        self.detail = {"code": code, "message": message, "limit": limit}


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else raw


def _since(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def current_period_start(anchor: str | None, duration_days: int, now: datetime | None = None) -> str | None:
    """The start of the allowance window the subscriber is in right now.

    Whole ``duration_days`` steps from the anchor, so stacked renewals (which push
    expires_at forward without moving the anchor) keep one steady monthly rhythm.
    """
    start = _parse(anchor)
    if start is None or duration_days <= 0:
        return None
    moment = now or datetime.now(timezone.utc)
    if moment <= start:
        return start.isoformat()
    step = timedelta(days=duration_days)
    return (start + (moment - start) // step * step).isoformat()


@dataclass(frozen=True)
class Entitlement:
    """What this customer may send right now, and how much of it is already spent."""

    plan_code: str | None  # None on the free trial
    plan_name: str
    items: int
    sellers_per_item: int
    daily_contacts: int
    period_start: str | None  # None means the allowance is for the lifetime of the account
    items_used: int
    contacts_today: int
    # The spendable ledger balance, or None while TASEER_LEDGER_ENABLED is off.
    credits: int | None = None

    @property
    def subscribed(self) -> bool:
        return self.plan_code is not None

    @property
    def items_left(self) -> int:
        # With the ledger on, what may still be sent IS the balance.
        if self.credits is not None:
            return max(0, self.credits)
        return max(0, self.items - self.items_used)

    @property
    def contacts_left_today(self) -> int:
        return max(0, self.daily_contacts - self.contacts_today)

    def as_dict(self) -> dict:
        payload = {
            "plan": self.plan_code,
            "plan_name": self.plan_name,
            "subscribed": self.subscribed,
            "items": self.items,
            "items_used": self.items_used,
            "items_left": self.items_left,
            "sellers_per_item": self.sellers_per_item,
            "daily_contacts": self.daily_contacts,
            "contacts_today": self.contacts_today,
            "contacts_left_today": self.contacts_left_today,
            "period_start": self.period_start,
        }
        if self.credits is not None:
            payload["credits"] = self.credits
        return payload


def entitlement(store, limits: Limits, user_id: str, billing=None) -> Entitlement:
    """Read the customer's allowance from their plan, or fall back to the free trial.

    A plan row with no quotas set (the retired sandbox placeholder, or a plan added by hand)
    falls back to the trial numbers rather than to no limit: an unknown allowance must not
    become an unlimited one.
    """
    # An open account: the owner's own, and any account he opens later. It is neither on the
    # trial nor a paying subscriber, so it is neither given a plan it never bought nor a
    # subscription row that would show it a renewal date and a payment history that do not
    # exist. sellers_per_item still honours the platform ceiling, because that one protects
    # the shared Haraj account rather than the customer's wallet.
    if getattr(store, "account_unlimited", None) and store.account_unlimited(user_id):
        return Entitlement(
            plan_code=OPEN_ACCOUNT,
            plan_name="حساب مفتوح",
            items=OPEN_ALLOWANCE,
            sellers_per_item=limits.max_sellers_per_item,
            daily_contacts=OPEN_ALLOWANCE,
            period_start=None,
            items_used=store.count_items(user_id, _trial_since(store, user_id)),
            contacts_today=store.count_contacts(user_id, _since(days=1)),
        )

    # With the ledger on, the number shown is the CENTRAL balance (Farq's
    # billing.credit_ledger, keyed by the Farq user id) - display only, briefly cached,
    # never the debit decision; that decision is the consume call itself in api.py.
    # None while the flag is off, the account is unlinked, or central cannot be read.
    credits = None
    if limits.ledger_enabled and billing is not None and billing.configured():
        farq_uid = store.farq_user_id(user_id) if getattr(store, "farq_user_id", None) else None
        if farq_uid:
            credits = billing.balance(farq_uid)

    subscription = store.get_latest_subscription(user_id) if store.is_subscribed(user_id) else None
    if subscription is None:
        return Entitlement(
            plan_code=None,
            plan_name="التجربة المجانية",
            items=limits.trial_items,
            sellers_per_item=limits.trial_sellers_per_item,
            daily_contacts=limits.trial_daily_contacts,
            period_start=None,
            # Since the account's own reset mark, not since it was created, so returning a
            # customer's allowance never means deleting what he did with it.
            items_used=store.count_items(user_id, _trial_since(store, user_id)),
            contacts_today=store.count_contacts(user_id, _since(days=1)),
            credits=credits,
        )

    plan = store.get_plan(subscription["plan"]) or {}
    duration = int(plan.get("duration_days") or 30)
    period_start = current_period_start(subscription.get("period_anchor"), duration)
    sellers = int(plan.get("sellers_per_item") or limits.trial_sellers_per_item)
    return Entitlement(
        plan_code=subscription["plan"],
        plan_name=plan.get("name_ar") or subscription["plan"],
        items=int(plan.get("monthly_items") or limits.trial_items),
        sellers_per_item=min(sellers, limits.max_sellers_per_item),
        daily_contacts=int(plan.get("daily_contacts") or limits.trial_daily_contacts),
        period_start=period_start,
        items_used=store.count_items(user_id, period_start),
        contacts_today=store.count_contacts(user_id, _since(days=1)),
        credits=credits,
    )


OPEN_ACCOUNT = "open"
# Large enough never to be reached, small enough to read as a number on a screen.
OPEN_ALLOWANCE = 100_000


def _trial_since(store, user_id: str) -> str | None:
    reader = getattr(store, "trial_since", None)
    return reader(user_id) if reader else None


def _items_in(recipients, default_need: str | None) -> dict[str, set[str]]:
    per_item: dict[str, set[str]] = {}
    for item in recipients:
        per_item.setdefault(item.need or default_need or "", set()).add(seller_key(item.seller_id))
    return per_item


def check_new_request(store, limits: Limits, user_id: str, recipients, default_need: str | None, trace_id: str | None = None) -> None:
    """recipients: objects with seller_id and need. Raises LimitExceeded."""
    from farq.haraj_user_connection import enabled as user_connection_enabled
    # The user-account API verifies Haraj connection before this check. Preserve
    # the legacy verification rule only for the other existing flow.
    if not user_connection_enabled() and limits.verification_required() and not store.email_verified(user_id):
        raise LimitExceeded(
            403, "EMAIL_NOT_VERIFIED",
            "أكّد بريدك الإلكتروني قبل إرسال أول طلب. أرسلنا لك رابط التأكيد.",
        )
    per_user_haraj = user_connection_enabled()
    per_item = _items_in(recipients, default_need)
    allowance = entitlement(store, limits, user_id)

    # With the ledger on, the item allowance is NOT decided here: only the consume call
    # against Farq's central billing ledger (in api.py's create_request) grants an item.
    # A locally read balance is display-only and stale by design, so refusing on it would
    # make a cached number the authority. Every other cap below still holds.
    if not limits.ledger_enabled and len(per_item) > allowance.items_left:
        if allowance.subscribed:
            message = (
                f"وصلت لحد باقتك ({allowance.items} بند في الشهر)."
                f" استخدمت {allowance.items_used}. رقِّ باقتك أو انتظر تجديد الفترة."
            )
        else:
            message = f"انتهت التجربة المجانية ({allowance.items} بنود). اشترك لإرسال طلبات جديدة."
        raise LimitExceeded(402, "ITEM_ALLOWANCE_EXHAUSTED", message, allowance.items)

    if not per_user_haraj and any(len(sellers) > allowance.sellers_per_item for sellers in per_item.values()):
        suffix = "." if allowance.subscribed else " في التجربة المجانية."
        message = f"يمكن إرسال الطلب إلى {allowance.sellers_per_item} موردين كحد أقصى لكل بند" + suffix
        raise LimitExceeded(403, "TOO_MANY_SELLERS", message, allowance.sellers_per_item)

    if not per_user_haraj and len(recipients) > allowance.contacts_left_today:
        message = (
            f"وصلت للحد اليومي ({allowance.daily_contacts} مورد في اليوم)."
            " الإرسال مجدول بالتساوي على كل العملاء، فحاول مرة ثانية بكرة."
        )
        raise LimitExceeded(429, "DAILY_CONTACT_LIMIT", message, allowance.daily_contacts)

    if not per_user_haraj and store.count_requests(user_id, since=_since(days=1)) >= limits.daily_requests:
        raise LimitExceeded(429, "DAILY_REQUEST_LIMIT", f"وصلت للحد اليومي ({limits.daily_requests} طلب). حاول مرة ثانية بكرة.", limits.daily_requests)

    if limits.recipients_from_search:
        shown = store.searched_sellers(user_id, trace_id, _since(days=limits.search_memory_days))
        if any(seller_key(item.seller_id) not in shown for item in recipients):
            raise LimitExceeded(403, "UNKNOWN_RECIPIENT", "اختر الموردين من نتائج البحث ثم أرسل الطلب.")


def check_new_message(store, limits: Limits, user_id: str) -> None:
    from farq.haraj_user_connection import enabled as user_connection_enabled
    if user_connection_enabled():
        return
    if store.count_customer_messages(user_id, _since(days=1)) >= limits.daily_messages:
        raise LimitExceeded(429, "DAILY_MESSAGE_LIMIT", f"وصلت للحد اليومي للرسائل ({limits.daily_messages} رسالة). حاول مرة ثانية بكرة.", limits.daily_messages)
