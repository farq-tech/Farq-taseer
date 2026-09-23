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
from farq.haraj_chat import InboundMessage, SentMessage, extract_quote, new_reference
from farq.store import (
    SINGLE_SELLER,
    AwardConflict,
    _delivery_state,
    choose_thread,
    current_offer_rows,
    media_entry,
    invite_text,
    mark_cheapest,
    password_matches,
    priced_offer,
    reply_audience,
    request_summary,
    route_targets,
    seller_key,
    seller_message,
    session_cutoff,
    session_keys,
    settle_decision,
    token_digest,
    visible_to_seller,
)


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
            # prepare_threshold=None: Supabase's transaction pooler hands each transaction to any
            # backend, so server-side prepared statements collide ("_pg3_0 already exists").
            kwargs={"row_factory": dict_row, "options": "-c search_path=taseer", "prepare_threshold": None},
            open=True,
        )

    def close(self) -> None:
        self._pool.close()

    # -- auth -----------------------------------------------------------------

    def register(self, email: str, password: str, name: str | None = None) -> str:
        user_id = uuid4().hex
        salt = secrets.token_hex(16)
        with self._pool.connection() as conn:
            try:
                conn.execute(
                    "insert into users (id, email, password_hash, salt, name, created_at) values (%s, %s, %s, %s, %s, %s)",
                    (user_id, email.lower().strip(), _hash_password(password, salt), salt, name, _now()),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError("email already registered") from exc
        return user_id

    def login(self, email: str, password: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from users where email = %s", (email.lower().strip(),)).fetchone()
        if not password_matches(password, row):
            return None
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn:
            conn.execute(
                "insert into sessions (token, user_id, created_at) values (%s, %s, %s)",
                (token_digest(token), row["id"], _now()),
            )
            conn.execute("delete from sessions where user_id = %s and created_at < %s", (row["id"], session_cutoff()))
        return token

    def start_guest(self) -> dict:
        email = f"guest-{uuid4().hex}@users.farq.local"
        password = secrets.token_urlsafe(18)
        user_id = self.register(email, password)
        token = self.login(email, password)
        return {"user_id": user_id, "token": token}

    def _session(self, conn, token: str):
        """The live session row for a presented token (joined to its user), or None.
        Expired sessions are deleted; a pre-hashing raw token is rewritten to its digest."""
        if not token:
            return None
        keys = list(session_keys(token))
        row = conn.execute(
            "select s.token, s.created_at, u.id, u.email, u.name from sessions s join users u on u.id = s.user_id where s.token = any(%s)",
            (keys,),
        ).fetchone()
        if row is None:
            return None
        if row["created_at"] < session_cutoff():
            conn.execute("delete from sessions where token = %s", (row["token"],))
            return None
        if row["token"] != keys[0]:
            conn.execute("update sessions set token = %s where token = %s", (keys[0], row["token"]))
        return row

    def user_for_token(self, token: str) -> str | None:
        with self._pool.connection() as conn:
            row = self._session(conn, token)
        return None if row is None else row["id"]

    def account_for_token(self, token: str) -> dict | None:
        with self._pool.connection() as conn:
            row = self._session(conn, token)
        return None if row is None else {"id": row["id"], "email": row["email"], "name": row["name"]}

    def logout(self, token: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("delete from sessions where token = any(%s)", (list(session_keys(token)),))

    def login_failures(self, key: str, window_seconds: int) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) as count from login_attempts where key = %s and attempted_at >= now() - make_interval(secs => %s)",
                (key, window_seconds),
            ).fetchone()
        return row["count"]

    def record_login_failure(self, keys: list[str]) -> None:
        with self._pool.connection() as conn:
            for key in keys:
                conn.execute("insert into login_attempts (key) values (%s)", (key,))
            conn.execute("delete from login_attempts where attempted_at < now() - interval '1 day'")

    def clear_login_failures(self, key: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("delete from login_attempts where key = %s", (key,))

    # -- idempotency ------------------------------------------------------------

    def reserve_idempotency(self, user_id: str, scope: str, key: str, fingerprint: str) -> dict | None:
        """Claims (user, scope, key). Returns None when this call now owns the key, otherwise
        the earlier claim {"fingerprint", "response"}; response is None while it is still running."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "insert into idempotency_keys (user_id, scope, key, fingerprint) values (%s, %s, %s, %s) on conflict do nothing",
                (user_id, scope, key, fingerprint),
            )
            if cur.rowcount > 0:
                return None
            row = conn.execute(
                "select fingerprint, response from idempotency_keys where user_id = %s and scope = %s and key = %s", (user_id, scope, key)
            ).fetchone()
        if row is None:
            return self.reserve_idempotency(user_id, scope, key, fingerprint)
        return {"fingerprint": row["fingerprint"], "response": row["response"]}

    def complete_idempotency(self, user_id: str, scope: str, key: str, response: dict) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "update idempotency_keys set response = %s where user_id = %s and scope = %s and key = %s",
                (Jsonb(response), user_id, scope, key),
            )

    def release_idempotency(self, user_id: str, scope: str, key: str) -> None:
        """A failed attempt gives its key back so the client can retry with it."""
        with self._pool.connection() as conn:
            conn.execute(
                "delete from idempotency_keys where user_id = %s and scope = %s and key = %s and response is null", (user_id, scope, key)
            )

    # -- search journeys --------------------------------------------------------

    def record_journey(self, trace_id: str, user_id: str | None, query: str, state: str, trace: dict) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into search_journeys (trace_id, user_id, query, state, trace, created_at) values (%s, %s, %s, %s, %s, %s)"
                " on conflict (trace_id) do update set user_id = excluded.user_id, query = excluded.query, state = excluded.state, trace = excluded.trace",
                (trace_id, user_id, query, state, Jsonb(trace), _now()),
            )

    def journey(self, trace_id: str, owner_user_id: str | None = None) -> dict | None:
        """With an owner, only that account's own searches are found."""
        with self._pool.connection() as conn:
            if owner_user_id is None:
                row = conn.execute("select trace from search_journeys where trace_id = %s", (trace_id,)).fetchone()
            else:
                row = conn.execute(
                    "select trace from search_journeys where trace_id = %s and user_id = %s", (trace_id, owner_user_id)
                ).fetchone()
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
        first_token = secrets.token_urlsafe(16)
        with self._pool.connection() as conn:
            ref_code = next(code for code in iter(new_reference, None) if conn.execute("select 1 from requests where ref_code = %s", (code,)).fetchone() is None)
            conn.execute(
                "insert into requests (id, owner_user_id, original_text, need, notes, city, attributes, reply_token, created_at, ref_code)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (request_id, owner_user_id, original_text, need, notes, city_name, Jsonb(attributes), first_token, _now(), ref_code),
            )
            conn.cursor().executemany(
                "insert into request_recipients (request_id, seller_id, seller_name, ad_id, need, reply_token, send_status, listing_url)"
                " values (%s, %s, %s, %s, %s, %s, 'queued', %s)",
                [
                    (
                        request_id,
                        item.seller_id,
                        item.seller_name,
                        item.ad_id,
                        item.need or need,
                        item.reply_token or (first_token if index == 0 else secrets.token_urlsafe(16)),
                        item.listing_url,
                    )
                    for index, item in enumerate(recipients)
                ],
            )
            # The quote request is the first message on each item, routed to every seller on that item.
            for item_need in dict.fromkeys((item.need or need or "") for item in recipients):
                lines = ["طلب عرض سعر", (item_need or need or original_text or "").strip(), f"المدينة: {city_name}"]
                if notes and notes.strip():
                    lines.append(notes.strip())
                self._enqueue(
                    conn,
                    request_id,
                    "\n".join(line for line in lines if line),
                    item_need or None,
                    None,
                    None,
                    owner_user_id,
                    haraj_text=invite_text(item_need or need or original_text, city_name),
                )
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

    def _insert_message(
        self,
        conn,
        request_id: str,
        sender_role: str,
        sender_user_id: str | None,
        body: str,
        offer: Offer | None = None,
        seller_id: str | None = None,
        need: str | None = None,
        reply_to: str | None = None,
        scope: str | None = None,
        created_at: str | None = None,
        haraj_conversation_id: str | None = None,
        haraj_message_id: str | None = None,
        haraj_text: str | None = None,
        media: list[dict] | None = None,
    ) -> Message:
        message_id = uuid4().hex
        created = created_at or _now()
        conn.execute(
            "insert into messages (id, request_id, sender_role, sender_user_id, seller_id, need, reply_to, scope,"
            " haraj_conversation_id, haraj_message_id, haraj_text, body, offer_amount, offer_currency, attachment_ids, media, created_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                haraj_text,
                body,
                None if offer is None else offer.amount,
                None if offer is None else offer.currency,
                Jsonb([]),
                Jsonb(media or []),
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
            need=need,
            reply_to=reply_to,
            direction="seller_to_customer" if sender_role == "seller" else "customer_to_seller",
            scope=scope,
            haraj_conversation_id=haraj_conversation_id,
            body=body,
            offer=offer,
            created_at=created,
            media=media or [],
        )

    def add_message(
        self,
        request_id: str,
        sender_role: str,
        sender_user_id: str | None,
        body: str,
        offer: Offer | None = None,
        attachment_ids: list[str] | None = None,
        seller_id: str | None = None,
        **fields,
    ) -> Message:
        with self._pool.connection() as conn:
            return self._insert_message(conn, request_id, sender_role, sender_user_id, body, offer, seller_id=seller_id, **fields)

    def get_request(self, request_id: str, owner_user_id: str) -> RequestRecord | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from requests where id = %s and owner_user_id = %s", (request_id, owner_user_id)).fetchone()
            if row is None:
                return None
            recipients = [_recipient(item) for item in conn.execute("select * from request_recipients where request_id = %s order by id", (request_id,))]
            attachments = [
                Attachment(id=item["id"], filename=item["filename"], content_type=item["content_type"], size_bytes=item["size_bytes"])
                for item in conn.execute("select * from attachments where request_id = %s", (request_id,))
            ]
            messages = self._messages(conn, request_id)
            offers = self._offers(conn, request_id)
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
            offers=offers,
            reply_token=row["reply_token"],
            awarded_seller_id=row["awarded_seller_id"],
            awarded_at=_iso(row["awarded_at"]),
            last_synced_at=_iso(row["last_synced_at"]),
            ref_code=row.get("ref_code"),
            created_at=_iso(row["created_at"]),
        )

    def _messages(self, conn, request_id: str) -> list[Message]:
        deliveries: dict[str, list[dict]] = {}
        for item in conn.execute(
            "select message_id, seller_id, delivery_status, sent_at from message_deliveries where request_id = %s order by created_at", (request_id,)
        ):
            deliveries.setdefault(item["message_id"], []).append({"seller_id": item["seller_id"], "status": item["delivery_status"], "sent_at": _iso(item["sent_at"])})
        messages = []
        for item in conn.execute("select * from messages where request_id = %s order by created_at", (request_id,)):
            offer = None
            if item["offer_amount"] is not None:
                offer = Offer(amount=float(item["offer_amount"]), currency=item["offer_currency"])
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
                    attachment_ids=item["attachment_ids"],
                    created_at=_iso(item["created_at"]),
                    delivery_state=_delivery_state(routed) if routed else None,
                    deliveries=routed,
                    media=item["media"] or [],
                )
            )
        return messages

    def _offers(self, conn, request_id: str) -> list[Offer]:
        rows = current_offer_rows(conn.execute("select * from offers where request_id = %s order by created_at desc", (request_id,)).fetchall())
        return mark_cheapest(
            [
                Offer(
                    amount=_num(item["total_price"]),
                    currency=item["currency"] or "SAR",
                    note=item["message"],
                    provider_name=item["provider_name"],
                    phone=item["phone"],
                    seller_id=item["seller_id"],
                    base_price=_num(item["base_price"]),
                    delivery_included=item["delivery_included"],
                    delivery_price=_num(item["delivery_price"]),
                    total_price=_num(item["total_price"]),
                    need=item["need"],
                )
                for item in rows
            ]
        )

    def list_requests(self, owner_user_id: str) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute("select * from requests where owner_user_id = %s order by created_at desc", (owner_user_id,)).fetchall()
            items = []
            for row in rows:
                request_id = row["id"]
                recipients = [_recipient(item) for item in conn.execute("select * from request_recipients where request_id = %s order by id", (request_id,))]
                offers = self._offers(conn, request_id)
                messages = conn.execute(
                    "select sender_role, seller_id, body, offer_amount, offer_currency, created_at from messages where request_id = %s order by created_at",
                    (request_id,),
                ).fetchall()
                items.append(request_summary(row, recipients, offers, messages))
            return items

    def _request_by_token(self, token: str):
        with self._pool.connection() as conn:
            return conn.execute(
                "select r.* from requests r where r.reply_token = %s"
                " union all select r.* from requests r join request_recipients p on p.request_id = r.id where p.reply_token = %s limit 1",
                (token, token),
            ).fetchone()

    def seller_view(self, token: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select r.* from requests r where r.reply_token = %s"
                " union all select r.* from requests r join request_recipients p on p.request_id = r.id where p.reply_token = %s limit 1",
                (token, token),
            ).fetchone()
            if row is None:
                return None
            own = conn.execute("select * from request_recipients where reply_token = %s", (token,)).fetchone()
            if own is not None:
                recipients = [_recipient(own)]
                need = own["need"] or row["need"]
            else:
                recipients = [_recipient(item) for item in conn.execute("select * from request_recipients where request_id = %s order by id", (row["id"],))]
                need = row["need"]
            attachments = [
                {"id": item["id"], "filename": item["filename"], "content_type": item["content_type"], "size_bytes": item["size_bytes"]}
                for item in conn.execute("select * from attachments where request_id = %s", (row["id"],))
            ]
            messages = self._messages(conn, row["id"])
            sent = {
                item["id"]: item["haraj_text"]
                for item in conn.execute("select id, haraj_text from messages where request_id = %s and haraj_text is not null", (row["id"],))
            }
        seller_id = own["seller_id"] if own is not None else None
        awarded_to_me = row["awarded_seller_id"] is not None and seller_id is not None and seller_key(row["awarded_seller_id"]) == seller_key(seller_id)
        # The invite promises the item and the city only: the customer's own words and notes stay with him.
        # The phone and the place are here only when this supplier won AND the customer chose to share them.
        return {
            "request_id": row["id"],
            "ref_code": row.get("ref_code"),
            "need": need,
            "city": row["city"],
            "awarded_to_me": awarded_to_me,
            "contact": self.shared_contact(row["id"], seller_id) if awarded_to_me else None,
            "offers_open": row["awarded_seller_id"] in (None, seller_id),
            "recipients": [item.model_dump(mode="json") for item in recipients],
            "attachments": attachments,
            # Each seller sees their own thread, never another seller's messages or who else was asked.
            "messages": [seller_message(item, seller_id, sent.get(item.id)) for item in messages if visible_to_seller(item, seller_id, need)],
        }

    def add_seller_reply(self, token: str, seller_id: str | None, body: str, offer: Offer | None) -> Message:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select r.* from requests r where r.reply_token = %s"
                " union all select r.* from requests r join request_recipients p on p.request_id = r.id where p.reply_token = %s limit 1",
                (token, token),
            ).fetchone()
            if row is None:
                raise ValueError("request not found")
            own = conn.execute("select * from request_recipients where reply_token = %s", (token,)).fetchone()
            recipients = conn.execute("select * from request_recipients where request_id = %s order by id", (row["id"],)).fetchall()
            ids = [item["seller_id"] for item in recipients]
            if own is not None:
                seller_id = own["seller_id"]
            if seller_id is None:
                if len(ids) != 1:
                    raise ValueError("seller_id required")
                seller_id = ids[0]
            if seller_id not in ids:
                raise ValueError("unknown seller")
            matched = next(item for item in recipients if item["seller_id"] == seller_id)
            awarded = row["awarded_seller_id"]
            if offer is not None and awarded is not None and awarded != seller_id:
                raise ValueError("offers are closed: the customer has already chosen a supplier")
            if offer is not None:
                offer = priced_offer(offer, body, matched["seller_name"], matched["need"] or row["need"])
                # One current offer per supplier per item: a revised price replaces the earlier one.
                conn.execute("delete from offers where request_id = %s and seller_id = %s and coalesce(need, '') = %s", (row["id"], seller_id, offer.need or ""))
                conn.execute(
                    "insert into offers (id, request_id, seller_id, need, provider_name, phone, base_price, delivery_included, delivery_price, total_price, currency, message, created_at)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (uuid4().hex, row["id"], seller_id, offer.need, offer.provider_name, offer.phone, offer.base_price, offer.delivery_included,
                     offer.delivery_price, offer.total_price, offer.currency, offer.note, _now()),
                )
            text = body.strip() or ("عرض سعر" if offer is None else f"الإجمالي: {offer.total_price:g} ر.س")
            need = (offer.need if offer is not None else None) or matched["need"] or row["need"]
            message = self._insert_message(conn, row["id"], "seller", None, text, offer, seller_id=seller_id, need=need)
            conn.execute(
                "insert into notifications (id, user_id, request_id, kind, created_at) values (%s, %s, %s, %s, %s)",
                (uuid4().hex, row["owner_user_id"], row["id"], "seller_reply", message.created_at),
            )
        return message

    def attachment_path(self, request_id: str, attachment_id: str, owner_user_id: str | None = None, reply_token: str | None = None) -> tuple[Path, str, str] | None:
        with self._pool.connection() as conn:
            if owner_user_id is not None:
                allowed = conn.execute("select 1 from requests where id = %s and owner_user_id = %s", (request_id, owner_user_id)).fetchone()
            elif reply_token is not None:
                allowed = conn.execute(
                    "select 1 from requests where id = %s and reply_token = %s"
                    " union all select 1 from request_recipients where request_id = %s and reply_token = %s",
                    (request_id, reply_token, request_id, reply_token),
                ).fetchone()
            else:
                return None
            if allowed is None:
                return None
            row = conn.execute("select * from attachments where id = %s and request_id = %s", (attachment_id, request_id)).fetchone()
        if row is None:
            return None
        return Path(row["path"]), row["content_type"], row["filename"]

    # -- router: customer -> Haraj conversations --------------------------------

    def _enqueue(
        self,
        conn,
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
        recipients = conn.execute("select * from request_recipients where request_id = %s order by id", (request_id,)).fetchall()
        default_need = conn.execute("select need from requests where id = %s", (request_id,)).fetchone()["need"]
        targets, need, scope = route_targets(recipients, default_need, need, seller_id, seller_ids)
        single = targets[0]["seller_id"] if scope == SINGLE_SELLER else None
        message = self._insert_message(
            conn, request_id, "user", owner_user_id, body, None, seller_id=single, need=need, reply_to=reply_to, scope=scope, haraj_text=haraj_text, media=media
        )
        created = _now()
        in_app = self._registered_sellers(conn, [row["seller_id"] for row in targets])
        for row in targets:
            item_need = row["need"] or default_need or ""
            conn.execute(
                "insert into haraj_threads (request_id, seller_id, need, ad_id) values (%s, %s, %s, %s) on conflict do nothing",
                (request_id, row["seller_id"], item_need, row["ad_id"]),
            )
            # A registered supplier reads this in his own app, so it is not a Haraj send: it
            # skips the queue entirely and never spends a slot of the platform-wide 20-second
            # spacing. The worker claims 'queued' only, so 'in_app' is invisible to it.
            direct = seller_key(row["seller_id"]) in in_app
            conn.execute(
                "insert into message_deliveries (id, message_id, request_id, seller_id, need, delivery_status, sent_at, created_at)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid4().hex, message.id, request_id, row["seller_id"], item_need,
                 "in_app" if direct else "queued", created if direct else None, created),
            )
            if direct:
                conn.execute(
                    "update request_recipients set send_status = 'sent' where request_id = %s and seller_id = %s and coalesce(need, '') = %s",
                    (request_id, row["seller_id"], item_need),
                )
        return message

    def _registered_sellers(self, conn, seller_ids) -> set[str]:
        """Which of these Haraj sellers already have an active Taseer account."""
        keys = sorted({seller_key(item) for item in seller_ids if item})
        if not keys:
            return set()
        rows = conn.execute(
            "select haraj_seller_id from suppliers where status = 'active' and haraj_seller_id = any(%s)", (keys,)
        ).fetchall()
        return {row["haraj_seller_id"] for row in rows}

    # -- supplier accounts ----------------------------------------------------

    def _supplier_row(self, row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "phone": row["phone"],
            "activity_type": row["activity_type"],
            "description": row["description"],
            "categories": row["categories"] or [],
            "haraj_seller_id": row["haraj_seller_id"],
            "status": row["status"],
            "created_at": _iso(row["created_at"]),
        }

    def seller_id_for_reply_token(self, token: str) -> str | None:
        """The Haraj seller an invite link already proves. Registration binds to this and
        never to a seller id the account asks for."""
        if not token:
            return None
        with self._pool.connection() as conn:
            row = conn.execute("select seller_id from request_recipients where reply_token = %s", (token,)).fetchone()
        return None if row is None else seller_key(row["seller_id"])

    def register_supplier(self, *, name: str, email: str, phone: str, password: str,
                          activity_type: str = "both", description: str | None = None,
                          categories=None, haraj_seller_id: str | None = None) -> dict:
        supplier_id = uuid4().hex
        salt = secrets.token_hex(16)
        bound = seller_key(haraj_seller_id) if haraj_seller_id else None
        with self._pool.connection() as conn:
            if bound and conn.execute("select 1 from suppliers where haraj_seller_id = %s", (bound,)).fetchone():
                raise ValueError("seller already registered")
            try:
                conn.execute(
                    "insert into suppliers (id, name, email, phone, password_hash, salt, activity_type, description,"
                    " categories, haraj_seller_id, status, created_at, updated_at)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (supplier_id, name.strip(), email.lower().strip(), phone.strip(),
                     _hash_password(password, salt), salt, activity_type, (description or "").strip() or None,
                     Jsonb(list(categories or [])), bound, "active" if bound else "pending", _now(), _now()),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError("email already registered") from exc
            row = conn.execute("select * from suppliers where id = %s", (supplier_id,)).fetchone()
        return self._supplier_row(row)

    def login_supplier(self, email: str, password: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from suppliers where email = %s", (email.lower().strip(),)).fetchone()
        if not password_matches(password, row):
            return None
        token = secrets.token_urlsafe(32)
        with self._pool.connection() as conn:
            conn.execute(
                "insert into supplier_sessions (token, supplier_id, created_at) values (%s, %s, %s)",
                (token_digest(token), row["id"], _now()),
            )
            conn.execute("delete from supplier_sessions where supplier_id = %s and created_at < %s", (row["id"], session_cutoff()))
        return token

    def supplier_for_token(self, token: str) -> dict | None:
        if not token:
            return None
        keys = list(session_keys(token))
        with self._pool.connection() as conn:
            row = conn.execute(
                "select s.token as session_token, s.created_at as session_created, p.* from supplier_sessions s"
                " join suppliers p on p.id = s.supplier_id where s.token = any(%s)",
                (keys,),
            ).fetchone()
            if row is None:
                return None
            if row["session_created"] < session_cutoff():
                conn.execute("delete from supplier_sessions where token = %s", (row["session_token"],))
                return None
        return self._supplier_row(row)

    def logout_supplier(self, token: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("delete from supplier_sessions where token = any(%s)", (list(session_keys(token)),))

    def claim_supplier_link(self, supplier_id: str, reply_token: str) -> dict | None:
        """Bind a pending account to the Haraj seller an invite link proves."""
        bound = self.seller_id_for_reply_token(reply_token)
        if bound is None:
            return None
        with self._pool.connection() as conn:
            taken = conn.execute("select id from suppliers where haraj_seller_id = %s", (bound,)).fetchone()
            if taken is not None and taken["id"] != supplier_id:
                raise ValueError("seller already registered")
            conn.execute(
                "update suppliers set haraj_seller_id = %s, status = 'active', updated_at = %s where id = %s",
                (bound, _now(), supplier_id),
            )
            row = conn.execute("select * from suppliers where id = %s", (supplier_id,)).fetchone()
        return None if row is None else self._supplier_row(row)

    def supplier_requests(self, supplier_id: str) -> list[dict]:
        """Every request this supplier was written to, newest first, with the state the
        list screen colours by: new, quoted, awarded to him, or awarded to someone else."""
        with self._pool.connection() as conn:
            supplier = conn.execute("select haraj_seller_id, status from suppliers where id = %s", (supplier_id,)).fetchone()
            if supplier is None or supplier["status"] != "active" or not supplier["haraj_seller_id"]:
                return []
            seller = supplier["haraj_seller_id"]
            rows = conn.execute(
                """
                select r.id, r.ref_code, r.city, r.created_at, r.awarded_seller_id, r.contact_shared_at,
                       p.need, p.reply_token, p.seller_id,
                       (select min(o.total_price) from offers o
                         where o.request_id = r.id and o.seller_id = p.seller_id) as my_offer,
                       (select count(*) from messages m
                         where m.request_id = r.id and m.sender_role = 'seller' and m.seller_id = p.seller_id) as my_messages
                  from request_recipients p join requests r on r.id = p.request_id
                 where p.seller_id = %s or p.seller_id = %s
                 order by r.created_at desc
                 limit 100
                """,
                (seller, f"haraj:seller:{seller}"),
            ).fetchall()
        out = []
        for row in rows:
            awarded = row["awarded_seller_id"]
            mine = awarded is not None and seller_key(awarded) == seller
            if awarded is not None:
                state = "awarded" if mine else "lost"
            elif row["my_offer"] is not None:
                state = "quoted"
            else:
                state = "new"
            out.append({
                "request_id": row["id"],
                "ref_code": row["ref_code"],
                "need": row["need"],
                "city": row["city"],
                "created_at": _iso(row["created_at"]),
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
        """The customer's own phone and place, given to the winning supplier by his own
        action. Refused before a winner exists, so it can never leak to a losing bidder."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "select awarded_seller_id from requests where id = %s and owner_user_id = %s", (request_id, owner_user_id)
            ).fetchone()
            if row is None:
                raise LookupError("request not found")
            if not row["awarded_seller_id"]:
                raise ValueError("award a supplier first")
            conn.execute(
                "update requests set contact_phone = %s, contact_lat = %s, contact_lng = %s, contact_shared_at = %s where id = %s",
                (phone.strip(), lat, lng, _now(), request_id),
            )
        return {"shared": True, "phone": phone.strip(), "lat": lat, "lng": lng}

    def revoke_contact(self, request_id: str, owner_user_id: str) -> dict:
        with self._pool.connection() as conn:
            updated = conn.execute(
                "update requests set contact_phone = null, contact_lat = null, contact_lng = null, contact_shared_at = null"
                " where id = %s and owner_user_id = %s returning id",
                (request_id, owner_user_id),
            ).fetchone()
            if updated is None:
                raise LookupError("request not found")
        return {"shared": False}

    def shared_contact(self, request_id: str, seller_id: str) -> dict | None:
        """Only the awarded supplier, and only once the customer has shared."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "select awarded_seller_id, contact_phone, contact_lat, contact_lng, contact_shared_at"
                " from requests where id = %s",
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
        with self._pool.connection() as conn:
            if conn.execute("select 1 from requests where id = %s and owner_user_id = %s", (request_id, owner_user_id)).fetchone() is None:
                raise LookupError("request not found")
            media = []
            for file_id in media_ids or []:
                row = conn.execute(
                    "select id, content_type, filename, size_bytes, width, height from files where id = %s and owner_user_id = %s and request_id = %s",
                    (file_id, owner_user_id, request_id),
                ).fetchone()
                if row is None:
                    raise ValueError("unknown file")
                media.append(media_entry(row))
            if not body.strip() and not media:
                raise ValueError("message is required")
            if reply_to:
                quoted = conn.execute("select * from messages where id = %s and request_id = %s", (reply_to, request_id)).fetchone()
                if quoted is None:
                    raise ValueError("unknown reply_to")
                delivered = [item["seller_id"] for item in conn.execute("select seller_id from message_deliveries where message_id = %s order by created_at", (reply_to,))]
                seller_id, seller_ids = reply_audience(quoted["seller_id"], delivered, seller_id, seller_ids)
                need = need or quoted["need"]
            message = self._enqueue(conn, request_id, body, need, seller_id, reply_to, owner_user_id, seller_ids=seller_ids, media=media)
            return next(item for item in self._messages(conn, request_id) if item.id == message.id)

    def save_file(self, owner_user_id: str, request_id: str, content_type: str, filename: str, data: bytes, width: int | None = None, height: int | None = None) -> dict:
        file_id = uuid4().hex
        name = Path(filename).name or "file"
        with self._pool.connection() as conn:
            if conn.execute("select 1 from requests where id = %s and owner_user_id = %s", (request_id, owner_user_id)).fetchone() is None:
                raise LookupError("request not found")
            conn.execute(
                "insert into files (id, owner_user_id, request_id, content_type, filename, size_bytes, width, height, data) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (file_id, owner_user_id, request_id, content_type, name, len(data), width, height, data),
            )
        return media_entry({"id": file_id, "content_type": content_type, "filename": name, "size_bytes": len(data), "width": width, "height": height})

    def get_file(self, file_id: str) -> dict | None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from files where id = %s", (file_id,)).fetchone()
        return None if row is None else {**dict(row), "data": bytes(row["data"])}

    def claim_deliveries(self, limit: int = 50) -> list[dict]:
        # SKIP LOCKED: two instances never claim the same delivery.
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                update message_deliveries d set delivery_status = 'sending', attempts = d.attempts + 1, last_attempt_at = now()
                where d.id in (
                  select id from message_deliveries where delivery_status = 'queued' order by created_at limit %s for update skip locked
                )
                returning d.id, d.request_id, d.seller_id, d.need, d.message_id, d.created_at
                """,
                (limit,),
            ).fetchall()
            claimed = []
            for row in sorted(rows, key=lambda item: item["created_at"]):
                detail = conn.execute(
                    """
                    select coalesce(m.haraj_text, m.body) as body, m.media, t.ad_id, t.haraj_conversation_id,
                      (select r.reply_token from request_recipients r where r.request_id = %s and r.seller_id = %s
                       order by coalesce(r.need, '') = %s desc, r.id limit 1) as reply_token,
                      (select q.ref_code from requests q where q.id = t.request_id) as ref_code
                    from messages m join haraj_threads t on t.request_id = %s and t.seller_id = %s and t.need = %s
                    where m.id = %s
                    """,
                    (row["request_id"], row["seller_id"], row["need"], row["request_id"], row["seller_id"], row["need"], row["message_id"]),
                ).fetchone()
                claimed.append({"id": row["id"], "request_id": row["request_id"], "seller_id": row["seller_id"], "need": row["need"], **(detail or {})})
            return claimed

    def finish_delivery(self, delivery_id: str, sent: SentMessage | None = None, error: str | None = None, retry: bool = False) -> None:
        with self._pool.connection() as conn:
            row = conn.execute("select * from message_deliveries where id = %s", (delivery_id,)).fetchone()
            if row is None:
                return
            key = (row["request_id"], row["seller_id"], row["need"])
            if sent is not None:
                conn.execute(
                    "update message_deliveries set delivery_status = 'sent', haraj_message_id = %s, sent_at = now(), error = null where id = %s",
                    (sent.haraj_message_id, delivery_id),
                )
                # Replies are read from our first message on: older history in the conversation is not imported.
                conn.execute(
                    "update haraj_threads set haraj_conversation_id = %s, high_water = coalesce(high_water, %s) where request_id = %s and seller_id = %s and need = %s",
                    (sent.haraj_conversation_id, sent.seq, *key),
                )
                status = "sent"
            else:
                status = "queued" if retry else "failed"
                conn.execute("update message_deliveries set delivery_status = %s, error = %s where id = %s", (status, error, delivery_id))
            if status != "queued":
                conn.execute(
                    "update request_recipients set send_status = %s where request_id = %s and seller_id = %s and coalesce(need, '') in (%s, '') and coalesce(send_status, '') != 'sent'",
                    (status, *key),
                )

    def delivery_attempts(self, delivery_id: str) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("select attempts from message_deliveries where id = %s", (delivery_id,)).fetchone()
        return 0 if row is None else row["attempts"]

    # -- sync: Haraj -> item conversation ---------------------------------------

    def threads_to_sync(self, limit: int = 200, now: float | None = None) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select * from haraj_threads where haraj_conversation_id is not null and (retry_at is null or retry_at <= coalesce(to_timestamp(%s), now()))"
                " order by checked_at nulls first limit %s",
                (now, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def thread_checked(self, thread: dict, failure_code: str | None = None, retry_seconds: int = 30, now: float | None = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "update haraj_threads set checked_at = coalesce(to_timestamp(%s), now()), retry_at = coalesce(to_timestamp(%s), now()) + make_interval(secs => %s),"
                " failure_code = %s where request_id = %s and seller_id = %s and need = %s",
                (now, now, retry_seconds, failure_code, thread["request_id"], thread["seller_id"], thread["need"]),
            )

    def reserve_send_slot(self, spacing: float, now: float, deadline: float) -> float | None:
        """Book the next send time for every instance at once. The row lock serialises concurrent bookers."""
        with self._pool.connection() as conn:
            conn.execute("insert into haraj_channel (key, value) values ('next_send_at', '0') on conflict (key) do nothing")
            row = conn.execute(
                "update haraj_channel set value = (greatest(coalesce(nullif(value, '')::float8, 0), %(now)s) + %(spacing)s)::text, updated_at = now()"
                " where key = 'next_send_at' and greatest(coalesce(nullif(value, '')::float8, 0), %(now)s) <= %(deadline)s"
                " returning value::float8 - %(spacing)s as slot",
                {"now": now, "spacing": spacing, "deadline": deadline},
            ).fetchone()
        return None if row is None else float(row["slot"])

    def release_send_slot(self, slot: float, spacing: float) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "update haraj_channel set value = %s::text where key = 'next_send_at' and value::float8 = %s",
                (slot, slot + spacing),
            )

    def _inbound_candidates(self, conn, conversation_id: str) -> list[dict]:
        rows = conn.execute(
            "select t.*, r.owner_user_id, r.ref_code, r.awarded_seller_id, r.created_at as request_created_at,"
            " coalesce((select array_agg(d.sent_at) from message_deliveries d where d.request_id = t.request_id and d.seller_id = t.seller_id"
            " and d.need = t.need and d.delivery_status = 'sent' and d.sent_at is not null), '{}') as sends"
            " from haraj_threads t join requests r on r.id = t.request_id where t.haraj_conversation_id = %s",
            (conversation_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def thread_for_inbound(self, conversation_id: str, sent_at: str, body: str = "") -> dict | None:
        """The request a seller's reply belongs to (see store.choose_thread); None when it cannot be told apart."""
        with self._pool.connection() as conn:
            candidates = self._inbound_candidates(conn, conversation_id)
        return choose_thread(candidates, body, sent_at)

    def record_unmatched_inbound(self, conversation_id: str, seller_id: str, inbound: InboundMessage) -> list[str]:
        """Keep a reply no request can safely claim, out of every customer's view. Returns the requests it could belong to."""
        with self._pool.connection() as conn:
            candidates = sorted({row["request_id"] for row in self._inbound_candidates(conn, conversation_id)})
            conn.execute(
                "insert into haraj_unmatched (haraj_message_id, haraj_conversation_id, seller_id, body, media, sent_at, candidate_request_ids)"
                " values (%s, %s, %s, %s, %s, %s::timestamptz, %s) on conflict (haraj_message_id) do nothing",
                (inbound.haraj_message_id, conversation_id, seller_id, inbound.body, Jsonb(list(inbound.media)), inbound.sent_at, Jsonb(candidates)),
            )
        return candidates

    def unmatched_inbound(self, conversation_id: str | None = None) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select * from haraj_unmatched where %s::text is null or haraj_conversation_id = %s order by sent_at", (conversation_id, conversation_id)
            ).fetchall()
        return [{**dict(row), "candidate_request_ids": list(row["candidate_request_ids"] or [])} for row in rows]

    # -- limits: who may be asked, and how often ----------------------------------

    def record_search_sellers(self, trace_id: str, user_id: str | None, seller_ids) -> None:
        rows = [(trace_id, user_id, seller_id) for seller_id in seller_ids]
        if not rows:
            return
        with self._pool.connection() as conn:
            conn.cursor().executemany(
                "insert into search_sellers (trace_id, user_id, seller_id) values (%s, %s, %s)"
                " on conflict (trace_id, seller_id) do update set user_id = coalesce(search_sellers.user_id, excluded.user_id)",
                rows,
            )

    def searched_sellers(self, user_id: str, trace_id: str | None, since: str) -> set[str]:
        """Sellers this user's searches showed since then, and those of an anonymous search he names."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select seller_id from search_sellers where (user_id = %s and created_at >= %s::timestamptz)"
                " or (trace_id = %s and (user_id is null or user_id = %s))",
                (user_id, since, trace_id, user_id),
            ).fetchall()
        return {row["seller_id"] for row in rows}

    def count_requests(self, user_id: str, since: str | None = None) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) as count from requests where owner_user_id = %s and (%s::timestamptz is null or created_at >= %s::timestamptz)",
                (user_id, since, since),
            ).fetchone()
        return int(row["count"])

    def count_items(self, user_id: str, since: str | None = None) -> int:
        """Items, not requests: one request carries a distinct need per item, and the quota is
        sold per item. Counting requests here would let ten items inside one request cost one."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) as count from ("
                " select distinct rr.request_id, coalesce(rr.need, '') from request_recipients rr"
                " join requests r on r.id = rr.request_id"
                " where r.owner_user_id = %s and (%s::timestamptz is null or r.created_at >= %s::timestamptz)"
                ") t",
                (user_id, since, since),
            ).fetchone()
        return int(row["count"])

    def count_contacts(self, user_id: str, since: str | None = None) -> int:
        """Supplier contacts: what actually consumes the shared Haraj send capacity."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) as count from request_recipients rr join requests r on r.id = rr.request_id"
                " where r.owner_user_id = %s and (%s::timestamptz is null or r.created_at >= %s::timestamptz)",
                (user_id, since, since),
            ).fetchone()
        return int(row["count"])

    def count_customer_messages(self, user_id: str, since: str) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select count(*) as count from messages m join requests r on r.id = m.request_id"
                " where r.owner_user_id = %s and m.sender_role = 'user' and m.created_at >= %s::timestamptz",
                (user_id, since),
            ).fetchone()
        return int(row["count"])

    def conversation_checked(self, conversation_id: str, failure_code: str | None = None, retry_seconds: int = 30, now: float | None = None, high_water: int | None = None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "update haraj_threads set checked_at = coalesce(to_timestamp(%s), now()), retry_at = coalesce(to_timestamp(%s), now()) + make_interval(secs => %s),"
                " failure_code = %s, high_water = greatest(coalesce(high_water, 0), %s) where haraj_conversation_id = %s",
                (now, now, retry_seconds, failure_code, high_water or 0, conversation_id),
            )

    def has_haraj_message(self, haraj_message_id: str) -> bool:
        with self._pool.connection() as conn:
            return (
                conn.execute(
                    "select 1 from messages where haraj_message_id = %s union all select 1 from haraj_unmatched where haraj_message_id = %s",
                    (haraj_message_id, haraj_message_id),
                ).fetchone()
                is not None
            )

    def save_file_for_request(self, request_id: str, content_type: str, filename: str, data: bytes, width: int | None = None, height: int | None = None) -> dict:
        with self._pool.connection() as conn:
            owner = conn.execute("select owner_user_id from requests where id = %s", (request_id,)).fetchone()
        return self.save_file(owner["owner_user_id"], request_id, content_type, filename, data, width, height)

    def get_value(self, key: str) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("select value from haraj_channel where key = %s", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_value(self, key: str, value: str | None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "insert into haraj_channel (key, value, updated_at) values (%s, %s, now())"
                " on conflict (key) do update set value = excluded.value, updated_at = excluded.updated_at",
                (key, value),
            )

    def record_inbound(self, thread: dict, inbound: InboundMessage) -> Message | None:
        """Attach a Haraj reply to its item and seller. Returns None when it was already recorded."""
        request_id, seller_id, need = thread["request_id"], thread["seller_id"], thread["need"] or None
        with self._pool.connection() as conn:
            advance = (
                "update haraj_threads set last_fetched_at = now(), high_water = greatest(coalesce(high_water, 0), %s)"
                " where request_id = %s and seller_id = %s and need = %s"
            )
            if conn.execute("select 1 from messages where haraj_message_id = %s", (inbound.haraj_message_id,)).fetchone():
                conn.execute(advance, (inbound.seq, request_id, seller_id, thread["need"]))
                return None
            recipient = conn.execute(
                "select * from request_recipients where request_id = %s and seller_id = %s order by coalesce(need, '') = %s desc, id limit 1",
                (request_id, seller_id, need or ""),
            ).fetchone()
            offer = None
            quote = extract_quote(inbound.body)
            price = None if quote is None else quote.total
            awarded = conn.execute("select awarded_seller_id from requests where id = %s", (request_id,)).fetchone()["awarded_seller_id"]
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
                conn.execute("delete from offers where request_id = %s and seller_id = %s and coalesce(need, '') = %s", (request_id, seller_id, need or ""))
                conn.execute(
                    "insert into offers (id, request_id, seller_id, need, provider_name, base_price, delivery_included, delivery_price, total_price, currency, message, created_at)"
                    " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'SAR', %s, now())",
                    (uuid4().hex, request_id, seller_id, need, offer.provider_name, quote.base, quote.delivery_included, quote.delivery_price, price, inbound.body),
                )
            message = self._insert_message(
                conn,
                request_id,
                "seller",
                None,
                inbound.body,
                offer,
                seller_id=seller_id,
                need=need,
                scope="single_seller",
                created_at=inbound.sent_at,
                haraj_conversation_id=thread["haraj_conversation_id"],
                haraj_message_id=inbound.haraj_message_id,
                media=list(inbound.media),
            )
            owner = conn.execute("select owner_user_id from requests where id = %s", (request_id,)).fetchone()
            conn.execute(
                "insert into notifications (id, user_id, request_id, kind, created_at) values (%s, %s, %s, %s, %s)",
                (uuid4().hex, owner["owner_user_id"], request_id, "seller_reply", message.created_at),
            )
            conn.execute(advance, (inbound.seq, request_id, seller_id, thread["need"]))
        return message

    AWARD_TEXT = "تم اختيار عرضك. سنتواصل معك لإكمال التفاصيل."

    def award(self, request_id: str, owner_user_id: str, seller_id: str, notify: bool = True) -> Message | None:
        with self._pool.connection() as conn:
            known = conn.execute(
                "select 1 from request_recipients r join requests q on q.id = r.request_id where r.request_id = %s and r.seller_id = %s and q.owner_user_id = %s",
                (request_id, seller_id, owner_user_id),
            ).fetchone()
            if known is None:
                raise ValueError("unknown seller")
            current = conn.execute("select awarded_seller_id from requests where id = %s", (request_id,)).fetchone()["awarded_seller_id"]
            if current == seller_id:
                return None
            # Only the first award counts; a second one for another supplier is refused, not swapped in.
            changed = conn.execute(
                "update requests set awarded_seller_id = %s, awarded_at = now() where id = %s and owner_user_id = %s and awarded_seller_id is null",
                (seller_id, request_id, owner_user_id),
            ).rowcount
            if not changed:
                raise AwardConflict("request already awarded to another supplier")
        if not notify:
            return None
        return self.route_customer_message(request_id, owner_user_id, self.AWARD_TEXT, seller_id=seller_id)

    def mark_read(self, request_id: str, owner_user_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("update requests set customer_read_at = now() where id = %s and owner_user_id = %s", (request_id, owner_user_id))

    def recipient_name(self, request_id: str, seller_id: str | None) -> str | None:
        with self._pool.connection() as conn:
            row = conn.execute("select seller_name from request_recipients where request_id = %s and seller_id = %s limit 1", (request_id, seller_id)).fetchone()
        return None if row is None else row["seller_name"]

    def add_push_subscription(self, user_id: str, endpoint: str, p256dh: str, auth: str) -> bool:
        """A device endpoint stays with the account that registered it. Returns False, and
        changes nothing, when another account already holds it."""
        with self._pool.connection() as conn:
            cur = conn.execute(
                "insert into push_subscriptions (endpoint, user_id, p256dh, auth) values (%s, %s, %s, %s)"
                " on conflict (endpoint) do update set p256dh = excluded.p256dh, auth = excluded.auth"
                " where push_subscriptions.user_id = excluded.user_id",
                (endpoint, user_id, p256dh, auth),
            )
            return cur.rowcount > 0

    def remove_push_subscription(self, endpoint: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("delete from push_subscriptions where endpoint = %s", (endpoint,))

    def push_subscriptions_for_request(self, request_id: str) -> list[dict]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "select s.* from push_subscriptions s join requests r on r.owner_user_id = s.user_id where r.id = %s", (request_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_synced(self, request_id: str | None = None) -> None:
        with self._pool.connection() as conn:
            if request_id:
                conn.execute("update requests set last_synced_at = now() where id = %s", (request_id,))
            else:
                conn.execute("update requests set last_synced_at = now()")

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
            "monthly_items": row["monthly_items"],
            "sellers_per_item": row["sellers_per_item"],
            "daily_contacts": row["daily_contacts"],
        }

    def _subscription_row(self, row) -> dict:
        return {
            "id": str(row["id"]),
            "user_id": row["user_id"],
            "plan": row["plan"],
            "status": row["status"],
            "starts_at": _iso(row["starts_at"]),
            "expires_at": _iso(row["expires_at"]),
            "period_anchor": _iso(row["period_anchor"]) or _iso(row["starts_at"]) or _iso(row["created_at"]),
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
            "provider_status": row.get("provider_status"),
            "refunded_amount": row.get("refunded_amount"),
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
        """Same rules as Store.settle_payment: only a final Moyasar state moves the row;
        while 3-D Secure runs the row stays 'payment_pending' with provider_status kept."""
        with self._pool.connection() as conn:
            payment = self._lock_user_for_settlement(conn, payment_id)
            if payment is None:
                return {"ok": False, "reason": "payment_not_found"}
            decision = settle_decision(payment, provider_payment_id, provider_status, paid_amount, paid_currency)
            if decision in ("done", "same"):
                return {
                    "ok": True,
                    "already_processed": True,
                    "status": payment["status"],
                    "activated": False,
                    "subscription": self.get_latest_subscription(payment["user_id"]),
                }
            if decision == "mismatch":
                conn.execute(
                    "update payments set status = 'failed', provider_payment_id = %s, provider_status = %s where id = %s",
                    (provider_payment_id, provider_status, payment_id),
                )
                return {"ok": True, "already_processed": False, "status": "failed", "activated": False, "reason": "amount_mismatch"}
            if decision == "closed":
                conn.execute(
                    "update payments set status = %s, provider_payment_id = %s, provider_status = %s where id = %s",
                    (provider_status, provider_payment_id, provider_status, payment_id),
                )
                return {"ok": True, "already_processed": False, "status": provider_status, "activated": False}
            if decision == "pending":
                conn.execute(
                    "update payments set status = 'payment_pending', provider_payment_id = %s, provider_status = %s where id = %s",
                    (provider_payment_id, provider_status, payment_id),
                )
                return {"ok": True, "already_processed": False, "status": "payment_pending", "provider_status": provider_status, "pending": True, "activated": False}

            plan = conn.execute("select * from subscription_plans where code = %s", (payment["plan"],)).fetchone()
            if plan is None:
                conn.execute(
                    "update payments set status = 'failed', provider_payment_id = %s, provider_status = %s where id = %s",
                    (provider_payment_id, provider_status, payment_id),
                )
                return {"ok": True, "already_processed": False, "status": "failed", "activated": False, "reason": "unknown_plan"}

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
                # Renewing the same plan keeps the monthly rhythm; changing plan starts the new
                # allowance now rather than part-way through the old plan's cycle.
                anchor = latest["period_anchor"] if latest["plan"] == plan["code"] and latest["period_anchor"] else now
                conn.execute(
                    "update subscriptions set plan = %s, status = 'active', expires_at = %s, period_anchor = %s, updated_at = %s where id = %s",
                    (plan["code"], expires_at, anchor, now, subscription_id),
                )
            else:
                subscription_id = uuid4()
                conn.execute(
                    "insert into subscriptions (id, user_id, plan, status, starts_at, expires_at, period_anchor, created_at, updated_at)"
                    " values (%s, %s, %s, 'active', %s, %s, %s, %s, %s)",
                    (subscription_id, payment["user_id"], plan["code"], now, expires_at, now, now, now),
                )
            conn.execute(
                "update payments set status = 'paid', provider_payment_id = %s, provider_status = %s, subscription_id = %s where id = %s",
                (provider_payment_id, provider_status, subscription_id, payment_id),
            )
        return {
            "ok": True,
            "already_processed": False,
            "status": "paid",
            "activated": True,
            "subscription": self.get_latest_subscription(payment["user_id"]),
        }

    def refund_payment(self, payment_id: str, provider_payment_id: str, refunded_amount: int | None = None) -> dict:
        """Same rules as Store.refund_payment: a full refund takes back only the term that
        payment bought (cancelling when nothing paid is left); a partial refund is recorded
        and leaves the entitlement alone; a refund before settlement never grants anything."""
        with self._pool.connection() as conn:
            payment = self._lock_user_for_settlement(conn, payment_id)
            if payment is None:
                return {"ok": False, "reason": "payment_not_found"}
            if payment["status"] == "refunded":
                return {"ok": True, "already_processed": True, "status": "refunded", "activated": False, "subscription": self.get_latest_subscription(payment["user_id"])}
            amount = payment["amount"] if refunded_amount is None else refunded_amount
            settled = payment["status"] in ("paid", "partially_refunded") and payment["subscription_id"]
            if settled and amount < payment["amount"]:
                if payment["status"] == "partially_refunded" and (payment.get("refunded_amount") or 0) >= amount:
                    return {"ok": True, "already_processed": True, "status": "partially_refunded", "activated": False, "subscription": self.get_latest_subscription(payment["user_id"])}
                conn.execute(
                    "update payments set status = 'partially_refunded', refunded_amount = %s, provider_payment_id = %s where id = %s",
                    (amount, provider_payment_id, payment_id),
                )
                partial = True
            else:
                partial = False
                if settled:
                    sub = conn.execute("select * from subscriptions where id = %s", (payment["subscription_id"],)).fetchone()
                    plan = conn.execute("select * from subscription_plans where code = %s", (payment["plan"],)).fetchone()
                    if sub is not None and sub["status"] == "active":
                        now = datetime.now(timezone.utc)
                        expires = sub["expires_at"] or now
                        remaining = expires - timedelta(days=plan["duration_days"] if plan else 0)
                        if plan is None or remaining <= now:
                            conn.execute(
                                "update subscriptions set status = 'cancelled', updated_at = %s where id = %s",
                                (now, payment["subscription_id"]),
                            )
                        else:
                            conn.execute(
                                "update subscriptions set expires_at = %s, updated_at = %s where id = %s",
                                (remaining, now, payment["subscription_id"]),
                            )
                conn.execute(
                    "update payments set status = 'refunded', refunded_amount = %s, provider_payment_id = %s where id = %s",
                    (amount, provider_payment_id, payment_id),
                )
        return {
            "ok": True,
            "already_processed": False,
            "status": "partially_refunded" if partial else "refunded",
            "activated": False,
            "subscription": self.get_latest_subscription(payment["user_id"]),
        }


def _num(value) -> float | None:
    return None if value is None else float(value)


def _recipient(item) -> RequestRecipient:
    return RequestRecipient(
        seller_id=item["seller_id"],
        seller_name=item["seller_name"],
        ad_id=item["ad_id"],
        need=item.get("need"),
        reply_token=item.get("reply_token"),
        send_status=item.get("send_status") or "sent",
        listing_url=item.get("listing_url"),
    )


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
