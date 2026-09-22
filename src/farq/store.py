"""Consumer persistence. Separate from FARQ Construction procurement tables."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from farq.cities import known_city
from farq.contracts import Attachment, Message, Offer, RequestRecipient, RequestRecord
from farq.haraj_chat import InboundMessage, SentMessage, extract_price

ALL_SELLERS = "all_sellers"
SINGLE_SELLER = "single_seller"
SOME_SELLERS = "some_sellers"
MEDIA_TYPES = {"image/jpeg", "application/pdf"}
MAX_FILE_BYTES = 4 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return digest.hex()


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


def visible_to_seller(message: Message, seller_id: str | None, need: str | None) -> bool:
    if message.sender_role == "seller":
        return seller_id is not None and message.seller_id == seller_id
    if message.seller_id is not None:
        return message.seller_id == seller_id
    return message.need is None or need is None or message.need == need


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


def request_summary(row, recipients: list[RequestRecipient], offers: list[Offer], messages) -> dict:
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
    }


class Store:
    def __init__(self, path: Path, upload_dir: Path):
        self.path = path
        self.upload_dir = upload_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._settlement_lock = threading.Lock()
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
            """
        )
        self._connection.commit()
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
        self._connection.commit()
        self._seed_plans()

    def _seed_plans(self) -> None:
        existing = self._connection.execute("select count(*) as count from subscription_plans").fetchone()["count"]
        if existing:
            return
        now = _now()
        self._connection.execute(
            """
            insert into subscription_plans
              (code, name_ar, name_en, description_ar, price_amount, currency, duration_days,
               features_json, is_active, is_placeholder_price, moyasar_metadata_json, created_at, updated_at)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "monthly_placeholder",
                "الاشتراك الشهري (سعر تجريبي مؤقت)",
                "Monthly plan (placeholder test price)",
                "سعر مؤقت لاختبار الدفع في وضع Sandbox فقط. يجب تحديد السعر النهائي قبل الإطلاق.",
                100,
                "SAR",
                30,
                json.dumps(["ميزات تجريبية سيتم تحديدها لاحقاً"], ensure_ascii=False),
                1,
                1,
                json.dumps({}, ensure_ascii=False),
                now,
                now,
            ),
        )
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
        if row is None or _hash_password(password, row["salt"]) != row["password_hash"]:
            return None
        token = secrets.token_urlsafe(32)
        self._connection.execute(
            "insert into sessions (token, user_id, created_at) values (?, ?, ?)",
            (token, row["id"], _now()),
        )
        self._connection.commit()
        return token

    def start_guest(self) -> dict:
        email = f"guest-{uuid4().hex}@users.farq.local"
        password = secrets.token_urlsafe(18)
        user_id = self.register(email, password)
        token = self.login(email, password)
        return {"user_id": user_id, "token": token}

    def user_for_token(self, token: str) -> str | None:
        row = self._connection.execute("select user_id from sessions where token = ?", (token,)).fetchone()
        return None if row is None else row["user_id"]

    def account_for_token(self, token: str) -> dict | None:
        row = self._connection.execute(
            "select u.id, u.email, u.name from sessions s join users u on u.id = s.user_id where s.token = ?", (token,)
        ).fetchone()
        return None if row is None else dict(row)

    def logout(self, token: str) -> None:
        self._connection.execute("delete from sessions where token = ?", (token,))
        self._connection.commit()

    def record_journey(self, trace_id: str, user_id: str | None, query: str, state: str, trace: dict) -> None:
        self._connection.execute(
            "insert or replace into search_journeys (trace_id, user_id, query, state, trace_json, created_at) values (?, ?, ?, ?, ?, ?)",
            (trace_id, user_id, query, state, json.dumps(trace, ensure_ascii=False), _now()),
        )
        self._connection.commit()

    def journey(self, trace_id: str) -> dict | None:
        row = self._connection.execute("select trace_json from search_journeys where trace_id = ?", (trace_id,)).fetchone()
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
    ) -> str:
        if not recipients:
            raise ValueError("at least one recipient is required")
        city_name = known_city(city)
        if city_name is None:
            raise ValueError("city is required")
        request_id = uuid4().hex
        first_token = secrets.token_urlsafe(16)
        created = _now()
        self._connection.execute(
            "insert into requests (id, owner_user_id, original_text, need, notes, city, attributes_json, reply_token, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, owner_user_id, original_text, need, notes, city_name, json.dumps(attributes, ensure_ascii=False), first_token, created),
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
            last_synced_at=row["last_synced_at"] if "last_synced_at" in row.keys() else None,
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
        for row in targets:
            self._connection.execute(
                "insert or ignore into haraj_threads (request_id, seller_id, need, ad_id) values (?, ?, ?, ?)",
                (request_id, row["seller_id"], item_of(row), row["ad_id"]),
            )
            self._connection.execute(
                "insert into message_deliveries (id, message_id, request_id, seller_id, need, delivery_status, created_at) values (?, ?, ?, ?, ?, 'queued', ?)",
                (uuid4().hex, message.id, request_id, row["seller_id"], item_of(row), created),
            )
        self._connection.commit()
        return next(item for item in self._messages_for_request(request_id) if item.id == message.id)

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
            if quoted["seller_id"]:
                seller_id = quoted["seller_id"]
            need = need or quoted["need"]
            seller_ids = None
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
            select d.id, d.request_id, d.seller_id, d.need, coalesce(m.haraj_text, m.body) as body, m.media_json as media, t.ad_id, t.haraj_conversation_id,
              (select r.reply_token from request_recipients r where r.request_id = d.request_id and r.seller_id = d.seller_id
               order by coalesce(r.need, '') = d.need desc limit 1) as reply_token
            from message_deliveries d
            join messages m on m.id = d.message_id
            join haraj_threads t on t.request_id = d.request_id and t.seller_id = d.seller_id and t.need = d.need
            where d.delivery_status = 'queued'
            order by d.created_at
            limit ?
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            self._connection.execute("update message_deliveries set delivery_status = 'sending', attempts = attempts + 1, last_attempt_at = ? where id = ?", (_now(), row["id"]))
        self._connection.commit()
        return [{**dict(row), "media": json.loads(row["media"]) if row["media"] else []} for row in rows]

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

    def threads_to_sync(self, limit: int = 20, now: float | None = None) -> list[dict]:
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
        price = extract_price(inbound.body)
        if price is not None:
            offer = Offer(
                amount=price,
                currency="SAR",
                note=inbound.body,
                provider_name=recipient["seller_name"] if recipient else None,
                seller_id=seller_id,
                total_price=price,
                need=need,
            )
            # Latest price from a seller on an item replaces the earlier one.
            self._connection.execute("delete from offers where request_id = ? and seller_id = ? and coalesce(need, '') = ?", (request_id, seller_id, need or ""))
            self._connection.execute(
                "insert into offers (id, request_id, seller_id, need, provider_name, total_price, currency, message, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid4().hex, request_id, seller_id, need, offer.provider_name, price, "SAR", inbound.body, _now()),
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
        rows = self._connection.execute("select * from requests where owner_user_id = ? order by created_at desc", (owner_user_id,)).fetchall()
        items = []
        for row in rows:
            recipients = [self._recipient_from_row(item) for item in self._connection.execute("select * from request_recipients where request_id = ?", (row["id"],))]
            messages = self._connection.execute(
                "select sender_role, seller_id, body, offer_amount, offer_currency, created_at from messages where request_id = ? order by created_at",
                (row["id"],),
            ).fetchall()
            items.append(request_summary(row, recipients, self._offers_for_request(row["id"]), messages))
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
        rows = self._connection.execute("select * from offers where request_id = ? order by total_price is null, total_price", (request_id,)).fetchall()
        offers: list[Offer] = []
        for item in rows:
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
        return {
            "need": need,
            "original_text": row["original_text"],
            "notes": row["notes"],
            "city": row["city"],
            "recipients": [item.model_dump(mode="json") for item in recipients],
            "attachments": attachments,
            # Legacy seller link only: each seller sees their own thread, never another seller's messages.
            "messages": [
                item.model_dump(mode="json")
                for item in self._messages_for_request(row["id"])
                if visible_to_seller(item, recipient_row["seller_id"] if recipient_row is not None else None, need)
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
        if offer is not None:
            offer = priced_offer(offer, body, matched["seller_name"], self._col(matched, "need") or row["need"])
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

    def mark_read(self, request_id: str, owner_user_id: str) -> None:
        self._connection.execute("update requests set customer_read_at = ? where id = ? and owner_user_id = ?", (_now(), request_id, owner_user_id))
        self._connection.commit()

    def recipient_name(self, request_id: str, seller_id: str | None) -> str | None:
        row = self._connection.execute("select seller_name from request_recipients where request_id = ? and seller_id = ?", (request_id, seller_id)).fetchone()
        return None if row is None else row["seller_name"]

    def add_push_subscription(self, user_id: str, endpoint: str, p256dh: str, auth: str) -> None:
        self._connection.execute(
            "insert into push_subscriptions (endpoint, user_id, p256dh, auth, created_at) values (?, ?, ?, ?, ?)"
            " on conflict (endpoint) do update set user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth",
            (endpoint, user_id, p256dh, auth, _now()),
        )
        self._connection.commit()

    def remove_push_subscription(self, endpoint: str) -> None:
        self._connection.execute("delete from push_subscriptions where endpoint = ?", (endpoint,))
        self._connection.commit()

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
        }

    def _subscription_row(self, row) -> dict:
        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "plan": row["plan"],
            "status": row["status"],
            "starts_at": row["starts_at"],
            "expires_at": row["expires_at"],
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
        """Idempotently reconcile a Moyasar-verified payment. Only ever moves a
        payment out of 'payment_pending'; replaying the same event is a no-op.

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
            if payment["status"] != "payment_pending":
                # Already settled by an earlier callback/webhook - idempotent no-op.
                return {"ok": True, "already_processed": True, "status": payment["status"], "subscription": self.get_latest_subscription(payment["user_id"])}
            if payment["amount"] != paid_amount or payment["currency"] != paid_currency:
                self._connection.execute(
                    "update payments set status = 'failed', provider_payment_id = ? where id = ?",
                    (provider_payment_id, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": "failed", "reason": "amount_mismatch"}
            if provider_status != "paid":
                self._connection.execute(
                    "update payments set status = ?, provider_payment_id = ? where id = ?",
                    (provider_status, provider_payment_id, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": provider_status, "activated": False}

            plan = self.get_plan(payment["plan"])
            if plan is None:
                self._connection.execute(
                    "update payments set status = 'failed', provider_payment_id = ? where id = ?",
                    (provider_payment_id, payment_id),
                )
                self._connection.commit()
                return {"ok": True, "already_processed": False, "status": "failed", "reason": "unknown_plan"}

            now_iso = _now()
            latest = self.get_latest_subscription(payment["user_id"])
            if latest is not None and latest["status"] == "active" and latest["expires_at"] and latest["expires_at"] > now_iso:
                base = datetime.fromisoformat(latest["expires_at"])
            else:
                base = datetime.now(timezone.utc)

            expires_at = (base + timedelta(days=plan["duration_days"])).isoformat()

            if latest is not None and latest["status"] == "active":
                subscription_id = latest["id"]
                self._connection.execute(
                    "update subscriptions set plan = ?, status = 'active', expires_at = ?, updated_at = ? where id = ?",
                    (plan["code"], expires_at, now_iso, subscription_id),
                )
            else:
                subscription_id = uuid4().hex
                self._connection.execute(
                    "insert into subscriptions (id, user_id, plan, status, starts_at, expires_at, created_at, updated_at) values (?, ?, ?, 'active', ?, ?, ?, ?)",
                    (subscription_id, payment["user_id"], plan["code"], now_iso, expires_at, now_iso, now_iso),
                )
            self._connection.execute(
                "update payments set status = 'paid', provider_payment_id = ?, subscription_id = ? where id = ?",
                (provider_payment_id, subscription_id, payment_id),
            )
            self._connection.commit()
            return {
                "ok": True,
                "already_processed": False,
                "status": "paid",
                "activated": True,
                "subscription": self.get_latest_subscription(payment["user_id"]),
            }

    def refund_payment(self, payment_id: str, provider_payment_id: str) -> dict:
        """A refund can arrive before a pending payment ever settled, or
        after it already activated a subscription - handle both so a
        refunded charge never grants or keeps entitlement. Idempotent:
        replaying a refund event for an already-refunded payment is a no-op.
        """
        with self._settlement_lock:
            payment = self.get_payment(payment_id)
            if payment is None:
                return {"ok": False, "reason": "payment_not_found"}
            if payment["status"] == "refunded":
                return {"ok": True, "already_processed": True, "status": "refunded", "subscription": self.get_latest_subscription(payment["user_id"])}
            if payment["subscription_id"]:
                self._connection.execute(
                    "update subscriptions set status = 'cancelled', updated_at = ? where id = ? and status = 'active'",
                    (_now(), payment["subscription_id"]),
                )
            self._connection.execute(
                "update payments set status = 'refunded', provider_payment_id = ? where id = ?",
                (provider_payment_id, payment_id),
            )
            self._connection.commit()
            return {
                "ok": True,
                "already_processed": False,
                "status": "refunded",
                "activated": False,
                "subscription": self.get_latest_subscription(payment["user_id"]),
            }
