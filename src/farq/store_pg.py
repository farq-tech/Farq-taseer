"""Postgres-backed persistence (Supabase `taseer` schema). Same public method
surface as farq.store.Store so api.py does not care which one it holds.

This is what production must use: Vercel serverless functions get an
ephemeral /tmp per instance, so the SQLite Store silently loses every user,
request and subscription on cold start or when a request lands on a
different instance. create_default_app() in api.py refuses to boot on
Vercel without DATABASE_URL for exactly this reason.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from farq.cities import known_city
from farq.contracts import Attachment, Message, Offer, RequestRecipient, RequestRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return digest.hex()


class PgStore:
    def __init__(self, database_url: str, upload_dir: Path, min_size: int = 1, max_size: int = 5):
        self.upload_dir = upload_dir
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._pool = ConnectionPool(
            database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row, "options": "-c search_path=taseer"},
            open=True,
        )

    def close(self) -> None:
        self._pool.close()

    # -- auth -----------------------------------------------------------------

    def register(self, email: str, password: str) -> str:
        user_id = uuid4().hex
        salt = secrets.token_hex(16)
        with self._pool.connection() as conn:
            try:
                conn.execute(
                    "insert into users (id, email, password_hash, salt, created_at) values (%s, %s, %s, %s, %s)",
                    (user_id, email.lower().strip(), _hash_password(password, salt), salt, _now()),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError("email already registered") from exc
        return user_id

    def login(self, email: str, password: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from users where email = %s", (email.lower().strip(),)).fetchone()
            if row is None or _hash_password(password, row["salt"]) != row["password_hash"]:
                return None
            token = secrets.token_urlsafe(32)
            conn.execute(
                "insert into sessions (token, user_id, created_at) values (%s, %s, %s)",
                (token, row["id"], _now()),
            )
            return token

    def start_guest(self) -> dict:
        email = f"guest-{uuid4().hex}@users.farq.local"
        password = secrets.token_urlsafe(18)
        user_id = self.register(email, password)
        token = self.login(email, password)
        return {"user_id": user_id, "token": token}

    def user_for_token(self, token: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("select user_id from sessions where token = %s", (token,)).fetchone()
            return None if row is None else row["user_id"]

    # -- search journeys --------------------------------------------------------

    def record_journey(self, trace_id: str, user_id: str | None, query: str, state: str, trace: dict) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into search_journeys (trace_id, user_id, query, state, trace, created_at) values (%s, %s, %s, %s, %s, %s)"
                " on conflict (trace_id) do update set user_id = excluded.user_id, query = excluded.query, state = excluded.state, trace = excluded.trace",
                (trace_id, user_id, query, state, Jsonb(trace), _now()),
            )

    def journey(self, trace_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute("select trace from search_journeys where trace_id = %s", (trace_id,)).fetchone()
            return None if row is None else row["trace"]

    # -- requests ---------------------------------------------------------------

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
        with self._pool.connection() as conn:
            conn.execute(
                "insert into requests (id, owner_user_id, original_text, need, notes, city, attributes, reply_token, created_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (request_id, owner_user_id, original_text, need, notes, city_name, Jsonb(attributes), reply_token, _now()),
            )
            conn.executemany(
                "insert into request_recipients (request_id, seller_id, seller_name, ad_id) values (%s, %s, %s, %s)",
                [(request_id, item.seller_id, item.seller_name, item.ad_id) for item in recipients],
            )
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
        with self._pool.connection() as conn:
            conn.execute(
                "insert into attachments (id, request_id, owner_user_id, filename, content_type, size_bytes, path, created_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (attachment_id, request_id, owner_user_id, safe_name, content_type, len(content), str(path), _now()),
            )
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
        with self._pool.connection() as conn:
            conn.execute(
                "insert into messages (id, request_id, sender_role, sender_user_id, seller_id, body, offer_amount, offer_currency, attachment_ids, created_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    message_id,
                    request_id,
                    sender_role,
                    sender_user_id,
                    seller_id,
                    body,
                    None if offer is None else offer.amount,
                    None if offer is None else offer.currency,
                    Jsonb(attachment_ids or []),
                    created,
                ),
            )
            if sender_user_id:
                conn.execute(
                    "insert into notifications (id, user_id, request_id, kind, created_at) values (%s, %s, %s, %s, %s)",
                    (uuid4().hex, sender_user_id, request_id, "message", created),
                )
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
        with self._pool.connection() as conn:
            row = conn.execute(
                "select * from requests where id = %s and owner_user_id = %s", (request_id, owner_user_id)
            ).fetchone()
            if row is None:
                return None
            recipients = [
                RequestRecipient(seller_id=item["seller_id"], seller_name=item["seller_name"], ad_id=item["ad_id"])
                for item in conn.execute("select * from request_recipients where request_id = %s", (request_id,))
            ]
            attachments = [
                Attachment(id=item["id"], filename=item["filename"], content_type=item["content_type"], size_bytes=item["size_bytes"])
                for item in conn.execute("select * from attachments where request_id = %s", (request_id,))
            ]
            messages = self._messages(conn, request_id)
        return RequestRecord(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            original_text=row["original_text"],
            need=row["need"],
            notes=row["notes"],
            city=row["city"],
            attributes=row["attributes"],
            recipients=recipients,
            attachments=attachments,
            messages=messages,
            reply_token=row["reply_token"],
            created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else row["created_at"],
        )

    def list_requests(self, owner_user_id: str) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select * from requests where owner_user_id = %s order by created_at desc", (owner_user_id,)
            ).fetchall()
            items = []
            for row in rows:
                request_id = row["id"]
                recipient_count = conn.execute(
                    "select count(*) as count from request_recipients where request_id = %s", (request_id,)
                ).fetchone()["count"]
                seller_names = [
                    item["seller_name"]
                    for item in conn.execute(
                        "select seller_name from request_recipients where request_id = %s order by id", (request_id,)
                    ).fetchall()
                ]
                messages = conn.execute(
                    "select sender_role, seller_id, body, offer_amount, offer_currency, created_at from messages"
                    " where request_id = %s order by created_at",
                    (request_id,),
                ).fetchall()
                replied: set[str] = set()
                latest_offer = None
                for message in messages:
                    if message["sender_role"] == "seller":
                        replied.add(message["seller_id"] or str(message["created_at"]))
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
                        "created_at": _iso(row["created_at"]),
                        "seller_names": seller_names,
                        "last_message": preview[:180],
                        "last_message_at": None if last is None else _iso(last["created_at"]),
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
        with self._pool.connection() as conn:
            return conn.execute("select * from requests where reply_token = %s", (token,)).fetchone()

    def seller_view(self, token: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from requests where reply_token = %s", (token,)).fetchone()
            if row is None:
                return None
            recipients = [
                {"seller_id": item["seller_id"], "seller_name": item["seller_name"], "ad_id": item["ad_id"]}
                for item in conn.execute("select * from request_recipients where request_id = %s", (row["id"],))
            ]
            attachments = [
                {"id": item["id"], "filename": item["filename"], "content_type": item["content_type"], "size_bytes": item["size_bytes"]}
                for item in conn.execute("select * from attachments where request_id = %s", (row["id"],))
            ]
            messages = self._messages(conn, row["id"])
        return {
            "need": row["need"],
            "original_text": row["original_text"],
            "notes": row["notes"],
            "city": row["city"],
            "recipients": recipients,
            "attachments": attachments,
            "messages": [item.model_dump(mode="json") for item in messages],
        }

    def _messages(self, conn, request_id: str) -> list[Message]:
        messages = []
        for item in conn.execute("select * from messages where request_id = %s order by created_at", (request_id,)):
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
                    attachment_ids=item["attachment_ids"],
                    created_at=_iso(item["created_at"]),
                )
            )
        return messages

    def add_seller_reply(self, token: str, seller_id: str | None, body: str, offer: Offer | None) -> Message:
        row = self._request_by_token(token)
        if row is None:
            raise ValueError("request not found")
        with self._pool.connection() as conn:
            recipients = conn.execute(
                "select seller_id from request_recipients where request_id = %s", (row["id"],)
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
        with self._pool.connection() as conn:
            conn.execute(
                "insert into notifications (id, user_id, request_id, kind, created_at) values (%s, %s, %s, %s, %s)",
                (uuid4().hex, row["owner_user_id"], row["id"], "seller_reply", message.created_at),
            )
        return message

    def attachment_path(self, request_id: str, attachment_id: str, owner_user_id: str | None = None, reply_token: str | None = None) -> tuple[Path, str, str] | None:
        with self._pool.connection() as conn:
            if owner_user_id is not None:
                owner = conn.execute(
                    "select id from requests where id = %s and owner_user_id = %s", (request_id, owner_user_id)
                ).fetchone()
                if owner is None:
                    return None
            elif reply_token is not None:
                owner = conn.execute(
                    "select id from requests where id = %s and reply_token = %s", (request_id, reply_token)
                ).fetchone()
                if owner is None:
                    return None
            else:
                return None
            row = conn.execute(
                "select * from attachments where id = %s and request_id = %s", (attachment_id, request_id)
            ).fetchone()
        if row is None:
            return None
        return Path(row["path"]), row["content_type"], row["filename"]

    # -- subscriptions & payments -------------------------------------------------

    def list_active_plans(self) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute("select * from subscription_plans where is_active order by price_amount asc").fetchall()
            return [self._plan_row(row) for row in rows]

    def get_plan(self, code: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from subscription_plans where code = %s", (code,)).fetchone()
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
            "features": row["features"],
            "is_active": row["is_active"],
            "is_placeholder_price": row["is_placeholder_price"],
        }

    def _subscription_row(self, row) -> dict:
        return {
            "id": str(row["id"]),
            "user_id": row["user_id"],
            "plan": row["plan"],
            "status": row["status"],
            "starts_at": _iso(row["starts_at"]),
            "expires_at": _iso(row["expires_at"]),
            "created_at": _iso(row["created_at"]),
            "updated_at": _iso(row["updated_at"]),
        }

    def get_latest_subscription(self, user_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select * from subscriptions where user_id = %s order by created_at desc limit 1", (user_id,)
            ).fetchone()
            return None if row is None else self._subscription_row(row)

    def subscription_status(self, user_id: str) -> str:
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
        payment_id = uuid4()
        with self._pool.connection() as conn:
            conn.execute(
                "insert into payments (id, user_id, provider, provider_payment_id, amount, currency, status, subscription_id, plan, created_at)"
                " values (%s, %s, 'moyasar', null, %s, %s, 'payment_pending', null, %s, %s)",
                (payment_id, user_id, amount, currency, plan_code, _now()),
            )
        return self.get_payment(str(payment_id))

    def _payment_row(self, row) -> dict:
        return {
            "id": str(row["id"]),
            "user_id": row["user_id"],
            "provider": row["provider"],
            "provider_payment_id": row["provider_payment_id"],
            "amount": row["amount"],
            "currency": row["currency"],
            "status": row["status"],
            "subscription_id": str(row["subscription_id"]) if row["subscription_id"] else None,
            "plan": row["plan"],
            "created_at": _iso(row["created_at"]),
        }

    def get_payment(self, payment_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from payments where id = %s", (payment_id,)).fetchone()
            return None if row is None else self._payment_row(row)

    def get_payment_by_provider_id(self, provider_payment_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from payments where provider_payment_id = %s", (provider_payment_id,)).fetchone()
            return None if row is None else self._payment_row(row)

    def record_webhook_event(self, event_id: str, event_type: str, provider_payment_id: str | None) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "insert into webhook_events (id, event_type, provider_payment_id, processed_at) values (%s, %s, %s, %s)"
                " on conflict (id) do nothing",
                (event_id, event_type, provider_payment_id, _now()),
            )
            return cur.rowcount > 0

    def _lock_user_for_settlement(self, conn, payment_id: str):
        """Serializes settle_payment/refund_payment for one user behind a
        Postgres advisory lock (released automatically at transaction end).
        Without this, a browser verify call and a webhook delivery for the
        same payment - or two distinct renewals for the same user - can both
        read 'payment_pending' before either writes, double-activating a
        subscription or losing a paid renewal term. Returns the payment row
        (re-read after the lock is held) or None if it doesn't exist.
        """
        owner = conn.execute("select user_id from payments where id = %s", (payment_id,)).fetchone()
        if owner is None:
            return None
        conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (owner["user_id"],))
        return conn.execute("select * from payments where id = %s", (payment_id,)).fetchone()

    def settle_payment(self, payment_id: str, provider_payment_id: str, provider_status: str, paid_amount: int, paid_currency: str) -> dict:
        with self._pool.connection() as conn:
            payment = self._lock_user_for_settlement(conn, payment_id)
            if payment is None:
                return {"ok": False, "reason": "payment_not_found"}
            if payment["status"] != "payment_pending":
                return {
                    "ok": True,
                    "already_processed": True,
                    "status": payment["status"],
                    "subscription": self.get_latest_subscription(payment["user_id"]),
                }
            if payment["amount"] != paid_amount or payment["currency"] != paid_currency:
                conn.execute(
                    "update payments set status = 'failed', provider_payment_id = %s where id = %s",
                    (provider_payment_id, payment_id),
                )
                return {"ok": True, "already_processed": False, "status": "failed", "reason": "amount_mismatch"}
            if provider_status != "paid":
                conn.execute(
                    "update payments set status = %s, provider_payment_id = %s where id = %s",
                    (provider_status, provider_payment_id, payment_id),
                )
                return {"ok": True, "already_processed": False, "status": provider_status, "activated": False}

            plan = conn.execute("select * from subscription_plans where code = %s", (payment["plan"],)).fetchone()
            if plan is None:
                conn.execute(
                    "update payments set status = 'failed', provider_payment_id = %s where id = %s",
                    (provider_payment_id, payment_id),
                )
                return {"ok": True, "already_processed": False, "status": "failed", "reason": "unknown_plan"}

            now = datetime.now(timezone.utc)
            latest = conn.execute(
                "select * from subscriptions where user_id = %s order by created_at desc limit 1", (payment["user_id"],)
            ).fetchone()
            if latest is not None and latest["status"] == "active" and latest["expires_at"] and latest["expires_at"] > now:
                base = latest["expires_at"]
            else:
                base = now
            expires_at = base + timedelta(days=plan["duration_days"])

            if latest is not None and latest["status"] == "active":
                subscription_id = latest["id"]
                conn.execute(
                    "update subscriptions set plan = %s, status = 'active', expires_at = %s, updated_at = %s where id = %s",
                    (plan["code"], expires_at, now, subscription_id),
                )
            else:
                subscription_id = uuid4()
                conn.execute(
                    "insert into subscriptions (id, user_id, plan, status, starts_at, expires_at, created_at, updated_at)"
                    " values (%s, %s, %s, 'active', %s, %s, %s, %s)",
                    (subscription_id, payment["user_id"], plan["code"], now, expires_at, now, now),
                )
            conn.execute(
                "update payments set status = 'paid', provider_payment_id = %s, subscription_id = %s where id = %s",
                (provider_payment_id, subscription_id, payment_id),
            )
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
        with self._pool.connection() as conn:
            payment = self._lock_user_for_settlement(conn, payment_id)
            if payment is None:
                return {"ok": False, "reason": "payment_not_found"}
            if payment["status"] == "refunded":
                return {"ok": True, "already_processed": True, "status": "refunded", "subscription": self.get_latest_subscription(payment["user_id"])}
            if payment["subscription_id"]:
                conn.execute(
                    "update subscriptions set status = 'cancelled', updated_at = %s where id = %s and status = 'active'",
                    (datetime.now(timezone.utc), payment["subscription_id"]),
                )
            conn.execute(
                "update payments set status = 'refunded', provider_payment_id = %s where id = %s",
                (provider_payment_id, payment_id),
            )
        return {
            "ok": True,
            "already_processed": False,
            "status": "refunded",
            "activated": False,
            "subscription": self.get_latest_subscription(payment["user_id"]),
        }


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
