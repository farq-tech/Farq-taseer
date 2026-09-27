"""Consumer persistence. Separate from FARQ Construction procurement tables."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from farq.cities import known_city
from farq.contracts import Attachment, Message, Offer, RequestRecipient, RequestRecord
from farq.haraj_chat import InboundMessage, SentMessage, author_id, extract_quote, find_references, new_reference

ALL_SELLERS = "all_sellers"
SINGLE_SELLER = "single_seller"
SOME_SELLERS = "some_sellers"
MEDIA_TYPES = {"image/jpeg", "application/pdf"}
MAX_FILE_BYTES = 4 * 1024 * 1024

# (code, name_ar, name_en, description_ar, price_halalas, features, monthly_items,
#  sellers_per_item, daily_contacts). Kept identical to the Postgres migration
# 20260923160000_taseer_plan_quotas.sql. daily_contacts guards the shared Haraj send
# capacity: 20s spacing platform-wide is 3 supplier contacts a minute, for everyone.
PLAN_CATALOG = (
    (
        "starter", "بداية", "Starter",
        "للاستخدام الشخصي والطلبات المتفرقة.",
        6900,
        ["100 بند شهرياً", "حتى 6 موردين لكل بند", "كل العروض في مكان واحد", "إشعار فوري عند وصول رد"],
        100, 6, 100,
    ),
    (
        "project", "مشروع", "Project",
        "لمن يسعّر باستمرار: ضعف ونصف الكمية، وأولوية في الإرسال.",
        16900,
        ["250 بند شهرياً", "حتى 6 موردين لكل بند", "أولوية في إرسال الطلبات", "كل مزايا بداية"],
        250, 6, 200,
    ),
    (
        "large", "مشروع كبير", "Large project",
        "للاستخدام الكثيف: أكبر كمية، وأكثر موردين لكل بند.",
        37900,
        ["600 بند شهرياً", "حتى 8 موردين لكل بند", "أولوية قصوى في الإرسال", "كل مزايا مشروع"],
        600, 8, 400,
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return digest.hex()


# A sign-in lasts this long; after that the customer signs in again.
SESSION_DAYS = 30
# Hashed when an email has no account, so a miss costs the same time as a wrong password.
_DUMMY_SALT = "0" * 32
# Moyasar states a payment still moves on from (3-D Secure, authorisation). A payment row
# stays settleable while its provider status is one of these; paid activates and the
# closed states end the attempt.
PROVIDER_CLOSED = {"failed", "voided", "expired", "canceled", "cancelled"}


def token_digest(token: str) -> str:
    """Sessions store sha256(token), never the bearer token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def session_keys(token: str) -> tuple[str, ...]:
    """The stored keys a presented token may match: its digest and, for sessions issued
    before tokens were hashed, the raw token. A 64-hex value is never tried raw, so a
    leaked digest cannot be replayed as a token."""
    digest = token_digest(token)
    if len(token) == 64 and all(char in "0123456789abcdef" for char in token):
        return (digest,)
    return (digest, token)


def session_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=SESSION_DAYS)


def password_matches(password: str, row) -> bool:
    """Always runs the hash, then compares in constant time."""
    if row is None:
        _hash_password(password, _DUMMY_SALT)
        return False
    return hmac.compare_digest(_hash_password(password, row["salt"]), row["password_hash"])


def settle_decision(payment: dict, provider_payment_id: str, provider_status: str, paid_amount: int, paid_currency: str) -> str:
    """What a verified Moyasar state does to our payment row:
    "done"     - the row is final (paid or refunded); nothing changes.
    "same"     - the same attempt was already closed as failed; nothing changes.
    "mismatch" - Moyasar charged a different amount; close the attempt as failed.
    "closed"   - the attempt failed/was voided; close it (a later paid retry still settles).
    "pending"  - 3-D Secure / authorisation still running; keep the row settleable.
    "paid"     - activate.
    """
    if payment["status"] in ("paid", "refunded", "partially_refunded"):
        return "done"
    if payment["status"] != "payment_pending" and payment["provider_payment_id"] == provider_payment_id and provider_status != "paid":
        return "same"
    if provider_status == "paid":
        if payment["amount"] != paid_amount or payment["currency"] != paid_currency:
            return "mismatch"
        return "paid"
    if provider_status in PROVIDER_CLOSED:
        return "closed"
    return "pending"


QUOTE_LINK = "{quote_link}"


def invite_text(item: str, city: str | None) -> str:
    """Farq's fixed invite: the item only, no quantities, prices, notes or buyer number.
    The link is each seller's own quote page, filled in when the message is sent."""
    line = f"{item.strip()} في {city}" if city else item.strip()
    return "\n".join(
        [
            "السلام عليكم عزيزي البائع",
            "لدينا مشتري يطلب توفير:",
            line,
            "في حال توفرها الرجاء الضغط على الرابط التالي لتقديم عرضك",
            QUOTE_LINK,
        ]
    )


def _delivery_state(deliveries: list[dict]) -> str:
    statuses = {item["status"] for item in deliveries}
    if statuses == {"sent"}:
        return "sent"
    if statuses & {"queued", "sending"}:
        return "queued"
    return "partial" if "sent" in statuses else "failed"


class AwardConflict(Exception):
    """The request is already awarded to another supplier."""


def visible_to_seller(message: Message, seller_id: str | None, need: str | None) -> bool:
    """A supplier sees his own messages and the customer messages routed to him, nothing else."""
    if message.sender_role == "seller":
        return seller_id is not None and message.seller_id == seller_id
    if message.deliveries:
        # The delivery rows are who the message went to; a message to some suppliers stays with them.
        return seller_id is not None and any(item["seller_id"] == seller_id for item in message.deliveries)
    if message.seller_id is not None:
        return message.seller_id == seller_id
    if message.scope == SOME_SELLERS:
        return False
    return message.need is None or need is None or message.need == need


def seller_message(message: Message, seller_id: str | None, sent_text: str | None = None) -> dict:
    """What a supplier may see of a message: no routing, no other suppliers, only his own offer.
    sent_text is what actually went to him on Haraj (the invite, not the customer's notes)."""
    own = message.sender_role == "seller" and seller_id is not None and message.seller_id == seller_id
    return {
        "id": message.id,
        "sender_role": message.sender_role,
        "body": message.body if sent_text is None else sent_text.replace(QUOTE_LINK, "").strip(),
        "created_at": message.created_at,
        "media": message.media,
        "offer": message.offer.model_dump(mode="json") if own and message.offer is not None else None,
    }


def _stamp(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def route_targets(recipients, default_need: str | None, need: str | None, seller_id: str | None, seller_ids=None):
    """Who a customer message goes to: the suppliers picked, one supplier, or every supplier on the item.
    Returns (targets, need, scope)."""
    item_of = lambda row: row["need"] or default_need or ""
    if seller_ids:
        picked = list(dict.fromkeys(str(item) for item in seller_ids))
        targets = []
        for picked_id in picked:
            match = [row for row in recipients if row["seller_id"] == picked_id and (need is None or item_of(row) == need)]
            if not match:
                raise ValueError("unknown seller")
            targets.append(match[0])
        item = item_of(targets[0])
        if any(item_of(row) != item for row in targets):
            raise ValueError("pick suppliers from one item")
        on_item = {row["seller_id"] for row in recipients if item_of(row) == item}
        if len(targets) == 1:
            return targets, item or None, SINGLE_SELLER
        return targets, item or None, ALL_SELLERS if {row["seller_id"] for row in targets} == on_item else SOME_SELLERS
    if seller_id is not None:
        targets = [row for row in recipients if row["seller_id"] == seller_id and (need is None or item_of(row) == need)]
        if not targets:
            raise ValueError("unknown seller")
        return targets[:1], item_of(targets[0]) or None, SINGLE_SELLER
    if need is None and len({item_of(row) for row in recipients}) > 1:
        raise ValueError("need is required for a request with several items")
    targets = [row for row in recipients if need is None or item_of(row) == need]
    if not targets:
        raise ValueError("unknown item")
    return targets, need, ALL_SELLERS


def reply_audience(quoted_seller_id: str | None, delivered: list[str], seller_id: str | None, seller_ids=None):
    """A reply goes to whoever got the quoted message, narrowed by any suppliers picked; never wider.
    Returns (seller_id, seller_ids) for route_targets."""
    audience = [quoted_seller_id] if quoted_seller_id else list(dict.fromkeys(delivered))
    if not audience:
        return seller_id, seller_ids
    picked = [str(item) for item in seller_ids] if seller_ids else ([seller_id] if seller_id is not None else None)
    if picked is not None:
        audience = [item for item in audience if item in picked]
        if not audience:
            raise ValueError("pick suppliers who got the quoted message")
    return None, audience


def media_entry(file_row) -> dict:
    """What the conversation shows for a customer's file; the bytes stay in the files table."""
    return {
        "type": file_row["content_type"],
        "file_id": file_row["id"],
        "url": f"/v1/files/{file_row['id']}",
        "name": file_row["filename"],
        "size": file_row["size_bytes"],
        "width": file_row["width"],
        "height": file_row["height"],
    }


def current_offer_rows(rows) -> list:
    """One current offer per supplier per item: the latest row wins, older ones are history.
    Rows come newest first; the result is cheapest first."""
    seen: set[tuple[str, str]] = set()
    current = []
    for row in rows:
        key = (row["seller_id"], row["need"] or "")
        if key not in seen:
            seen.add(key)
            current.append(row)
    return sorted(current, key=lambda row: (row["total_price"] is None, float(row["total_price"] or 0)))


def mark_cheapest(offers: list[Offer]) -> list[Offer]:
    """One cheapest offer per item, and only when it is strictly the lowest."""
    grouped: dict[str, list[Offer]] = {}
    for offer in offers:
        grouped.setdefault(offer.need or "", []).append(offer)
    for group in grouped.values():
        priced = [item for item in group if item.total_price is not None]
        if priced:
            lowest = min(item.total_price for item in priced)
            winners = [item for item in priced if item.total_price == lowest]
            if len(winners) == 1:
                winners[0].cheapest = True
    return offers


def priced_offer(offer: Offer, body: str, seller_name: str, need: str | None) -> Offer:
    base = offer.base_price if offer.base_price is not None else offer.amount
    included = True if offer.delivery_included is None else offer.delivery_included
    delivery = 0.0 if included else (offer.delivery_price or 0.0)
    total = None if base is None else float(base) + float(delivery)
    return Offer(
        amount=total,
        currency=offer.currency or "SAR",
        note=offer.note or (body.strip() or None),
        provider_name=offer.provider_name or seller_name,
        phone=offer.phone,
        base_price=base,
        delivery_included=included,
        delivery_price=delivery,
        total_price=total,
        need=offer.need or need,
    )


def _moment(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc(value):
    moment = _moment(value)
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def choose_thread(candidates: list[dict], body: str, sent_at: str) -> dict | None:
    """The thread a seller's Haraj reply belongs to, or None when that cannot be known.

    Taseer writes from one Haraj account, so a seller has one conversation with us shared by
    every buyer who asked him. A reply quoting a request's reference goes to that request.
    Otherwise it goes to the latest request we had sent him something for before he wrote
    (Farq's rule), but only while the open requests in that conversation belong to one buyer:
    across buyers a guess would show one buyer the reply, phone number and price meant for another.
    Each candidate is a haraj_threads row plus owner_user_id, ref_code, awarded_seller_id,
    request_created_at, and sends (when our messages on that thread were sent)."""
    if not candidates:
        return None
    moment = _utc(sent_at)
    oldest = datetime.min.replace(tzinfo=timezone.utc)

    def last_send(row):
        sends = [item for item in (_utc(value) for value in row["sends"]) if item is not None and (moment is None or item <= moment)]
        return max(sends) if sends else None

    def latest(rows):
        sent = [(last_send(row), row) for row in rows]
        sent = [item for item in sent if item[0] is not None]
        if sent:
            return max(sent, key=lambda item: item[0])[1]
        return max(rows, key=lambda row: _utc(row["request_created_at"]) or oldest)

    codes = set(find_references(body))
    quoted = [row for row in candidates if row.get("ref_code") and row["ref_code"] in codes]
    if quoted:
        return latest(quoted) if len({row["request_id"] for row in quoted}) == 1 else None
    # Only requests he had heard about from us when he wrote, and not those already awarded to someone else.
    heard = [row for row in candidates if last_send(row) is not None] or candidates
    open_rows = [row for row in heard if row.get("awarded_seller_id") in (None, "", row["seller_id"])] or heard
    if len({row["owner_user_id"] for row in open_rows}) > 1:
        return None
    return latest(open_rows)


def unfiled_for(rows: list[dict], seller_id: str | None, request_id: str) -> list[dict]:
    """The kept replies a seller may file into this request: his own, and only where it was a candidate."""
    if not seller_id or not request_id:
        return []
    key = seller_key(seller_id)
    return [
        {"id": row["haraj_message_id"], "body": row["body"], "sent_at": _iso_text(row["sent_at"]), "media": list(row.get("media") or [])}
        for row in rows
        if seller_key(row["seller_id"]) == key and request_id in row["candidate_request_ids"]
    ]


def filed_inbound(row: dict, threads: list[dict], media=None) -> tuple[dict, InboundMessage]:
    """The thread a kept reply goes to inside the request chosen for it, and the reply to record there."""
    sent_at = _iso_text(row["sent_at"]) or _now()
    thread = choose_thread(threads, row["body"], sent_at) or threads[0]
    kept = tuple(media) if media is not None else tuple(row.get("media") or ())
    return thread, InboundMessage(haraj_message_id=row["haraj_message_id"], body=row["body"], sent_at=sent_at, media=kept)


def _iso_text(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def search_seller_ids(results) -> set[str]:
    """The Haraj seller ids a search showed, the only ones a customer may then send a request to."""
    found = set()
    for result in results or ():
        seller = result.seller or (result.ad.seller if result.ad else None)
        if seller is not None and seller.id:
            found.add(seller_key(seller.id))
    return found


def seller_key(seller_id: str) -> str:
    """``haraj:seller:19676360`` and ``19676360`` are the same seller."""
    return author_id(seller_id) or str(seller_id).strip()


def request_summary(row, recipients: list[RequestRecipient], offers: list[Offer], messages) -> dict:
    awarded = row["awarded_seller_id"] if "awarded_seller_id" in (row.keys() if hasattr(row, "keys") else row) else None
    recipient_count = len(recipients)
    read_at = _moment(row["customer_read_at"]) if "customer_read_at" in row.keys() else None
    unread = sum(
        1
        for message in messages
        if message["sender_role"] == "seller" and (read_at is None or (_moment(message["created_at"]) or read_at) > read_at)
    )
    sent_count = sum(1 for item in recipients if item.send_status == "sent")
    failed_count = sum(1 for item in recipients if item.send_status == "failed")
    replied: set[str] = set()
    latest_offer = None
    for message in messages:
        if message["sender_role"] == "seller":
            replied.add(message["seller_id"] or _stamp(message["created_at"]))
            if message["offer_amount"] is not None:
                latest_offer = message
    newest_offer = bool(messages) and messages[-1]["sender_role"] == "seller" and messages[-1]["offer_amount"] is not None
    need_cards = []
    grouped: dict[str, list[RequestRecipient]] = {}
    for recipient in recipients:
        grouped.setdefault(recipient.need or row["need"] or row["original_text"], []).append(recipient)
    for label, group in grouped.items():
        group_offers = [item for item in offers if (item.need or label) == label]
        totals = [item.total_price for item in group_offers if item.total_price is not None]
        need_cards.append({"need": label, "recipient_count": len(group), "offer_count": len(group_offers), "lowest_total": min(totals) if totals else None})
    last = messages[-1] if messages else None
    preview = " ".join((last["body"] or "").split()) if last is not None else ""
    replies = max(len(replied), len(offers))
    latest_amount = None if latest_offer is None else float(latest_offer["offer_amount"])
    return {
        "id": row["id"],
        "original_text": row["original_text"],
        "need": row["need"],
        "city": row["city"],
        "created_at": _stamp(row["created_at"]),
        "last_synced_at": _stamp(row["last_synced_at"]),
        "seller_names": [item.seller_name for item in recipients],
        "last_message": preview[:180],
        "last_message_at": None if last is None else _stamp(last["created_at"]),
        "recipient_count": recipient_count,
        "sent_count": sent_count,
        "failed_count": failed_count,
        "queued_count": recipient_count - sent_count - failed_count,
        "replied_count": replies,
        "unread_count": unread,
        "waiting_count": max(0, recipient_count - replies),
        "has_new_offer": newest_offer or bool(offers),
        "latest_offer_amount": latest_amount if not offers else min((item.total_price for item in offers if item.total_price is not None), default=None),
        "latest_offer_currency": None if latest_offer is None else latest_offer["offer_currency"],
        "needs": need_cards,
        "awarded_seller_id": awarded,
    }


class Store:
    def __init__(self, path: Path, upload_dir: Path):
        self.path = path
        self.upload_dir = upload_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._settlement_lock = threading.Lock()
        self._db_lock = threading.Lock()  # Protects concurrent writes to _connection
        self._migrate()

    def _migrate(self) -> None:
        self._connection.executescript(
            """
            create table if not exists users (
              id text primary key,
              email text unique not null,
              password_hash text not null,
              salt text not null,
              created_at text not null
            );
            create table if not exists sessions (
              token text primary key,
              user_id text not null,
              created_at text not null
            );
            create table if not exists requests (
              id text primary key,
              owner_user_id text not null,
              original_text text not null,
              need text,
              notes text,
              city text,
              attributes_json text not null,
              reply_token text,
              created_at text not null
            );
            create table if not exists request_recipients (
              request_id text not null,
              seller_id text not null,
              seller_name text not null,
              ad_id text
            );
            create table if not exists attachments (
              id text primary key,
              request_id text not null,
              owner_user_id text not null,
              filename text not null,
              content_type text not null,
              size_bytes integer not null,
              path text not null,
              created_at text not null
            );
            create table if not exists messages (
              id text primary key,
              request_id text not null,
              sender_role text not null,
              sender_user_id text,
              seller_id text,
              body text not null,
              offer_amount real,
              offer_currency text,
              attachment_ids_json text not null,
              created_at text not null
            );
            create table if not exists notifications (
              id text primary key,
              user_id text not null,
              request_id text,
              kind text not null,
              created_at text not null
            );
            create table if not exists search_journeys (
              trace_id text primary key,
              user_id text,
              query text not null,
              state text not null,
              trace_json text not null,
              created_at text not null
            );
            create table if not exists subscription_plans (
              code text primary key,
              name_ar text not null,
              name_en text not null,
              description_ar text,
              price_amount integer not null,
              currency text not null default 'SAR',
              duration_days integer not null,
              features_json text not null default '[]',
              is_active integer not null default 1,
              is_placeholder_price integer not null default 0,
              moyasar_metadata_json text not null default '{}',
              created_at text not null,
              updated_at text not null
            );
            create table if not exists subscriptions (
              id text primary key,
              user_id text not null,
              plan text not null references subscription_plans(code),
              status text not null check (status in ('active', 'expired', 'cancelled', 'payment_pending')),
              starts_at text,
              expires_at text,
              created_at text not null,
              updated_at text not null
            );
            create index if not exists subscriptions_user_idx on subscriptions(user_id);
            create table if not exists payments (
              id text primary key,
              user_id text not null,
              provider text not null default 'moyasar',
              provider_payment_id text unique,
              amount integer not null,
              currency text not null default 'SAR',
              status text not null,
              subscription_id text references subscriptions(id),
              plan text references subscription_plans(code),
              source_type text,
              created_at text not null
            );
            create index if not exists payments_user_idx on payments(user_id);
            create table if not exists webhook_events (
              id text primary key,
              event_type text not null,
              provider_payment_id text,
              processed_at text not null
            );
            create table if not exists login_attempts (
              key text not null,
              attempted_at text not null
            );
            create index if not exists login_attempts_key_idx on login_attempts(key, attempted_at);
            create table if not exists idempotency_keys (
              user_id text not null,
              scope text not null,
              key text not null,
              fingerprint text not null,
              response_json text,
              created_at text not null,
              primary key (user_id, scope, key)
            );
            """
        )
        self._connection.commit()
        self._ensure_column("payments", "provider_status", "text")
        self._ensure_column("payments", "refunded_amount", "integer")
        self._hash_legacy_sessions()
        self._ensure_column("users", "trial_reset_at", "text")
        self._ensure_column("users", "unlimited", "integer")
        self._ensure_column("requests", "reply_token", "text")
        self._ensure_column("requests", "last_synced_at", "text")
        self._ensure_column("messages", "seller_id", "text")
        self._ensure_column("messages", "delivery_state", "text")
        self._ensure_column("messages", "need", "text")
        self._ensure_column("messages", "reply_to", "text")
        self._ensure_column("messages", "scope", "text")
        self._ensure_column("messages", "haraj_conversation_id", "text")
        self._ensure_column("messages", "haraj_message_id", "text")
        self._ensure_column("messages", "haraj_text", "text")
        self._ensure_column("requests", "customer_read_at", "text")
        self._ensure_column("requests", "awarded_seller_id", "text")
        self._ensure_column("requests", "awarded_at", "text")
        self._ensure_column("messages", "media_json", "text")
        self._ensure_column("users", "name", "text")
        self._connection.execute(
            "create table if not exists files (id text primary key, owner_user_id text not null, request_id text, content_type text not null,"
            " filename text not null, size_bytes integer not null, width integer, height integer, data blob not null, created_at text not null)"
        )
        self._connection.execute(
            "create table if not exists push_subscriptions (endpoint text primary key, user_id text not null, p256dh text not null, auth text not null, created_at text not null)"
        )
        self._connection.executescript(
            """
            create unique index if not exists messages_haraj_message on messages (haraj_message_id) where haraj_message_id is not null;
            create table if not exists haraj_threads (
              request_id text not null,
              seller_id text not null,
              need text not null default '',
              ad_id text,
              haraj_conversation_id text,
              last_fetched_at text,
              primary key (request_id, seller_id, need)
            );
            create table if not exists message_deliveries (
              id text primary key,
              message_id text not null,
              request_id text not null,
              seller_id text not null,
              need text not null default '',
              haraj_message_id text,
              delivery_status text not null,
              error text,
              attempts integer not null default 0,
              sent_at text,
              created_at text not null
            );
            update message_deliveries set delivery_status = 'queued' where delivery_status = 'sending';
            create table if not exists haraj_channel (
              key text primary key,
              value text,
              updated_at text not null
            );
            """
        )
        self._ensure_column("haraj_threads", "high_water", "integer")
        self._ensure_column("haraj_threads", "checked_at", "text")
        self._ensure_column("haraj_threads", "retry_at", "text")
        self._ensure_column("haraj_threads", "failure_code", "text")
        self._ensure_column("message_deliveries", "last_attempt_at", "text")
        self._ensure_column("request_recipients", "need", "text")
        self._ensure_column("request_recipients", "reply_token", "text")
        self._ensure_column("request_recipients", "send_status", "text")
        self._ensure_column("request_recipients", "listing_url", "text")
        self._connection.execute(
            """
            create table if not exists offers (
              id text primary key,
              request_id text not null,
              seller_id text not null,
              need text,
              provider_name text,
              phone text,
              base_price real,
              delivery_included integer,
              delivery_price real,
              total_price real,
              currency text,
              message text,
              created_at text not null
            )
            """
        )
        self._ensure_column("requests", "ref_code", "text")
        self._connection.executescript(
            """
            create table if not exists suppliers (
              id text primary key,
              name text not null,
              -- Null for a guest: a supplier who opened his invite link is recorded from
              -- what the link proves, and only a real sign-up fills these in.
              email text unique,
              phone text,
              password_hash text,
              salt text,
              activity_type text not null default 'both',
              description text,
              categories_json text not null default '[]',
              haraj_seller_id text unique,
              status text not null default 'pending',
              created_at text not null,
              updated_at text not null
            );
            create table if not exists supplier_sessions (
              token text primary key,
              supplier_id text not null references suppliers(id) on delete cascade,
              created_at text not null
            );
            create table if not exists supplier_funnel (
              id integer primary key autoincrement,
              step text not null,
              seller_id text not null,
              supplier_id text,
              request_id text,
              need text,
              channel text not null default 'haraj',
              created_at text not null
            );
            create unique index if not exists supplier_funnel_once
              on supplier_funnel (step, seller_id, coalesce(request_id, ''));
            create table if not exists supplier_push_subscriptions (
              endpoint text primary key,
              supplier_id text not null references suppliers(id) on delete cascade,
              p256dh text not null,
              auth text not null,
              created_at text not null
            );
            create table if not exists supplier_notifications (
              id text primary key,
              supplier_id text not null references suppliers(id) on delete cascade,
              request_id text,
              seller_id text,
              event text not null,
              title text not null,
              body text,
              url text,
              delivered_json text not null default '{}',
              read_at text,
              created_at text not null
            );
            create unique index if not exists supplier_notifications_once
              on supplier_notifications (supplier_id, event, request_id)
              where event in ('request_new', 'awarded', 'request_cancelled', 'closing_soon');
            """
        )
        self._ensure_column("users", "email_verified_at", "text")
        self._ensure_column("users", "farq_user_id", "text")
        self._connection.execute("create unique index if not exists users_farq_user_id_key on users (farq_user_id) where farq_user_id is not null")
        self._connection.commit()
        self._connection.executescript(
            """
            create table if not exists email_verifications (
              token text primary key,
              user_id text not null references users(id) on delete cascade,
              expires_at text not null,
              created_at text not null
            );
            """
        )
        self._ensure_column("suppliers", "capabilities_json", "text")
        self._ensure_column("suppliers", "services_json", "text")
        self._ensure_column("suppliers", "products_json", "text")
        self._ensure_column("requests", "contact_phone", "text")
        self._ensure_column("requests", "contact_lat", "real")
        self._ensure_column("requests", "contact_lng", "real")
        self._ensure_column("requests", "contact_shared_at", "text")
        self._ensure_column("message_deliveries", "sent_at", "text")
        self._ensure_column("subscription_plans", "monthly_items", "integer")
        self._ensure_column("subscription_plans", "sellers_per_item", "integer")
        self._ensure_column("subscription_plans", "daily_contacts", "integer")
        self._ensure_column("subscriptions", "period_anchor", "text")
        self._connection.executescript(
            """
            create unique index if not exists requests_ref_code on requests (ref_code) where ref_code is not null;
            create table if not exists haraj_unmatched (
              haraj_message_id text primary key,
              haraj_conversation_id text not null,
              seller_id text not null,
              body text not null,
              media_json text,
              sent_at text,
              candidate_request_ids_json text not null,
              created_at text not null
            );
            create table if not exists search_sellers (
              trace_id text not null,
              user_id text,
              seller_id text not null,
              created_at text not null,
              primary key (trace_id, seller_id)
            );
            create index if not exists search_sellers_user on search_sellers (user_id, created_at);
            """
        )
        self._connection.commit()
        self._seed_plans()

    def _seed_plans(self) -> None:
        """The three plans sold from 2026-09-23 (OPTION B). Same rows as the Postgres
        migration 20260923160000_taseer_plan_quotas.sql, so local runs and tests see the
        prices and quotas production sells. Prices are in halalas (SAR x 100)."""
        now = _now()
        for code, name_ar, name_en, description_ar, price, features, items, sellers, daily in PLAN_CATALOG:
            self._connection.execute(
                """
                insert into subscription_plans
                  (code, name_ar, name_en, description_ar, price_amount, currency, duration_days,
                   features_json, is_active, is_placeholder_price, moyasar_metadata_json,
                   monthly_items, sellers_per_item, daily_contacts, created_at, updated_at)
                values (?, ?, ?, ?, ?, 'SAR', 30, ?, 1, 0, '{}', ?, ?, ?, ?, ?)
                on conflict (code) do update set
                  name_ar = excluded.name_ar, name_en = excluded.name_en,
                  description_ar = excluded.description_ar, price_amount = excluded.price_amount,
                  features_json = excluded.features_json, is_active = 1, is_placeholder_price = 0,
                  monthly_items = excluded.monthly_items, sellers_per_item = excluded.sellers_per_item,
                  daily_contacts = excluded.daily_contacts, updated_at = excluded.updated_at
                """,
                (code, name_ar, name_en, description_ar, price,
                 json.dumps(features, ensure_ascii=False), items, sellers, daily, now, now),
            )
        # The 1 SAR sandbox placeholder is retired; any subscription on it keeps working.
        self._connection.execute(
            "update subscription_plans set is_active = 0, updated_at = ? where code = 'monthly_placeholder'",
            (now,),
        )
        self._connection.commit()

    def _hash_legacy_sessions(self) -> None:
        """Sessions issued before tokens were hashed keep working: store their digest instead."""
        rows = self._connection.execute("select token from sessions where length(token) <> 64").fetchall()
        for row in rows:
            self._connection.execute("update sessions set token = ? where token = ?", (token_digest(row["token"]), row["token"]))
        if rows:
            self._connection.commit()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        names = {row[1] for row in self._connection.execute(f"pragma table_info({table})")}
        if column not in names:
            self._connection.execute(f"alter table {table} add column {column} {declaration}")
            self._connection.commit()

    def register(self, email: str, password: str, name: str | None = None) -> str:
        user_id = uuid4().hex
        salt = secrets.token_hex(16)
        try:
            self._connection.execute(
                "insert into users (id, email, password_hash, salt, name, created_at) values (?, ?, ?, ?, ?, ?)",
                (user_id, email.lower().strip(), _hash_password(password, salt), salt, name, _now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("email already registered") from exc
        self._connection.commit()
        return user_id

    def login(self, email: str, password: str) -> str | None:
        row = self._connection.execute("select * from users where email = ?", (email.lower().strip(),)).fetchone()
        if not password_matches(password, row):
            return None
        token = secrets.token_urlsafe(32)
        self._connection.execute(
            "insert into sessions (token, user_id, created_at) values (?, ?, ?)",
            (token_digest(token), row["id"], _now()),
        )
        self._connection.execute("delete from sessions where user_id = ? and created_at < ?", (row["id"], session_cutoff().isoformat()))
        self._connection.commit()
        return token

    def login_farq(self, farq_user_id: str, email: str, name: str | None, email_verified: bool) -> str:
        """See PgStore.login_farq."""
        email = email.lower().strip()
        row = self._connection.execute("select id, name, email_verified_at from users where farq_user_id = ?", (farq_user_id,)).fetchone()
        if row is None:
            same = self._connection.execute("select id, name, email_verified_at, farq_user_id from users where email = ?", (email,)).fetchone()
            if same is not None:
                if not email_verified or self._col(same, "farq_user_id"):
                    raise ValueError("email belongs to another account")
                self._connection.execute("update users set farq_user_id = ? where id = ?", (farq_user_id, same["id"]))
                row = same
            else:
                user_id = uuid4().hex
                salt = secrets.token_hex(16)
                self._connection.execute(
                    "insert into users (id, email, password_hash, salt, name, created_at, farq_user_id) values (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, email, _hash_password(secrets.token_urlsafe(32), salt), salt, name, _now(), farq_user_id),
                )
                row = {"id": user_id, "name": name, "email_verified_at": None}
        current_name = row["name"] if isinstance(row, dict) else self._col(row, "name")
        verified_at = row["email_verified_at"] if isinstance(row, dict) else self._col(row, "email_verified_at")
        if name and not current_name:
            self._connection.execute("update users set name = ? where id = ?", (name, row["id"]))
        if email_verified and not verified_at:
            self._connection.execute("update users set email_verified_at = ? where id = ?", (_now(), row["id"]))
        token = secrets.token_urlsafe(32)
        self._connection.execute(
            "insert into sessions (token, user_id, created_at) values (?, ?, ?)",
            (token_digest(token), row["id"], _now()),
        )
        self._connection.execute("delete from sessions where user_id = ? and created_at < ?", (row["id"], session_cutoff().isoformat()))
        self._connection.commit()
        return token

    def link_farq(self, user_id: str, farq_user_id: str) -> bool:
        """See PgStore.link_farq."""
        other = self._connection.execute("select id from users where farq_user_id = ? and id <> ?", (farq_user_id, user_id)).fetchone()
        if other is not None:
            return False
        self._connection.execute("update users set farq_user_id = ? where id = ?", (farq_user_id, user_id))
        self._connection.commit()
        return True

    # -- email verification ----------------------------------------------------

    def start_email_verification(self, user_id: str, ttl_hours: int = 48) -> str:
        """See PgStore.start_email_verification."""
        token = secrets.token_urlsafe(24)
        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        self._connection.execute("delete from email_verifications where user_id = ?", (user_id,))
        self._connection.execute(
            "insert into email_verifications (token, user_id, expires_at, created_at) values (?, ?, ?, ?)",
            (token_digest(token), user_id, expires, _now()),
        )
        self._connection.commit()
        return token

    def verify_email(self, token: str) -> str | None:
        if not token:
            return None
        digest = token_digest(token)
        row = self._connection.execute(
            "select user_id, expires_at from email_verifications where token = ?", (digest,)
        ).fetchone()
        if row is None:
            return None
        self._connection.execute("delete from email_verifications where token = ?", (digest,))
        if row["expires_at"] < _now():
            self._connection.commit()
            return None
        self._connection.execute("update users set email_verified_at = ? where id = ?", (_now(), row["user_id"]))
        self._connection.commit()
        return row["user_id"]

    def email_verified(self, user_id: str) -> bool:
        row = self._connection.execute("select email_verified_at from users where id = ?", (user_id,)).fetchone()
        return bool(row and self._col(row, "email_verified_at"))

    def start_guest(self) -> dict:
        email = f"guest-{uuid4().hex}@users.farq.local"
        password = secrets.token_urlsafe(18)
        user_id = self.register(email, password)
        token = self.login(email, password)
        return {"user_id": user_id, "token": token}

    def _session_user(self, token: str) -> str | None:
        if not token:
            return None
        keys = session_keys(token)
        marks = ", ".join("?" for _ in keys)
        row = self._connection.execute(f"select token, user_id, created_at from sessions where token in ({marks})", keys).fetchone()
        if row is None:
            return None
        if row["created_at"] < session_cutoff().isoformat():
            self._connection.execute("delete from sessions where token = ?", (row["token"],))
            self._connection.commit()
            return None
        if row["token"] != keys[0]:
            # A session from before tokens were hashed: keep only the digest from now on.
            self._connection.execute("update sessions set token = ? where token = ?", (keys[0], row["token"]))
            self._connection.commit()
        return row["user_id"]

    def user_for_token(self, token: str) -> str | None:
        return self._session_user(token)

    def account_for_token(self, token: str) -> dict | None:
        user_id = self._session_user(token)
        if user_id is None:
            return None
        row = self._connection.execute("select id, email, name, farq_user_id from users where id = ?", (user_id,)).fetchone()
        return None if row is None else dict(row)

    def logout(self, token: str) -> None:
        keys = session_keys(token)
        marks = ", ".join("?" for _ in keys)
        self._connection.execute(f"delete from sessions where token in ({marks})", keys)
        self._connection.commit()

    def login_failures(self, key: str, window_seconds: int) -> int:
        since = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
        row = self._connection.execute("select count(*) as count from login_attempts where key = ? and attempted_at >= ?", (key, since)).fetchone()
        return row["count"]

    def record_login_failure(self, keys: list[str]) -> None:
        now = _now()
        self._connection.executemany("insert into login_attempts (key, attempted_at) values (?, ?)", [(key, now) for key in keys])
        self._connection.execute("delete from login_attempts where attempted_at < ?", ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),))
        self._connection.commit()

    def clear_login_failures(self, key: str) -> None:
        self._connection.execute("delete from login_attempts where key = ?", (key,))
        self._connection.commit()

    # -- idempotency ----------------------------------------------------------------

    def reserve_idempotency(self, user_id: str, scope: str, key: str, fingerprint: str) -> dict | None:
        """Claims (user, scope, key). Returns None when this call now owns the key, otherwise
        the earlier claim {"fingerprint", "response"}; response is None while it is still running."""
        try:
            self._connection.execute(
                "insert into idempotency_keys (user_id, scope, key, fingerprint, response_json, created_at) values (?, ?, ?, ?, null, ?)",
                (user_id, scope, key, fingerprint, _now()),
            )
            self._connection.commit()
            return None
        except sqlite3.IntegrityError:
            row = self._connection.execute(
                "select fingerprint, response_json from idempotency_keys where user_id = ? and scope = ? and key = ?", (user_id, scope, key)
            ).fetchone()
            if row is None:
                return self.reserve_idempotency(user_id, scope, key, fingerprint)
            return {"fingerprint": row["fingerprint"], "response": None if row["response_json"] is None else json.loads(row["response_json"])}

    def complete_idempotency(self, user_id: str, scope: str, key: str, response: dict) -> None:
        self._connection.execute(
            "update idempotency_keys set response_json = ? where user_id = ? and scope = ? and key = ?",
            (json.dumps(response, ensure_ascii=False), user_id, scope, key),
        )
        self._connection.commit()

    def release_idempotency(self, user_id: str, scope: str, key: str) -> None:
        """A failed attempt gives its key back so the client can retry with it."""
        self._connection.execute(
            "delete from idempotency_keys where user_id = ? and scope = ? and key = ? and response_json is null", (user_id, scope, key)
        )
        self._connection.commit()

    def record_journey(self, trace_id: str, user_id: str | None, query: str, state: str, trace: dict) -> None:
        with self._db_lock:
            self._connection.execute(
                "insert or replace into search_journeys (trace_id, user_id, query, state, trace_json, created_at) values (?, ?, ?, ?, ?, ?)",
                (trace_id, user_id, query, state, json.dumps(trace, ensure_ascii=False), _now()),
            )
            self._connection.commit()

    def journey(self, trace_id: str, owner_user_id: str | None = None) -> dict | None:
        """With an owner, only that account's own searches are found."""
        if owner_user_id is None:
            row = self._connection.execute("select trace_json from search_journeys where trace_id = ?", (trace_id,)).fetchone()
        else:
            row = self._connection.execute(
                "select trace_json from search_journeys where trace_id = ? and user_id = ?", (trace_id, owner_user_id)
            ).fetchone()
        return None if row is None else json.loads(row["trace_json"])

    def create_request(
        self,
        owner_user_id: str,
        original_text: str,
        need: str | None,
        notes: str | None,
        city: str | None,
        attributes: dict,
        recipients: list[RequestRecipient],
        request_id: str | None = None,
    ) -> str:
        if not recipients:
            raise ValueError("at least one recipient is required")
        city_name = known_city(city)
        if city_name is None:
            raise ValueError("city is required")
        # The id may be handed in so a caller can spend ledger credits against it first.
        request_id = request_id or uuid4().hex
        first_token = secrets.token_urlsafe(16)
        created = _now()
        ref_code = next(code for code in iter(new_reference, None) if self._connection.execute("select 1 from requests where ref_code = ?", (code,)).fetchone() is None)
        self._connection.execute(
            "insert into requests (id, owner_user_id, original_text, need, notes, city, attributes_json, reply_token, created_at, ref_code) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, owner_user_id, original_text, need, notes, city_name, json.dumps(attributes, ensure_ascii=False), first_token, created, ref_code),
        )
        for index, item in enumerate(recipients):
            token = item.reply_token or (first_token if index == 0 else secrets.token_urlsafe(16))
            self._connection.execute(
                "insert into request_recipients (request_id, seller_id, seller_name, ad_id, need, reply_token, send_status, listing_url) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request_id,
                    item.seller_id,
                    item.seller_name,
                    item.ad_id,
                    item.need or need,
                    token,
                    "queued",
                    item.listing_url,
                ),
            )
        # The quote request is the first message on each item, routed to every seller on that item.
        needs = list(dict.fromkeys((item.need or need or "") for item in recipients))
        for item_need in needs:
            lines = ["طلب عرض سعر", (item_need or need or original_text or "").strip(), f"المدينة: {city_name}"]
            if notes and notes.strip():
                lines.append(notes.strip())
            self._enqueue(
                request_id,
                "\n".join(line for line in lines if line),
                item_need or None,
                None,
                None,
                owner_user_id,
                haraj_text=invite_text(item_need or need or original_text, city_name),
            )
        self._connection.commit()
        return request_id

    def add_attachment(self, request_id: str, owner_user_id: str, filename: str, content_type: str, content: bytes) -> Attachment:
        if not content:
            raise ValueError("empty attachment")
        attachment_id = uuid4().hex
        safe_name = Path(filename).name
        path = self.upload_dir / f"{attachment_id}_{safe_name}"
        path.write_bytes(content)
        self._connection.execute(
            "insert into attachments (id, request_id, owner_user_id, filename, content_type, size_bytes, path, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (attachment_id, request_id, owner_user_id, safe_name, content_type, len(content), str(path), _now()),
        )
        self._connection.commit()
        return Attachment(id=attachment_id, filename=safe_name, content_type=content_type, size_bytes=len(content))

    def add_message(
        self,
        request_id: str,
        sender_role: str,
        sender_user_id: str | None,
        body: str,
        offer: Offer | None = None,
        attachment_ids: list[str] | None = None,
        seller_id: str | None = None,
        need: str | None = None,
        reply_to: str | None = None,
        scope: str | None = None,
        created_at: str | None = None,
        haraj_conversation_id: str | None = None,
        haraj_message_id: str | None = None,
    ) -> Message:
        message_id = uuid4().hex
        with self._db_lock:
            created = created_at or _now()
            self._connection.execute(
                "insert into messages (id, request_id, sender_role, sender_user_id, seller_id, need, reply_to, scope, haraj_conversation_id, haraj_message_id, body, offer_amount, offer_currency, attachment_ids_json, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    request_id,
                    sender_role,
                    sender_user_id,
                    seller_id,
                    need,
                    reply_to,
                    scope,
                    haraj_conversation_id,
                    haraj_message_id,
                    body,
                    None if offer is None else offer.amount,
                    None if offer is None else offer.currency,
                    json.dumps(attachment_ids or []),
                    created,
                ),
            )
            if sender_user_id:
                self._connection.execute(
                    "insert into notifications (id, user_id, request_id, kind, created_at) values (?, ?, ?, ?, ?)",
                    (uuid4().hex, sender_user_id, request_id, "message", created),
                )
            self._connection.commit()
        return Message(
            id=message_id,
            request_id=request_id,
            sender_role=sender_role,
            seller_id=seller_id,
            need=need,
            reply_to=reply_to,
            direction="seller_to_customer" if sender_role == "seller" else "customer_to_seller",
            scope=scope,
            haraj_conversation_id=haraj_conversation_id,
            body=body,
            offer=offer,
            attachment_ids=attachment_ids or [],
            created_at=created,
        )

    def get_request(self, request_id: str, owner_user_id: str) -> RequestRecord | None:
        row = self._connection.execute(
            "select * from requests where id = ? and owner_user_id = ?",
            (request_id, owner_user_id),
        ).fetchone()
        if row is None:
            return None
        recipients = [self._recipient_from_row(item) for item in self._connection.execute("select * from request_recipients where request_id = ?", (request_id,))]
        attachments = [
            Attachment(id=item["id"], filename=item["filename"], content_type=item["content_type"], size_bytes=item["size_bytes"])
            for item in self._connection.execute("select * from attachments where request_id = ?", (request_id,))
        ]
        messages = self._messages_for_request(request_id)
        return RequestRecord(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            original_text=row["original_text"],
            need=row["need"],
            notes=row["notes"],
            city=row["city"],
            attributes=json.loads(row["attributes_json"]),
            recipients=recipients,
            attachments=attachments,
            messages=messages,
            offers=self._offers_for_request(request_id),
            reply_token=row["reply_token"],
            awarded_seller_id=self._col(row, "awarded_seller_id"),
            awarded_at=self._col(row, "awarded_at"),
            contact_shared=bool(self._col(row, "contact_shared_at")),
            last_synced_at=row["last_synced_at"] if "last_synced_at" in row.keys() else None,
            ref_code=self._col(row, "ref_code"),
            created_at=row["created_at"],
        )

    def _messages_for_request(self, request_id: str) -> list[Message]:
        deliveries: dict[str, list[dict]] = {}
        for item in self._connection.execute("select message_id, seller_id, delivery_status, sent_at from message_deliveries where request_id = ? order by created_at", (request_id,)):
            deliveries.setdefault(item["message_id"], []).append({"seller_id": item["seller_id"], "status": item["delivery_status"], "sent_at": item["sent_at"]})
        messages = []
        for item in self._connection.execute("select * from messages where request_id = ? order by created_at", (request_id,)):
            offer = None
            if item["offer_amount"] is not None:
                offer = Offer(amount=item["offer_amount"], currency=item["offer_currency"])
            routed = deliveries.get(item["id"], [])
            messages.append(
                Message(
                    id=item["id"],
                    request_id=request_id,
                    sender_role=item["sender_role"],
                    seller_id=item["seller_id"],
                    need=item["need"],
                    reply_to=item["reply_to"],
                    direction="seller_to_customer" if item["sender_role"] == "seller" else "customer_to_seller",
                    scope=item["scope"],
                    haraj_conversation_id=item["haraj_conversation_id"],
                    body=item["body"],
                    offer=offer,
                    attachment_ids=json.loads(item["attachment_ids_json"]),
                    created_at=item["created_at"],
                    delivery_state=_delivery_state(routed) if routed else None,
                    deliveries=routed,
                    media=json.loads(item["media_json"]) if item["media_json"] else [],
                )
            )
        return messages

    # --- Router: customer -> Haraj conversations -------------------------------------------------

    def _enqueue(
        self,
        request_id: str,
        body: str,
        need: str | None,
        seller_id: str | None,
        reply_to: str | None,
        owner_user_id: str | None = None,
        haraj_text: str | None = None,
        seller_ids=None,
        media: list[dict] | None = None,
    ) -> Message:
        recipients = self._connection.execute("select * from request_recipients where request_id = ?", (request_id,)).fetchall()
        default_need = self._connection.execute("select need from requests where id = ?", (request_id,)).fetchone()["need"]
        item_of = lambda row: row["need"] or default_need or ""
        targets, need, scope = route_targets(recipients, default_need, need, seller_id, seller_ids)
        single = targets[0]["seller_id"] if scope == SINGLE_SELLER else None
        message = self.add_message(request_id, "user", owner_user_id, body, None, seller_id=single, need=need, reply_to=reply_to, scope=scope)
        if haraj_text is not None:
            self._connection.execute("update messages set haraj_text = ? where id = ?", (haraj_text, message.id))
        if media:
            self._connection.execute("update messages set media_json = ? where id = ?", (json.dumps(media, ensure_ascii=False), message.id))
        created = _now()
        in_app = self._registered_sellers([row["seller_id"] for row in targets])
        for row in targets:
            self._connection.execute(
                "insert or ignore into haraj_threads (request_id, seller_id, need, ad_id) values (?, ?, ?, ?)",
                (request_id, row["seller_id"], item_of(row), row["ad_id"]),
            )
            # A registered supplier reads this in his own app: not a Haraj send, so it never
            # spends a slot of the platform-wide 20-second spacing. See PgStore._enqueue.
            direct = seller_key(row["seller_id"]) in in_app
            self._connection.execute(
                "insert into message_deliveries (id, message_id, request_id, seller_id, need, delivery_status, sent_at, created_at)"
                " values (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid4().hex, message.id, request_id, row["seller_id"], item_of(row),
                 "in_app" if direct else "queued", created if direct else None, created),
            )
            if direct:
                self._connection.execute(
                    "update request_recipients set send_status = 'sent' where request_id = ? and seller_id = ? and coalesce(need, '') = ?",
                    (request_id, row["seller_id"], item_of(row)),
                )
            # Step 1 the first time we write to him about this request, and step 6 once he
            # has already priced it and the customer is answering him. See PgStore._enqueue.
            lane = "in_app" if direct else "haraj"
            self.track_supplier("invite_received", row["seller_id"], request_id=request_id, need=item_of(row), channel=lane)
            priced = self._connection.execute(
                "select 1 from offers where request_id = ? and seller_id = ? limit 1", (request_id, row["seller_id"])
            ).fetchone()
            if priced is not None:
                self.track_supplier("buyer_replied", row["seller_id"], request_id=request_id, need=item_of(row), channel=lane)
        self._connection.commit()
        return next(item for item in self._messages_for_request(request_id) if item.id == message.id)

    def _registered_sellers(self, seller_ids) -> set[str]:
        keys = sorted({seller_key(item) for item in seller_ids if item})
        if not keys:
            return set()
        marks = ",".join("?" * len(keys))
        rows = self._connection.execute(
            f"select haraj_seller_id from suppliers where status = 'active' and haraj_seller_id in ({marks})", keys
        ).fetchall()
        return {row["haraj_seller_id"] for row in rows}

    def requests_closing_soon(self, after_hours: int = 20, before_hours: int = 72) -> list[dict]:
        """See PgStore.requests_closing_soon."""
        after = (datetime.now(timezone.utc) - timedelta(hours=after_hours)).isoformat()
        before = (datetime.now(timezone.utc) - timedelta(hours=before_hours)).isoformat()
        rows = self._connection.execute(
            """
            select r.id as request_id, p.seller_id, p.need
              from requests r join request_recipients p on p.request_id = r.id
             where r.awarded_seller_id is null and r.created_at < ? and r.created_at > ?
               and exists (select 1 from offers o where o.request_id = r.id)
               and not exists (select 1 from offers o2 where o2.request_id = r.id and o2.seller_id = p.seller_id)
            """,
            (after, before),
        ).fetchall()
        return [dict(row) for row in rows]

    def queue_health(self) -> dict:
        row = self._connection.execute(
            "select sum(delivery_status = 'queued') as queued, sum(delivery_status = 'sending') as sending,"
            " min(case when delivery_status = 'queued' then created_at end) as oldest from message_deliveries"
        ).fetchone()
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        in_app = self._connection.execute(
            "select count(*) as count from message_deliveries where delivery_status = 'in_app' and created_at > ?", (since,)
        ).fetchone()["count"]
        haraj = self._connection.execute(
            "select count(*) as count from message_deliveries where delivery_status in ('sent','queued','sending') and created_at > ?", (since,)
        ).fetchone()["count"]
        paused = self.get_value("send_paused_until")
        oldest = 0.0
        if row["oldest"]:
            oldest = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(row["oldest"])).total_seconds())
        queued = int(row["queued"] or 0)
        total = in_app + haraj
        return {
            "queued": queued,
            "sending": int(row["sending"] or 0),
            "oldest_queued_seconds": round(oldest),
            "drain_minutes": round(queued / 3, 1),
            "send_paused": bool(paused and float(paused) > datetime.now(timezone.utc).timestamp()),
            "in_app_share_30d": None if not total else round(in_app / total, 4),
        }

    # -- supplier notifications -----------------------------------------------

    def supplier_push_subscriptions(self, supplier_id: str) -> list[dict]:
        rows = self._connection.execute(
            "select endpoint, p256dh, auth from supplier_push_subscriptions where supplier_id = ?", (supplier_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def save_supplier_push_subscription(self, supplier_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        self._connection.execute(
            "insert into supplier_push_subscriptions (endpoint, supplier_id, p256dh, auth, created_at)"
            " values (?, ?, ?, ?, ?) on conflict (endpoint) do update set supplier_id = excluded.supplier_id,"
            " p256dh = excluded.p256dh, auth = excluded.auth",
            (endpoint, supplier_id, p256dh, auth, _now()),
        )
        self._connection.commit()

    def remove_supplier_push_subscription(self, endpoint: str) -> None:
        self._connection.execute("delete from supplier_push_subscriptions where endpoint = ?", (endpoint,))
        self._connection.commit()

    def suppliers_for_sellers(self, seller_ids) -> list[dict]:
        keys = sorted({seller_key(item) for item in seller_ids or () if item})
        if not keys:
            return []
        marks = ",".join("?" * len(keys))
        rows = self._connection.execute(
            f"select * from suppliers where status = 'active' and haraj_seller_id in ({marks})", keys
        ).fetchall()
        return [self._supplier_row(row) for row in rows]

    def reply_token_for(self, request_id: str, seller_id: str | None) -> str | None:
        if not (request_id and seller_id):
            return None
        key = seller_key(seller_id)
        row = self._connection.execute(
            "select reply_token from request_recipients where request_id = ? and (seller_id = ? or seller_id = ?) limit 1",
            (request_id, key, f"haraj:seller:{key}"),
        ).fetchone()
        return None if row is None else row["reply_token"]

    def add_supplier_notification(self, *, supplier_id: str, event: str, title: str, body: str | None,
                                  url: str | None, request_id: str | None = None, seller_id: str | None = None) -> dict | None:
        notification_id = uuid4().hex
        created = _now()
        cursor = self._connection.execute(
            "insert or ignore into supplier_notifications (id, supplier_id, request_id, seller_id, event, title, body, url, delivered_json, created_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?)",
            (notification_id, supplier_id, request_id, seller_id, event, title, body, url, created),
        )
        self._connection.commit()
        return None if cursor.rowcount == 0 else {"id": notification_id, "created_at": created}

    def set_notification_delivery(self, notification_id: str, delivered: dict) -> None:
        self._connection.execute(
            "update supplier_notifications set delivered_json = ? where id = ?",
            (json.dumps(delivered, ensure_ascii=False), notification_id),
        )
        self._connection.commit()

    def supplier_notifications(self, supplier_id: str, limit: int = 50) -> dict:
        rows = self._connection.execute(
            "select id, event, title, body, url, request_id, read_at, created_at from supplier_notifications"
            " where supplier_id = ? order by created_at desc limit ?",
            (supplier_id, limit),
        ).fetchall()
        unread = self._connection.execute(
            "select count(*) as count from supplier_notifications where supplier_id = ? and read_at is null", (supplier_id,)
        ).fetchone()["count"]
        return {
            "notifications": [
                {**{key: row[key] for key in ("id", "event", "title", "body", "url", "request_id")},
                 "read": row["read_at"] is not None, "created_at": row["created_at"]}
                for row in rows
            ],
            "unread": int(unread),
        }

    def mark_supplier_notifications_read(self, supplier_id: str, notification_id: str | None = None) -> int:
        if notification_id:
            cursor = self._connection.execute(
                "update supplier_notifications set read_at = ? where supplier_id = ? and id = ? and read_at is null",
                (_now(), supplier_id, notification_id),
            )
        else:
            cursor = self._connection.execute(
                "update supplier_notifications set read_at = ? where supplier_id = ? and read_at is null",
                (_now(), supplier_id),
            )
        self._connection.commit()
        return cursor.rowcount

    # -- the supplier funnel --------------------------------------------------

    def track_supplier(self, step: str, seller_id: str | None, *, request_id: str | None = None,
                       supplier_id: str | None = None, need: str | None = None, channel: str = "haraj") -> None:
        """See PgStore.track_supplier. Recorded once per supplier per request."""
        key = seller_key(seller_id) if seller_id else None
        if not key:
            return
        try:
            self._connection.execute(
                "insert or ignore into supplier_funnel (step, seller_id, supplier_id, request_id, need, channel, created_at)"
                " values (?, ?, ?, ?, ?, ?, ?)",
                (step, key, supplier_id, request_id, need, channel, _now()),
            )
            self._connection.commit()
        except Exception:  # noqa: BLE001 - measurement must never break the journey it measures
            pass

    def supplier_funnel(self, since_days: int = 30) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        rows = self._connection.execute(
            "select step, channel, count(distinct seller_id) as suppliers, count(*) as events"
            " from supplier_funnel where created_at >= ? group by step, channel",
            (since,),
        ).fetchall()
        steps = ["invite_received", "opened", "registered", "request_viewed", "quote_submitted", "buyer_replied", "awarded"]
        by_step = {step: {"suppliers": 0, "events": 0, "haraj": 0, "in_app": 0} for step in steps}
        for row in rows:
            slot = by_step.setdefault(row["step"], {"suppliers": 0, "events": 0, "haraj": 0, "in_app": 0})
            slot["suppliers"] += int(row["suppliers"])
            slot["events"] += int(row["events"])
            slot[row["channel"]] = slot.get(row["channel"], 0) + int(row["events"])
        out = []
        previous = None
        for step in steps:
            slot = by_step[step]
            out.append({"step": step, **slot,
                        "from_previous": None if previous in (None, 0) else round(slot["suppliers"] / previous, 4)})
            previous = slot["suppliers"]
        top = by_step[steps[0]]["suppliers"]
        for row in out:
            row["from_invite"] = None if not top else round(row["suppliers"] / top, 4)
        return {"days": since_days, "steps": out}

    # -- supplier accounts ----------------------------------------------------

    def _supplier_row(self, row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"],
            "activity_type": row["activity_type"],
            "description": row["description"],
            "categories": json.loads(row["categories_json"] or "[]"),
            "capabilities": json.loads(self._col(row, "capabilities_json") or "[]"),
            "services": json.loads(self._col(row, "services_json") or "[]"),
            "products": json.loads(self._col(row, "products_json") or "[]"),
            "haraj_seller_id": row["haraj_seller_id"],
            "status": row["status"],
            "created_at": row["created_at"],
        }

    def seller_id_for_reply_token(self, token: str) -> str | None:
        if not token:
            return None
        row = self._connection.execute("select seller_id from request_recipients where reply_token = ?", (token,)).fetchone()
        return None if row is None else seller_key(row["seller_id"])

    def register_supplier(self, *, name: str, email: str, phone: str, password: str,
                          activity_type: str = "both", description: str | None = None,
                          categories=None, capabilities=None, services=None, products=None,
                          haraj_seller_id: str | None = None) -> dict:
        supplier_id = uuid4().hex
        salt = secrets.token_hex(16)
        bound = seller_key(haraj_seller_id) if haraj_seller_id else None
        existing = self._connection.execute("select * from suppliers where haraj_seller_id = ?", (bound,)).fetchone() if bound else None
        if existing is not None:
            if existing["status"] != "guest":
                raise ValueError("seller already registered")
            # He already has the record he got for opening his link. Signing up properly
            # fills it in rather than colliding with it, or the guest record we created for
            # his convenience would lock him out of his own account.
            try:
                self._connection.execute(
                    "update suppliers set name = ?, email = ?, phone = ?, password_hash = ?, salt = ?,"
                    " activity_type = ?, description = ?, categories_json = ?, capabilities_json = ?,"
                    " services_json = ?, products_json = ?, status = 'active', updated_at = ? where id = ?",
                    (name.strip(), email.lower().strip(), phone.strip(),
                     _hash_password(password, salt), salt, activity_type, (description or "").strip() or None,
                     json.dumps(list(categories or []), ensure_ascii=False),
                     json.dumps(list(capabilities or []), ensure_ascii=False),
                     json.dumps(list(services or []), ensure_ascii=False),
                     json.dumps(list(products or []), ensure_ascii=False),
                     _now(), existing["id"]),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("email already registered") from exc
            self._connection.commit()
            return self._supplier_row(self._connection.execute("select * from suppliers where id = ?", (existing["id"],)).fetchone())
        try:
            self._connection.execute(
                "insert into suppliers (id, name, email, phone, password_hash, salt, activity_type, description,"
                " categories_json, capabilities_json, services_json, products_json, haraj_seller_id, status, created_at, updated_at)"
                " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (supplier_id, name.strip(), email.lower().strip(), phone.strip(),
                 _hash_password(password, salt), salt, activity_type, (description or "").strip() or None,
                 json.dumps(list(categories or []), ensure_ascii=False),
                 json.dumps(list(capabilities or []), ensure_ascii=False),
                 json.dumps(list(services or []), ensure_ascii=False),
                 json.dumps(list(products or []), ensure_ascii=False),
                 bound, "active" if bound else "pending", _now(), _now()),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("email already registered") from exc
        self._connection.commit()
        return self._supplier_row(self._connection.execute("select * from suppliers where id = ?", (supplier_id,)).fetchone())

    def supplier_by_seller_id(self, seller_id: str | None) -> dict | None:
        bound = seller_key(seller_id) if seller_id else None
        if not bound:
            return None
        row = self._connection.execute("select * from suppliers where haraj_seller_id = ?", (bound,)).fetchone()
        return self._supplier_row(row) if row is not None else None

    def ensure_guest_supplier(self, seller_id: str | None, name: str | None) -> dict | None:
        """The record a supplier gets for opening his link, without being asked anything.

        Returns the existing row if he already has one, so opening the link twice - or a
        second request's link - is still one supplier. It grants nothing: no session, no
        password, and no move off the Haraj lane. It exists so that a man who has already
        shown up is known, and so the day a reach channel is turned on the ask is one tap
        rather than the seven-field form that nobody has crossed.
        """
        bound = seller_key(seller_id) if seller_id else None
        if not bound:
            return None
        found = self.supplier_by_seller_id(bound)
        if found is not None:
            return found
        supplier_id = uuid4().hex
        self._connection.execute(
            "insert into suppliers (id, name, email, phone, password_hash, salt, activity_type, description,"
            " categories_json, capabilities_json, services_json, products_json, haraj_seller_id, status, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (supplier_id, (name or "").strip() or bound, None, None, None, None, "both", None,
             "[]", "[]", "[]", "[]", bound, "guest", _now(), _now()),
        )
        self._connection.commit()
        return self._supplier_row(self._connection.execute("select * from suppliers where id = ?", (supplier_id,)).fetchone())

    def login_supplier(self, email: str, password: str) -> str | None:
        row = self._connection.execute("select * from suppliers where email = ?", (email.lower().strip(),)).fetchone()
        if not password_matches(password, row):
            return None
        token = secrets.token_urlsafe(32)
        self._connection.execute(
            "insert into supplier_sessions (token, supplier_id, created_at) values (?, ?, ?)",
            (token_digest(token), row["id"], _now()),
        )
        self._connection.execute(
            "delete from supplier_sessions where supplier_id = ? and created_at < ?",
            (row["id"], session_cutoff().isoformat()),
        )
        self._connection.commit()
        return token

    def supplier_for_token(self, token: str) -> dict | None:
        if not token:
            return None
        keys = list(session_keys(token))
        marks = ",".join("?" * len(keys))
        row = self._connection.execute(
            f"select s.token as session_token, s.created_at as session_created, p.* from supplier_sessions s"
            f" join suppliers p on p.id = s.supplier_id where s.token in ({marks})",
            keys,
        ).fetchone()
        if row is None:
            return None
        if row["session_created"] < session_cutoff().isoformat():
            self._connection.execute("delete from supplier_sessions where token = ?", (row["session_token"],))
            self._connection.commit()
            return None
        return self._supplier_row(row)

    def logout_supplier(self, token: str) -> None:
        keys = list(session_keys(token))
        marks = ",".join("?" * len(keys))
        self._connection.execute(f"delete from supplier_sessions where token in ({marks})", keys)
        self._connection.commit()

    def claim_supplier_link(self, supplier_id: str, reply_token: str) -> dict | None:
        bound = self.seller_id_for_reply_token(reply_token)
        if bound is None:
            return None
        taken = self._connection.execute("select id from suppliers where haraj_seller_id = ?", (bound,)).fetchone()
        if taken is not None and taken["id"] != supplier_id:
            raise ValueError("seller already registered")
        self._connection.execute(
            "update suppliers set haraj_seller_id = ?, status = 'active', updated_at = ? where id = ?",
            (bound, _now(), supplier_id),
        )
        self._connection.commit()
        row = self._connection.execute("select * from suppliers where id = ?", (supplier_id,)).fetchone()
        return None if row is None else self._supplier_row(row)

    def supplier_requests(self, supplier_id: str) -> list[dict]:
        supplier = self._connection.execute("select haraj_seller_id, status from suppliers where id = ?", (supplier_id,)).fetchone()
        if supplier is None or supplier["status"] != "active" or not supplier["haraj_seller_id"]:
            return []
        seller = supplier["haraj_seller_id"]
        rows = self._connection.execute(
            """
            select r.id, r.ref_code, r.city, r.created_at, r.awarded_seller_id, r.contact_shared_at,
                   p.need, p.reply_token, p.seller_id,
                   (select min(o.total_price) from offers o where o.request_id = r.id and o.seller_id = p.seller_id) as my_offer,
                   (select count(*) from messages m where m.request_id = r.id and m.sender_role = 'seller' and m.seller_id = p.seller_id) as my_messages
              from request_recipients p join requests r on r.id = p.request_id
             where p.seller_id = ? or p.seller_id = ?
             order by r.created_at desc limit 100
            """,
            (seller, f"haraj:seller:{seller}"),
        ).fetchall()
        out = []
        for row in rows:
            awarded = row["awarded_seller_id"]
            mine = awarded is not None and seller_key(awarded) == seller
            state = ("awarded" if mine else "lost") if awarded else ("quoted" if row["my_offer"] is not None else "new")
            out.append({
                "request_id": row["id"],
                "ref_code": row["ref_code"],
                "need": row["need"],
                "city": row["city"],
                "created_at": row["created_at"],
                "state": state,
                "offer": None if row["my_offer"] is None else float(row["my_offer"]),
                "messages": int(row["my_messages"] or 0),
                "token": row["reply_token"],
                "contact_shared": bool(row["contact_shared_at"]) and mine,
            })
        return out

    # -- contact sharing after an award ---------------------------------------

    def share_contact(self, request_id: str, owner_user_id: str, *, phone: str,
                      lat: float | None = None, lng: float | None = None) -> dict:
        row = self._connection.execute(
            "select awarded_seller_id from requests where id = ? and owner_user_id = ?", (request_id, owner_user_id)
        ).fetchone()
        if row is None:
            raise LookupError("request not found")
        if not row["awarded_seller_id"]:
            raise ValueError("award a supplier first")
        self._connection.execute(
            "update requests set contact_phone = ?, contact_lat = ?, contact_lng = ?, contact_shared_at = ? where id = ?",
            (phone.strip(), lat, lng, _now(), request_id),
        )
        self._connection.commit()
        return {"shared": True, "phone": phone.strip(), "lat": lat, "lng": lng}

    def revoke_contact(self, request_id: str, owner_user_id: str) -> dict:
        cursor = self._connection.execute(
            "update requests set contact_phone = null, contact_lat = null, contact_lng = null, contact_shared_at = null"
            " where id = ? and owner_user_id = ?",
            (request_id, owner_user_id),
        )
        if cursor.rowcount == 0:
            raise LookupError("request not found")
        self._connection.commit()
        return {"shared": False}

    def shared_contact(self, request_id: str, seller_id: str) -> dict | None:
        row = self._connection.execute(
            "select awarded_seller_id, contact_phone, contact_lat, contact_lng, contact_shared_at from requests where id = ?",
            (request_id,),
        ).fetchone()
        if row is None or not row["contact_shared_at"] or not row["awarded_seller_id"]:
            return None
        if seller_key(row["awarded_seller_id"]) != seller_key(seller_id):
            return None
        return {"phone": row["contact_phone"], "lat": row["contact_lat"], "lng": row["contact_lng"]}

    def route_customer_message(
        self,
        request_id: str,
        owner_user_id: str,
        body: str,
        need: str | None = None,
        seller_id: str | None = None,
        reply_to: str | None = None,
        seller_ids=None,
        media_ids=None,
    ) -> Message:
        """Everyone on the item by default; the suppliers picked; or one when replying to him."""
        if self._connection.execute("select 1 from requests where id = ? and owner_user_id = ?", (request_id, owner_user_id)).fetchone() is None:
            raise LookupError("request not found")
        media = []
        for file_id in media_ids or []:
            row = self._connection.execute(
                "select id, content_type, filename, size_bytes, width, height from files where id = ? and owner_user_id = ? and request_id = ?",
                (file_id, owner_user_id, request_id),
            ).fetchone()
            if row is None:
                raise ValueError("unknown file")
            media.append(media_entry(row))
        if not body.strip() and not media:
            raise ValueError("message is required")
        if reply_to:
            quoted = self._connection.execute("select * from messages where id = ? and request_id = ?", (reply_to, request_id)).fetchone()
            if quoted is None:
                raise ValueError("unknown reply_to")
            delivered = [item["seller_id"] for item in self._connection.execute("select seller_id from message_deliveries where message_id = ? order by created_at", (reply_to,))]
            seller_id, seller_ids = reply_audience(quoted["seller_id"], delivered, seller_id, seller_ids)
            need = need or quoted["need"]
        return self._enqueue(request_id, body, need, seller_id, reply_to, owner_user_id, seller_ids=seller_ids, media=media)

    def save_file(self, owner_user_id: str, request_id: str, content_type: str, filename: str, data: bytes, width: int | None = None, height: int | None = None) -> dict:
        if self._connection.execute("select 1 from requests where id = ? and owner_user_id = ?", (request_id, owner_user_id)).fetchone() is None:
            raise LookupError("request not found")
        file_id = uuid4().hex
        self._connection.execute(
            "insert into files (id, owner_user_id, request_id, content_type, filename, size_bytes, width, height, data, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (file_id, owner_user_id, request_id, content_type, Path(filename).name or "file", len(data), width, height, data, _now()),
        )
        self._connection.commit()
        return media_entry({"id": file_id, "content_type": content_type, "filename": Path(filename).name or "file", "size_bytes": len(data), "width": width, "height": height})

    def get_file(self, file_id: str) -> dict | None:
        row = self._connection.execute("select * from files where id = ?", (file_id,)).fetchone()
        return None if row is None else {**dict(row), "data": bytes(row["data"])}

    def claim_deliveries(self, limit: int = 50) -> list[dict]:
        rows = self._connection.execute(
            """
            select d.id, d.request_id, d.seller_id, d.need, coalesce(m.haraj_text, m.body) as body, m.media_json as media, t.ad_id, t.haraj_conversation_id, q.ref_code
            from message_deliveries d
            join messages m on m.id = d.message_id
            join haraj_threads t on t.request_id = d.request_id and t.seller_id = d.seller_id and t.need = d.need
            join requests q on q.id = d.request_id
            where d.delivery_status = 'queued'
            order by d.created_at
            limit ?
            """,
            (limit,),
        ).fetchall()
        claimed = []
        for row in rows:
            self._connection.execute("update message_deliveries set delivery_status = 'sending', attempts = attempts + 1, last_attempt_at = ? where id = ?", (_now(), row["id"]))
            # Looked up per row: SQLite 3.45 cannot resolve d.need inside a correlated subquery's order by.
            token = self._connection.execute(
                "select reply_token from request_recipients where request_id = ? and seller_id = ? order by coalesce(need, '') = ? desc limit 1",
                (row["request_id"], row["seller_id"], row["need"]),
            ).fetchone()
            claimed.append({**dict(row), "reply_token": token["reply_token"] if token else None, "media": json.loads(row["media"]) if row["media"] else []})
        self._connection.commit()
        return claimed

    def finish_delivery(self, delivery_id: str, sent: SentMessage | None = None, error: str | None = None, retry: bool = False) -> None:
        row = self._connection.execute("select * from message_deliveries where id = ?", (delivery_id,)).fetchone()
        if row is None:
            return
        key = (row["request_id"], row["seller_id"], row["need"])
        if sent is not None:
            self._connection.execute(
                "update message_deliveries set delivery_status = 'sent', haraj_message_id = ?, sent_at = ?, error = null where id = ?",
                (sent.haraj_message_id, _now(), delivery_id),
            )
            # Replies are read from our first message on: older history in the conversation is not imported.
            self._connection.execute(
                "update haraj_threads set haraj_conversation_id = ?, high_water = coalesce(high_water, ?) where request_id = ? and seller_id = ? and need = ?",
                (sent.haraj_conversation_id, sent.seq, *key),
            )
            status = "sent"
        else:
            status = "queued" if retry else "failed"
            self._connection.execute("update message_deliveries set delivery_status = ?, error = ? where id = ?", (status, error, delivery_id))
        if status != "queued":
            self._connection.execute(
                "update request_recipients set send_status = ? where request_id = ? and seller_id = ? and coalesce(need, '') in (?, '') and coalesce(send_status, '') != 'sent'",
                (status, *key),
            )
        self._connection.commit()

    def delivery_attempts(self, delivery_id: str) -> int:
        row = self._connection.execute("select attempts from message_deliveries where id = ?", (delivery_id,)).fetchone()
        return 0 if row is None else row["attempts"]

    # --- Sync: Haraj -> item conversation ---------------------------------------------------------

    def threads_to_sync(self, limit: int = 200, now: float | None = None) -> list[dict]:
        stamp = datetime.fromtimestamp(now, tz=timezone.utc).isoformat() if now is not None else _now()
        rows = self._connection.execute(
            "select * from haraj_threads where haraj_conversation_id is not null and (retry_at is null or retry_at <= ?) order by checked_at is not null, checked_at limit ?",
            (stamp, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def thread_checked(self, thread: dict, failure_code: str | None = None, retry_seconds: int = 30, now: float | None = None) -> None:
        now = datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else datetime.now(timezone.utc)
        self._connection.execute(
            "update haraj_threads set checked_at = ?, retry_at = ?, failure_code = ? where request_id = ? and seller_id = ? and need = ?",
            (now.isoformat(), (now + timedelta(seconds=retry_seconds)).isoformat(), failure_code, thread["request_id"], thread["seller_id"], thread["need"]),
        )
        self._connection.commit()

    def _inbound_candidates(self, conversation_id: str) -> list[dict]:
        rows = self._connection.execute(
            "select t.*, r.owner_user_id, r.ref_code, r.awarded_seller_id, r.created_at as request_created_at"
            " from haraj_threads t join requests r on r.id = t.request_id where t.haraj_conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        candidates = []
        for row in rows:
            sends = self._connection.execute(
                "select sent_at from message_deliveries where request_id = ? and seller_id = ? and need = ? and delivery_status = 'sent' and sent_at is not null",
                (row["request_id"], row["seller_id"], row["need"]),
            ).fetchall()
            candidates.append({**dict(row), "sends": [item["sent_at"] for item in sends]})
        return candidates

    def thread_for_inbound(self, conversation_id: str, sent_at: str, body: str = "") -> dict | None:
        """The request a seller's reply belongs to (see choose_thread); None when it cannot be told apart."""
        return choose_thread(self._inbound_candidates(conversation_id), body, sent_at)

    def record_unmatched_inbound(self, conversation_id: str, seller_id: str, inbound: InboundMessage) -> list[str]:
        """Keep a reply no request can safely claim, out of every customer's view. Returns the requests it could belong to."""
        candidates = sorted({row["request_id"] for row in self._inbound_candidates(conversation_id)})
        self._connection.execute(
            "insert or ignore into haraj_unmatched (haraj_message_id, haraj_conversation_id, seller_id, body, media_json, sent_at, candidate_request_ids_json, created_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                inbound.haraj_message_id,
                conversation_id,
                seller_id,
                inbound.body,
                json.dumps(list(inbound.media), ensure_ascii=False) if inbound.media else None,
                inbound.sent_at,
                json.dumps(candidates),
                _now(),
            ),
        )
        self._connection.commit()
        return candidates

    def unmatched_inbound(self, conversation_id: str | None = None) -> list[dict]:
        rows = self._connection.execute(
            "select * from haraj_unmatched where ? is null or haraj_conversation_id = ? order by sent_at", (conversation_id, conversation_id)
        ).fetchall()
        return [
            {**dict(row), "candidate_request_ids": json.loads(row["candidate_request_ids_json"]), "media": json.loads(row["media_json"] or "[]")}
            for row in rows
        ]

    def unfiled_for_seller(self, seller_id: str | None, request_id: str) -> list[dict]:
        """His own Haraj replies that no request could safely claim and that this request may own."""
        return unfiled_for(self.unmatched_inbound(), seller_id, request_id)

    def file_unmatched(self, haraj_message_id: str, request_id: str, media=None) -> tuple[dict, Message | None]:
        """Move a kept reply into one of the requests it could belong to. Raises LookupError otherwise."""
        row = next((item for item in self.unmatched_inbound() if item["haraj_message_id"] == haraj_message_id), None)
        if row is None or request_id not in row["candidate_request_ids"]:
            raise LookupError("unfiled reply not found")
        threads = [item for item in self._inbound_candidates(row["haraj_conversation_id"]) if item["request_id"] == request_id]
        if not threads:
            raise LookupError("unfiled reply not found")
        thread, inbound = filed_inbound(row, threads, media)
        message = self.record_inbound(thread, inbound)
        self._connection.execute("delete from haraj_unmatched where haraj_message_id = ?", (haraj_message_id,))
        self._connection.commit()
        return thread, message

    # --- Limits: who may be asked, and how often -------------------------------------------------

    def record_search_sellers(self, trace_id: str, user_id: str | None, seller_ids) -> None:
        created = _now()
        with self._db_lock:
            for seller_id in seller_ids:
                self._connection.execute(
                    "insert into search_sellers (trace_id, user_id, seller_id, created_at) values (?, ?, ?, ?)"
                    " on conflict (trace_id, seller_id) do update set user_id = coalesce(search_sellers.user_id, excluded.user_id)",
                    (trace_id, user_id, seller_id, created),
                )
            self._connection.commit()

    def searched_sellers(self, user_id: str, trace_id: str | None, since: str) -> set[str]:
        """Sellers this user's searches showed since then, and those of an anonymous search he names."""
        rows = self._connection.execute(
            "select seller_id from search_sellers where (user_id = ? and julianday(created_at) >= julianday(?))"
            " or (trace_id = ? and (user_id is null or user_id = ?))",
            (user_id, since, trace_id, user_id),
        ).fetchall()
        return {row["seller_id"] for row in rows}

    def count_requests(self, user_id: str, since: str | None = None) -> int:
        row = self._connection.execute(
            "select count(*) as count from requests where owner_user_id = ? and (? is null or julianday(created_at) >= julianday(?))",
            (user_id, since, since),
        ).fetchone()
        return row["count"]

    def trial_since(self, user_id: str) -> str | None:
        """See PgStore.trial_since."""
        row = self._connection.execute("select trial_reset_at from users where id = ?", (user_id,)).fetchone()
        return (row["trial_reset_at"] if row else None) or None

    def account_unlimited(self, user_id: str) -> bool:
        row = self._connection.execute("select unlimited from users where id = ?", (user_id,)).fetchone()
        return bool(row and row["unlimited"])

    def reset_trial(self, user_id: str, at: str | None = None) -> None:
        self._connection.execute("update users set trial_reset_at = ? where id = ?", (at or _now(), user_id))
        self._connection.commit()

    def set_unlimited(self, user_id: str, value: bool = True) -> None:
        self._connection.execute("update users set unlimited = ? where id = ?", (1 if value else 0, user_id))
        self._connection.commit()

    def farq_user_id(self, user_id: str) -> str | None:
        """The Farq user id this Taseer account is tied to, or None while unlinked.
        Credits live ONLY in Farq's central billing ledger, keyed by this id."""
        row = self._connection.execute("select farq_user_id from users where id = ?", (user_id,)).fetchone()
        return (row["farq_user_id"] if row else None) or None

    def count_items(self, user_id: str, since: str | None = None) -> int:
        """Items, not requests - see PgStore.count_items."""
        row = self._connection.execute(
            "select count(*) as count from ("
            " select distinct rr.request_id, coalesce(rr.need, '') from request_recipients rr"
            " join requests r on r.id = rr.request_id"
            " where r.owner_user_id = ? and (? is null or julianday(r.created_at) >= julianday(?))"
            ")",
            (user_id, since, since),
        ).fetchone()
        return row["count"]

    def count_contacts(self, user_id: str, since: str | None = None) -> int:
        row = self._connection.execute(
            "select count(*) as count from request_recipients rr join requests r on r.id = rr.request_id"
            " where r.owner_user_id = ? and (? is null or julianday(r.created_at) >= julianday(?))",
            (user_id, since, since),
        ).fetchone()
        return row["count"]

    def count_customer_messages(self, user_id: str, since: str) -> int:
        row = self._connection.execute(
            "select count(*) as count from messages m join requests r on r.id = m.request_id"
            " where r.owner_user_id = ? and m.sender_role = 'user' and julianday(m.created_at) >= julianday(?)",
            (user_id, since),
        ).fetchone()
        return row["count"]

    def conversation_checked(self, conversation_id: str, failure_code: str | None = None, retry_seconds: int = 30, now: float | None = None, high_water: int | None = None) -> None:
        moment = datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else datetime.now(timezone.utc)
        self._connection.execute(
            "update haraj_threads set checked_at = ?, retry_at = ?, failure_code = ?, high_water = max(coalesce(high_water, 0), ?) where haraj_conversation_id = ?",
            (moment.isoformat(), (moment + timedelta(seconds=retry_seconds)).isoformat(), failure_code, high_water or 0, conversation_id),
        )
        self._connection.commit()

    def has_haraj_message(self, haraj_message_id: str) -> bool:
        return (
            self._connection.execute(
                "select 1 from messages where haraj_message_id = ? union all select 1 from haraj_unmatched where haraj_message_id = ?",
                (haraj_message_id, haraj_message_id),
            ).fetchone()
            is not None
        )

    def save_file_for_request(self, request_id: str, content_type: str, filename: str, data: bytes, width: int | None = None, height: int | None = None) -> dict:
        owner = self._connection.execute("select owner_user_id from requests where id = ?", (request_id,)).fetchone()
        return self.save_file(owner["owner_user_id"], request_id, content_type, filename, data, width, height)

    # --- Channel state shared by every instance: session tokens, pauses, pacing ------------------

    def reserve_send_slot(self, spacing: float, now: float, deadline: float) -> float | None:
        """Book the next send time for every instance at once: max(next_send_at, now), if it fits the deadline."""
        row = self._connection.execute("select value from haraj_channel where key = 'next_send_at'").fetchone()
        slot = max(float(row["value"]) if row and row["value"] else 0.0, now)
        if slot > deadline:
            return None
        self.set_value("next_send_at", repr(slot + spacing))
        return slot

    def release_send_slot(self, slot: float, spacing: float) -> None:
        """Give back a booked slot nobody used, unless someone booked after it."""
        self._connection.execute(
            "update haraj_channel set value = ? where key = 'next_send_at' and value = ?", (repr(slot), repr(slot + spacing))
        )
        self._connection.commit()

    def get_value(self, key: str) -> str | None:
        row = self._connection.execute("select value from haraj_channel where key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_value(self, key: str, value: str | None) -> None:
        self._connection.execute(
            "insert into haraj_channel (key, value, updated_at) values (?, ?, ?) on conflict (key) do update set value = excluded.value, updated_at = excluded.updated_at",
            (key, value, _now()),
        )
        self._connection.commit()

    def record_inbound(self, thread: dict, inbound: InboundMessage) -> Message | None:
        """Attach a Haraj reply to its item and seller. Returns None when it was already recorded."""
        if self._connection.execute("select 1 from messages where haraj_message_id = ?", (inbound.haraj_message_id,)).fetchone():
            self._connection.execute(
                "update haraj_threads set high_water = max(coalesce(high_water, 0), ?) where request_id = ? and seller_id = ? and need = ?",
                (inbound.seq, thread["request_id"], thread["seller_id"], thread["need"]),
            )
            self._connection.commit()
            return None
        request_id, seller_id, need = thread["request_id"], thread["seller_id"], thread["need"] or None
        recipient = self._connection.execute(
            "select * from request_recipients where request_id = ? and seller_id = ? order by coalesce(need, '') = ? desc",
            (request_id, seller_id, need or ""),
        ).fetchone()
        offer = None
        quote = extract_quote(inbound.body)
        price = None if quote is None else quote.total
        awarded = self._connection.execute("select awarded_seller_id from requests where id = ?", (request_id,)).fetchone()["awarded_seller_id"]
        # After the award, a price from anyone but the winner is kept as a message, not an offer.
        if price is not None and awarded not in (None, seller_id):
            quote, price = None, None
        if price is not None:
            offer = Offer(
                amount=price,
                currency="SAR",
                note=inbound.body,
                provider_name=recipient["seller_name"] if recipient else None,
                seller_id=seller_id,
                base_price=quote.base,
                delivery_included=quote.delivery_included,
                delivery_price=quote.delivery_price,
                total_price=price,
                need=need,
            )
            # Latest price from a seller on an item replaces the earlier one.
            self._connection.execute("delete from offers where request_id = ? and seller_id = ? and coalesce(need, '') = ?", (request_id, seller_id, need or ""))
            self._connection.execute(
                "insert into offers (id, request_id, seller_id, need, provider_name, base_price, delivery_included, delivery_price, total_price, currency, message, created_at)"
                " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    request_id,
                    seller_id,
                    need,
                    offer.provider_name,
                    quote.base,
                    None if quote.delivery_included is None else int(quote.delivery_included),
                    quote.delivery_price,
                    price,
                    "SAR",
                    inbound.body,
                    _now(),
                ),
            )
        message = self.add_message(
            request_id,
            "seller",
            None,
            inbound.body,
            offer,
            seller_id=seller_id,
            need=need,
            scope=SINGLE_SELLER,
            created_at=inbound.sent_at,
            haraj_conversation_id=thread["haraj_conversation_id"],
            haraj_message_id=inbound.haraj_message_id,
        )
        if inbound.media:
            self._connection.execute("update messages set media_json = ? where id = ?", (json.dumps(list(inbound.media), ensure_ascii=False), message.id))
            message.media = list(inbound.media)
        owner = self._connection.execute("select owner_user_id from requests where id = ?", (request_id,)).fetchone()
        self._connection.execute(
            "insert into notifications (id, user_id, request_id, kind, created_at) values (?, ?, ?, ?, ?)",
            (uuid4().hex, owner["owner_user_id"], request_id, "seller_reply", message.created_at),
        )
        self._connection.execute(
            "update haraj_threads set last_fetched_at = ?, high_water = max(coalesce(high_water, 0), ?) where request_id = ? and seller_id = ? and need = ?",
            (_now(), inbound.seq, request_id, seller_id, thread["need"]),
        )
        self._connection.commit()
        return message

    def list_requests(self, owner_user_id: str) -> list[dict]:
        """See PgStore.list_requests: a handful of queries for the whole list."""
        rows = self._connection.execute("select * from requests where owner_user_id = ? order by created_at desc", (owner_user_id,)).fetchall()
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        marks = ", ".join("?" for _ in ids)
        by_request: dict[str, dict[str, list]] = {rid: {"recipients": [], "offers": [], "messages": []} for rid in ids}
        for item in self._connection.execute(f"select * from request_recipients where request_id in ({marks})", ids):
            by_request[item["request_id"]]["recipients"].append(item)
        for item in self._connection.execute(f"select * from offers where request_id in ({marks}) order by created_at desc", ids):
            by_request[item["request_id"]]["offers"].append(item)
        for item in self._connection.execute(
            f"select request_id, sender_role, seller_id, body, offer_amount, offer_currency, created_at from messages where request_id in ({marks}) order by created_at", ids
        ):
            by_request[item["request_id"]]["messages"].append(item)
        queued_at = [
            item["created_at"]
            for item in self._connection.execute("select r.created_at from request_recipients rr join requests r on r.id = rr.request_id where rr.send_status = 'queued'")
        ]
        items = []
        for row in rows:
            parts = by_request[row["id"]]
            summary = request_summary(row, [self._recipient_from_row(item) for item in parts["recipients"]], self._offers_from_rows(parts["offers"]), parts["messages"])
            if summary.get("queued_count"):
                summary["queue_ahead"] = sum(1 for moment in queued_at if moment < row["created_at"])
            items.append(summary)
        return items

    def _request_by_token(self, token: str):
        row = self._connection.execute("select * from requests where reply_token = ?", (token,)).fetchone()
        if row is not None:
            return row
        recipient = self._connection.execute("select request_id from request_recipients where reply_token = ?", (token,)).fetchone()
        if recipient is None:
            return None
        return self._connection.execute("select * from requests where id = ?", (recipient["request_id"],)).fetchone()

    def _recipient_by_token(self, token: str):
        return self._connection.execute("select * from request_recipients where reply_token = ?", (token,)).fetchone()

    def _col(self, row, name, default=None):
        return row[name] if name in row.keys() and row[name] is not None else default

    def _recipient_from_row(self, item) -> RequestRecipient:
        return RequestRecipient(
            seller_id=item["seller_id"],
            seller_name=item["seller_name"],
            ad_id=item["ad_id"],
            need=self._col(item, "need"),
            reply_token=self._col(item, "reply_token"),
            send_status=self._col(item, "send_status", "sent"),
            listing_url=self._col(item, "listing_url"),
        )

    def _offers_for_request(self, request_id: str) -> list[Offer]:
        return self._offers_from_rows(self._connection.execute("select * from offers where request_id = ? order by created_at desc", (request_id,)).fetchall())

    def _offers_from_rows(self, rows) -> list[Offer]:
        offers: list[Offer] = []
        for item in current_offer_rows(rows):
            offer = Offer(
                amount=item["total_price"],
                currency=item["currency"] or "SAR",
                note=item["message"],
                provider_name=item["provider_name"],
                phone=item["phone"],
                seller_id=item["seller_id"],
                base_price=item["base_price"],
                delivery_included=bool(item["delivery_included"]) if item["delivery_included"] is not None else None,
                delivery_price=item["delivery_price"],
                total_price=item["total_price"],
                need=item["need"],
            )
            offers.append(offer)
        return mark_cheapest(offers)

    def seller_view(self, token: str) -> dict | None:
        row = self._request_by_token(token)
        if row is None:
            return None
        recipient_row = self._recipient_by_token(token)
        if recipient_row is not None:
            recipients = [self._recipient_from_row(recipient_row)]
            need = recipient_row["need"] or row["need"]
        else:
            recipients = [self._recipient_from_row(item) for item in self._connection.execute("select * from request_recipients where request_id = ?", (row["id"],))]
            need = row["need"]
        attachments = [
            {"id": item["id"], "filename": item["filename"], "content_type": item["content_type"], "size_bytes": item["size_bytes"]}
            for item in self._connection.execute("select * from attachments where request_id = ?", (row["id"],))
        ]
        own = recipient_row["seller_id"] if recipient_row is not None else None
        sent = {
            item["id"]: item["haraj_text"]
            for item in self._connection.execute("select id, haraj_text from messages where request_id = ? and haraj_text is not null", (row["id"],))
        }
        awarded = self._col(row, "awarded_seller_id")
        awarded_to_me = awarded is not None and own is not None and seller_key(awarded) == seller_key(own)
        # His own current price with its delivery terms, so his page can say what the customer
        # reads. Only his rows, and no `cheapest` mark: whether he undercut anyone stays private.
        mine = [] if own is None else [
            {
                "need": item["need"],
                "base_price": item["base_price"],
                "delivery_included": bool(item["delivery_included"]) if item["delivery_included"] is not None else None,
                "delivery_price": item["delivery_price"],
                "total_price": item["total_price"],
                "currency": item["currency"] or "SAR",
                "created_at": item["created_at"],
            }
            for item in current_offer_rows(
                self._connection.execute(
                    "select * from offers where request_id = ? and seller_id = ? order by created_at desc", (row["id"], own)
                ).fetchall()
            )
        ]
        # The invite promises the item and the city only: the customer's own words and notes stay with him.
        # The phone and the place are here only when this supplier won AND the customer chose to share them.
        return {
            "request_id": row["id"],
            "ref_code": self._col(row, "ref_code"),
            "need": need,
            "city": row["city"],
            "awarded_to_me": awarded_to_me,
            "contact": self.shared_contact(row["id"], own) if awarded_to_me else None,
            "offers_open": awarded in (None, own),
            "offers": mine,
            "recipients": [item.model_dump(mode="json") for item in recipients],
            "attachments": attachments,
            # Each seller sees their own thread, never another seller's messages or who else was asked.
            "messages": [
                seller_message(item, own, sent.get(item.id))
                for item in self._messages_for_request(row["id"])
                if visible_to_seller(item, own, need)
            ],
        }

    def add_seller_reply(self, token: str, seller_id: str | None, body: str, offer: Offer | None) -> Message:
        row = self._request_by_token(token)
        if row is None:
            raise ValueError("request not found")
        recipient_row = self._recipient_by_token(token)
        recipients = self._connection.execute(
            "select * from request_recipients where request_id = ?",
            (row["id"],),
        ).fetchall()
        ids = [item["seller_id"] for item in recipients]
        if recipient_row is not None:
            seller_id = recipient_row["seller_id"]
        if seller_id is None:
            if len(ids) != 1:
                raise ValueError("seller_id required")
            seller_id = ids[0]
        if seller_id not in ids:
            raise ValueError("unknown seller")
        matched = next(item for item in recipients if item["seller_id"] == seller_id)
        awarded = self._col(row, "awarded_seller_id")
        if offer is not None and awarded is not None and awarded != seller_id:
            raise ValueError("offers are closed: the customer has already chosen a supplier")
        if offer is not None:
            offer = priced_offer(offer, body, matched["seller_name"], self._col(matched, "need") or row["need"])
            # One current offer per supplier per item: a revised price replaces the earlier one.
            self._connection.execute(
                "delete from offers where request_id = ? and seller_id = ? and coalesce(need, '') = ?", (row["id"], seller_id, offer.need or "")
            )
            self._connection.execute(
                "insert into offers (id, request_id, seller_id, need, provider_name, phone, base_price, delivery_included, delivery_price, total_price, currency, message, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    row["id"],
                    seller_id,
                    offer.need,
                    offer.provider_name,
                    offer.phone,
                    offer.base_price,
                    None if offer.delivery_included is None else int(offer.delivery_included),
                    offer.delivery_price,
                    offer.total_price,
                    offer.currency,
                    offer.note,
                    _now(),
                ),
            )
        text = body.strip() or ("عرض سعر" if offer is None else f"الإجمالي: {offer.total_price:g} ر.س")
        need = (offer.need if offer is not None else None) or self._col(matched, "need") or row["need"]
        message = self.add_message(row["id"], "seller", None, text, offer, seller_id=seller_id, need=need)
        self._connection.execute(
            "insert into notifications (id, user_id, request_id, kind, created_at) values (?, ?, ?, ?, ?)",
            (uuid4().hex, row["owner_user_id"], row["id"], "seller_reply", message.created_at),
        )
        self._connection.commit()
        return message

    AWARD_TEXT = "تم اختيار عرضك. سنتواصل معك لإكمال التفاصيل."

    def award(self, request_id: str, owner_user_id: str, seller_id: str, notify: bool = True) -> Message | None:
        """The customer picks the winning offer. The supplier hears it from us only if the customer says so."""
        if self._connection.execute(
            "select 1 from request_recipients r join requests q on q.id = r.request_id where r.request_id = ? and r.seller_id = ? and q.owner_user_id = ?",
            (request_id, seller_id, owner_user_id),
        ).fetchone() is None:
            raise ValueError("unknown seller")
        current = self._connection.execute("select awarded_seller_id from requests where id = ?", (request_id,)).fetchone()["awarded_seller_id"]
        if current == seller_id:
            return None
        # Only the first award counts; a second one for another supplier is refused, not swapped in.
        changed = self._connection.execute(
            "update requests set awarded_seller_id = ?, awarded_at = ? where id = ? and owner_user_id = ? and awarded_seller_id is null",
            (seller_id, _now(), request_id, owner_user_id),
        ).rowcount
        self._connection.commit()
        if not changed:
            raise AwardConflict("request already awarded to another supplier")
        if not notify:
            return None
        return self.route_customer_message(request_id, owner_user_id, self.AWARD_TEXT, seller_id=seller_id)

    def mark_read(self, request_id: str, owner_user_id: str) -> None:
        self._connection.execute("update requests set customer_read_at = ? where id = ? and owner_user_id = ?", (_now(), request_id, owner_user_id))
        self._connection.commit()

    def recipient_name(self, request_id: str, seller_id: str | None) -> str | None:
        row = self._connection.execute("select seller_name from request_recipients where request_id = ? and seller_id = ?", (request_id, seller_id)).fetchone()
        return None if row is None else row["seller_name"]

    def add_push_subscription(self, user_id: str, endpoint: str, p256dh: str, auth: str) -> bool:
        """A device endpoint stays with the account that registered it. Returns False, and
        changes nothing, when another account already holds it."""
        cur = self._connection.execute(
            "insert into push_subscriptions (endpoint, user_id, p256dh, auth, created_at) values (?, ?, ?, ?, ?)"
            " on conflict (endpoint) do update set p256dh = excluded.p256dh, auth = excluded.auth"
            " where push_subscriptions.user_id = excluded.user_id",
            (endpoint, user_id, p256dh, auth, _now()),
        )
        self._connection.commit()
        return cur.rowcount > 0

    def remove_push_subscription(self, endpoint: str) -> None:
        self._connection.execute("delete from push_subscriptions where endpoint = ?", (endpoint,))
        self._connection.commit()

    def farq_user_for_request(self, request_id: str) -> str | None:
        """The Farq account that owns this request, or None while its owner is unlinked."""
        row = self._connection.execute(
            "select u.farq_user_id from requests r join users u on u.id = r.owner_user_id where r.id = ?", (request_id,)
        ).fetchone()
        return (row["farq_user_id"] if row else None) or None

    def push_subscriptions_for_request(self, request_id: str) -> list[dict]:
        rows = self._connection.execute(
            "select s.* from push_subscriptions s join requests r on r.owner_user_id = s.user_id where r.id = ?", (request_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_synced(self, request_id: str | None = None) -> None:
        stamp = _now()
        if request_id:
            self._connection.execute("update requests set last_synced_at = ? where id = ?", (stamp, request_id))
        else:
            self._connection.execute("update requests set last_synced_at = ?", (stamp,))
        self._connection.commit()

    def active_request_ids(self) -> list[str]:
        rows = self._connection.execute("select id from requests order by created_at desc").fetchall()
        return [item["id"] for item in rows]

    def attachment_path(self, request_id: str, attachment_id: str, owner_user_id: str | None = None, reply_token: str | None = None) -> tuple[Path, str, str] | None:
        if owner_user_id is not None:
            owner = self._connection.execute(
                "select id from requests where id = ? and owner_user_id = ?",
                (request_id, owner_user_id),
            ).fetchone()
            if owner is None:
                return None
        elif reply_token is not None:
            owner = self._connection.execute(
                "select id from requests where id = ? and reply_token = ?",
                (request_id, reply_token),
            ).fetchone()
            if owner is None:
                linked = self._connection.execute(
                    "select request_id from request_recipients where request_id = ? and reply_token = ?",
                    (request_id, reply_token),
                ).fetchone()
                if linked is None:
                    return None
        else:
            return None
        row = self._connection.execute(
            "select * from attachments where id = ? and request_id = ?",
            (attachment_id, request_id),
        ).fetchone()
        if row is None:
            return None
        return Path(row["path"]), row["content_type"], row["filename"]

    # -- Subscriptions & payments -------------------------------------------------

    def list_active_plans(self) -> list[dict]:
        rows = self._connection.execute(
            "select * from subscription_plans where is_active = 1 order by price_amount asc"
        ).fetchall()
        return [self._plan_row(row) for row in rows]

    def get_plan(self, code: str) -> dict | None:
        row = self._connection.execute("select * from subscription_plans where code = ?", (code,)).fetchone()
        return None if row is None else self._plan_row(row)

    def _plan_row(self, row) -> dict:
        return {
            "code": row["code"],
            "name_ar": row["name_ar"],
            "name_en": row["name_en"],
            "description_ar": row["description_ar"],
            "price_amount": row["price_amount"],
            "currency": row["currency"],
            "duration_days": row["duration_days"],
            "features": json.loads(row["features_json"]),
            "is_active": bool(row["is_active"]),
            "is_placeholder_price": bool(row["is_placeholder_price"]),
            "monthly_items": row["monthly_items"],
            "sellers_per_item": row["sellers_per_item"],
            "daily_contacts": row["daily_contacts"],
        }

    def _subscription_row(self, row) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "plan": row["plan"],
            "status": row["status"],
            "starts_at": row["starts_at"],
            "expires_at": row["expires_at"],
            "period_anchor": row["period_anchor"] or row["starts_at"] or row["created_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_latest_subscription(self, user_id: str) -> dict | None:
        row = self._connection.execute(
            "select * from subscriptions where user_id = ? order by created_at desc limit 1",
            (user_id,),
        ).fetchone()
        return None if row is None else self._subscription_row(row)

    def subscription_status(self, user_id: str) -> str:
        """The user's current entitlement state, computed server-side."""
        sub = self.get_latest_subscription(user_id)
        if sub is None:
            return "none"
        if sub["status"] == "active":
            if sub["expires_at"] and sub["expires_at"] < _now():
                return "expired"
            return "active"
        return sub["status"]

    def is_subscribed(self, user_id: str) -> bool:
        return self.subscription_status(user_id) == "active"

    def create_pending_payment(self, user_id: str, plan_code: str, amount: int, currency: str, source_type: str | None = None) -> dict:
        plan = self.get_plan(plan_code)
        if plan is None or not plan["is_active"]:
            raise ValueError("unknown or inactive plan")
        payment_id = uuid4().hex
        created = _now()
        self._connection.execute(
            "insert into payments (id, user_id, provider, provider_payment_id, amount, currency, status, subscription_id, plan, source_type, created_at)"
            " values (?, ?, 'moyasar', null, ?, ?, 'payment_pending', null, ?, ?, ?)",
            (payment_id, user_id, amount, currency, plan_code, source_type, created),
        )
        self._connection.commit()
        return self.get_payment(payment_id)

    def _payment_row(self, row) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "provider": row["provider"],
            "provider_payment_id": row["provider_payment_id"],
            "amount": row["amount"],
            "currency": row["currency"],
            "status": row["status"],
            "subscription_id": row["subscription_id"],
            "plan": row["plan"],
            "provider_status": row["provider_status"],
            "refunded_amount": row["refunded_amount"],
            "created_at": row["created_at"],
        }

    def get_payment(self, payment_id: str) -> dict | None:
        row = self._connection.execute("select * from payments where id = ?", (payment_id,)).fetchone()
        return None if row is None else self._payment_row(row)

    def get_payment_by_provider_id(self, provider_payment_id: str) -> dict | None:
        row = self._connection.execute(
            "select * from payments where provider_payment_id = ?", (provider_payment_id,)
        ).fetchone()
        return None if row is None else self._payment_row(row)

    def record_webhook_event(self, event_id: str, event_type: str, provider_payment_id: str | None) -> bool:
        """Insert-if-absent. Returns True the first time an event id is seen."""
        try:
            self._connection.execute(
                "insert into webhook_events (id, event_type, provider_payment_id, processed_at) values (?, ?, ?, ?)",
                (event_id, event_type, provider_payment_id, _now()),
            )
            self._connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def settle_payment(self, payment_id: str, provider_payment_id: str, provider_status: str, paid_amount: int, paid_currency: str) -> dict:
        """Idempotently reconcile a Moyasar-verified payment.

        Only a final Moyasar state moves the row: "paid" activates, failed/voided close the
        attempt. While 3-D Secure is still running ("initiated") the row stays
        'payment_pending' with the provider status kept alongside, so the webhook or the
        callback page can settle it once Moyasar says paid. A paid row is never applied
        twice, and a failed attempt can still be settled by a later paid retry of the same
        checkout (Moyasar's form retries with the same metadata).

        Holds a process-wide lock for the duration of the read-check-write so
        a concurrent verify call and webhook delivery for the same payment
        can't both observe 'payment_pending' before either commits (which
        would double-activate or double-extend a subscription). This store is
        single-connection/dev-only; PgStore uses a real per-user Postgres
        advisory lock for the same purpose in production.
        """
        with self._settlement_lock:
            payment = self.get_payment(payment_id)
            if payment is None:
                return {"ok": False, "reason": "payment_not_found"}
            decision = settle_decision(payment, provider_payment_id, provider_status, paid_amount, paid_currency)
            if decision in ("done", "same"):
                # Already settled by an earlier callback/webhook - idempotent no-op.
                return {"ok": True, "already_processed": True, "status": payment["status"], "activated": False, "subscription": self.get_latest_subscription(payment["user_id"])}
            if decision == "mismatch":
                self._connection.execute(
                    "update payments set status = 'failed', provider_payment_id = ?, provider_status = ? where id = ?",
                    (provider_payment_id, provider_status, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": "failed", "activated": False, "reason": "amount_mismatch"}
            if decision == "closed":
                self._connection.execute(
                    "update payments set status = ?, provider_payment_id = ?, provider_status = ? where id = ?",
                    (provider_status, provider_payment_id, provider_status, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": provider_status, "activated": False}
            if decision == "pending":
                self._connection.execute(
                    "update payments set status = 'payment_pending', provider_payment_id = ?, provider_status = ? where id = ?",
                    (provider_payment_id, provider_status, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": "payment_pending", "provider_status": provider_status, "pending": True, "activated": False}

            plan = self.get_plan(payment["plan"])
            if plan is None:
                self._connection.execute(
                    "update payments set status = 'failed', provider_payment_id = ?, provider_status = ? where id = ?",
                    (provider_payment_id, provider_status, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": "failed", "activated": False, "reason": "unknown_plan"}

            now_iso = _now()
            latest = self.get_latest_subscription(payment["user_id"])
            if latest is not None and latest["status"] == "active" and latest["expires_at"] and latest["expires_at"] > now_iso:
                base = datetime.fromisoformat(latest["expires_at"])
            else:
                base = datetime.now(timezone.utc)

            expires_at = (base + timedelta(days=plan["duration_days"])).isoformat()

            if latest is not None and latest["status"] == "active":
                subscription_id = latest["id"]
                anchor = latest["period_anchor"] if latest["plan"] == plan["code"] and latest["period_anchor"] else now_iso
                self._connection.execute(
                    "update subscriptions set plan = ?, status = 'active', expires_at = ?, period_anchor = ?, updated_at = ? where id = ?",
                    (plan["code"], expires_at, anchor, now_iso, subscription_id),
                )
            else:
                subscription_id = uuid4().hex
                self._connection.execute(
                    "insert into subscriptions (id, user_id, plan, status, starts_at, expires_at, period_anchor, created_at, updated_at) values (?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                    (subscription_id, payment["user_id"], plan["code"], now_iso, expires_at, now_iso, now_iso, now_iso),
                )
            self._connection.execute(
                "update payments set status = 'paid', provider_payment_id = ?, provider_status = ?, subscription_id = ? where id = ?",
                (provider_payment_id, provider_status, subscription_id, payment_id),
            )
            self._connection.commit()
            return {
                "ok": True,
                "already_processed": False,
                "status": "paid",
                "activated": True,
                "subscription": self.get_latest_subscription(payment["user_id"]),
            }

    def refund_payment(self, payment_id: str, provider_payment_id: str, refunded_amount: int | None = None) -> dict:
        """A refund can arrive before a pending payment ever settled, or after it
        already activated a subscription.

        A full refund takes back exactly the term that payment bought: the
        subscription's expiry moves back by the plan's duration, and it is cancelled
        only when nothing paid is left (a refunded renewal keeps the earlier term). A
        partial refund is recorded on the payment and leaves the entitlement alone.
        Before settlement any refund means the payment never grants anything.
        Idempotent: replaying a refund for an already-refunded payment is a no-op.
        """
        with self._settlement_lock:
            payment = self.get_payment(payment_id)
            if payment is None:
                return {"ok": False, "reason": "payment_not_found"}
            if payment["status"] == "refunded":
                return {"ok": True, "already_processed": True, "status": "refunded", "activated": False, "subscription": self.get_latest_subscription(payment["user_id"])}
            amount = payment["amount"] if refunded_amount is None else refunded_amount
            settled = payment["status"] in ("paid", "partially_refunded") and payment["subscription_id"]
            if settled and amount < payment["amount"]:
                if payment["status"] == "partially_refunded" and (payment["refunded_amount"] or 0) >= amount:
                    return {"ok": True, "already_processed": True, "status": "partially_refunded", "activated": False, "subscription": self.get_latest_subscription(payment["user_id"])}
                self._connection.execute(
                    "update payments set status = 'partially_refunded', refunded_amount = ?, provider_payment_id = ? where id = ?",
                    (amount, provider_payment_id, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": "partially_refunded", "activated": False, "subscription": self.get_latest_subscription(payment["user_id"])}
            if settled:
                sub = self._connection.execute("select * from subscriptions where id = ?", (payment["subscription_id"],)).fetchone()
                plan = self.get_plan(payment["plan"])
                if sub is not None and sub["status"] == "active":
                    now = datetime.now(timezone.utc)
                    expires = datetime.fromisoformat(sub["expires_at"]) if sub["expires_at"] else now
                    remaining = expires - timedelta(days=plan["duration_days"] if plan else 0)
                    if plan is None or remaining <= now:
                        self._connection.execute(
                            "update subscriptions set status = 'cancelled', updated_at = ? where id = ?",
                            (_now(), payment["subscription_id"]),
                        )
                    else:
                        self._connection.execute(
                            "update subscriptions set expires_at = ?, updated_at = ? where id = ?",
                            (remaining.isoformat(), _now(), payment["subscription_id"]),
                        )
            self._connection.execute(
                "update payments set status = 'refunded', refunded_amount = ?, provider_payment_id = ? where id = ?",
                (amount, provider_payment_id, payment_id),
            )
            self._connection.commit()
            return {
                "ok": True,
                "already_processed": False,
                "status": "refunded",
                "activated": False,
                "subscription": self.get_latest_subscription(payment["user_id"]),
            }
