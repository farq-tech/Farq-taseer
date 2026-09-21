"""Consumer persistence. Separate from FARQ Construction procurement tables."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

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
            """
        )
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
        request_id = uuid4().hex
        self._connection.execute(
            "insert into requests (id, owner_user_id, original_text, need, notes, city, attributes_json, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, owner_user_id, original_text, need, notes, city, json.dumps(attributes, ensure_ascii=False), _now()),
        )
        self._connection.executemany(
            "insert into request_recipients (request_id, seller_id, seller_name, ad_id) values (?, ?, ?, ?)",
            [(request_id, item.seller_id, item.seller_name, item.ad_id) for item in recipients],
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
    ) -> Message:
        message_id = uuid4().hex
        created = _now()
        self._connection.execute(
            "insert into messages (id, request_id, sender_role, sender_user_id, body, offer_amount, offer_currency, attachment_ids_json, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message_id,
                request_id,
                sender_role,
                sender_user_id,
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
                    body=item["body"],
                    offer=offer,
                    attachment_ids=json.loads(item["attachment_ids_json"]),
                    created_at=item["created_at"],
                )
            )
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
            created_at=row["created_at"],
        )
