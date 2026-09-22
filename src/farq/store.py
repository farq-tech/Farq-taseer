"""Consumer persistence. Separate from FARQ Construction procurement tables."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from farq.cities import known_city
from farq.contracts import Attachment, Message, Offer, RequestRecipient, RequestRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return digest.hex()


class Store:
    def __init__(self, path: Path, upload_dir: Path):
        self.path = path
        self.upload_dir = upload_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
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
        self._ensure_column("messages", "seller_id", "text")
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

    def register(self, email: str, password: str) -> str:
        user_id = uuid4().hex
        salt = secrets.token_hex(16)
        self._connection.execute(
            "insert into users (id, email, password_hash, salt, created_at) values (?, ?, ?, ?, ?)",
            (user_id, email.lower().strip(), _hash_password(password, salt), salt, _now()),
        )
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
        reply_token = secrets.token_urlsafe(24)
        self._connection.execute(
            "insert into requests (id, owner_user_id, original_text, need, notes, city, attributes_json, reply_token, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, owner_user_id, original_text, need, notes, city_name, json.dumps(attributes, ensure_ascii=False), reply_token, _now()),
        )
        self._connection.executemany(
            "insert into request_recipients (request_id, seller_id, seller_name, ad_id) values (?, ?, ?, ?)",
            [(request_id, item.seller_id, item.seller_name, item.ad_id) for item in recipients],
        )
        self._connection.commit()
        lines = ["طلب عرض سعر", (need or original_text or "").strip(), f"المدينة: {city_name}"]
        if notes and notes.strip():
            lines.append(notes.strip())
        self.add_message(request_id, "user", owner_user_id, "\n".join(line for line in lines if line))
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
    ) -> Message:
        message_id = uuid4().hex
        created = _now()
        self._connection.execute(
            "insert into messages (id, request_id, sender_role, sender_user_id, seller_id, body, offer_amount, offer_currency, attachment_ids_json, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                request_id,
                sender_role,
                sender_user_id,
                seller_id,
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
        recipients = [
            RequestRecipient(seller_id=item["seller_id"], seller_name=item["seller_name"], ad_id=item["ad_id"])
            for item in self._connection.execute("select * from request_recipients where request_id = ?", (request_id,))
        ]
        attachments = [
            Attachment(id=item["id"], filename=item["filename"], content_type=item["content_type"], size_bytes=item["size_bytes"])
            for item in self._connection.execute("select * from attachments where request_id = ?", (request_id,))
        ]
        messages = self._messages(request_id)
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
            reply_token=row["reply_token"],
            created_at=row["created_at"],
        )

    def list_requests(self, owner_user_id: str) -> list[dict]:
        rows = self._connection.execute(
            "select * from requests where owner_user_id = ? order by created_at desc",
            (owner_user_id,),
        ).fetchall()
        items = []
        for row in rows:
            request_id = row["id"]
            recipient_count = self._connection.execute(
                "select count(*) as count from request_recipients where request_id = ?",
                (request_id,),
            ).fetchone()["count"]
            seller_names = [
                item["seller_name"]
                for item in self._connection.execute(
                    "select seller_name from request_recipients where request_id = ? order by rowid",
                    (request_id,),
                ).fetchall()
            ]
            messages = self._connection.execute(
                "select sender_role, seller_id, body, offer_amount, offer_currency, created_at from messages where request_id = ? order by created_at",
                (request_id,),
            ).fetchall()
            replied: set[str] = set()
            latest_offer = None
            for message in messages:
                if message["sender_role"] == "seller":
                    replied.add(message["seller_id"] or message["created_at"])
                    if message["offer_amount"] is not None:
                        latest_offer = message
            newest_offer = bool(messages) and messages[-1]["sender_role"] == "seller" and messages[-1]["offer_amount"] is not None
            last = messages[-1] if messages else None
            preview = " ".join((last["body"] or "").split()) if last is not None else ""
            items.append(
                {
                    "id": request_id,
                    "original_text": row["original_text"],
                    "need": row["need"],
                    "city": row["city"],
                    "created_at": row["created_at"],
                    "seller_names": seller_names,
                    "last_message": preview[:180],
                    "last_message_at": None if last is None else last["created_at"],
                    "recipient_count": recipient_count,
                    "replied_count": len(replied),
                    "waiting_count": max(0, recipient_count - len(replied)),
                    "has_new_offer": newest_offer,
                    "latest_offer_amount": None if latest_offer is None else latest_offer["offer_amount"],
                    "latest_offer_currency": None if latest_offer is None else latest_offer["offer_currency"],
                }
            )
        return items

    def _request_by_token(self, token: str):
        return self._connection.execute("select * from requests where reply_token = ?", (token,)).fetchone()

    def seller_view(self, token: str) -> dict | None:
        row = self._request_by_token(token)
        if row is None:
            return None
        recipients = [
            {"seller_id": item["seller_id"], "seller_name": item["seller_name"], "ad_id": item["ad_id"]}
            for item in self._connection.execute("select * from request_recipients where request_id = ?", (row["id"],))
        ]
        attachments = [
            {"id": item["id"], "filename": item["filename"], "content_type": item["content_type"], "size_bytes": item["size_bytes"]}
            for item in self._connection.execute("select * from attachments where request_id = ?", (row["id"],))
        ]
        return {
            "need": row["need"],
            "original_text": row["original_text"],
            "notes": row["notes"],
            "city": row["city"],
            "recipients": recipients,
            "attachments": attachments,
            "messages": [item.model_dump(mode="json") for item in self._messages(row["id"])],
        }

    def _messages(self, request_id: str) -> list[Message]:
        messages = []
        for item in self._connection.execute("select * from messages where request_id = ? order by created_at", (request_id,)):
            offer = None
            if item["offer_amount"] is not None:
                offer = Offer(amount=item["offer_amount"], currency=item["offer_currency"])
            messages.append(
                Message(
                    id=item["id"],
                    request_id=request_id,
                    sender_role=item["sender_role"],
                    seller_id=item["seller_id"],
                    body=item["body"],
                    offer=offer,
                    attachment_ids=json.loads(item["attachment_ids_json"]),
                    created_at=item["created_at"],
                )
            )
        return messages

    def add_seller_reply(self, token: str, seller_id: str | None, body: str, offer: Offer | None) -> Message:
        row = self._request_by_token(token)
        if row is None:
            raise ValueError("request not found")
        recipients = self._connection.execute(
            "select seller_id from request_recipients where request_id = ?",
            (row["id"],),
        ).fetchall()
        ids = [item["seller_id"] for item in recipients]
        if seller_id is None:
            if len(ids) != 1:
                raise ValueError("seller_id required")
            seller_id = ids[0]
        if seller_id not in ids:
            raise ValueError("unknown seller")
        text = body.strip() or "عرض سعر"
        message = self.add_message(row["id"], "seller", None, text, offer, seller_id=seller_id)
        self._connection.execute(
            "insert into notifications (id, user_id, request_id, kind, created_at) values (?, ?, ?, ?, ?)",
            (uuid4().hex, row["owner_user_id"], row["id"], "seller_reply", message.created_at),
        )
        self._connection.commit()
        return message

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
        """
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
