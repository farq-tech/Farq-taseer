"""Haraj chat channel. Sellers only ever talk to us inside Haraj.

Taseer shows the customer one conversation per item; every outgoing message is
routed to the Haraj conversation of each targeted seller, and seller replies are
pulled back by the sync worker.

The transport is the one Farq Construction uses (farq repo:
api/lib/construction/haraj-session.js, haraj-invite.js, haraj-chat.js,
haraj-inbox.js; docs/construction/HARAJ_DISPATCH_AND_QUOTE_TOTALS.md), ported
as-is.

Accounts (2026-10-04). Taseer sends only from its own Haraj account, set as
HARAJ_TASEER_USER_ID / _USERNAME / _PASSWORD / _REFRESH_TOKEN. The older account
(HARAJ_USER_ID and the unprefixed HARAJ_* settings) is shared with Farq
Construction: Taseer keeps reading the conversations it already has there, and
never sends from it again. Each account keeps its tokens under its own cache key.

The seller's address is the Haraj author id only (``haraj:seller:19676360`` ->
``19676360``). No phone number is looked up, and ``postContact`` is never used.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import httpx

log = logging.getLogger("farq.haraj_chat")

CHAT_ENDPOINT = "https://api-chat.haraj.com.sa"
CHAT_SOCKET = "wss://api-chat.haraj.com.sa/chat/ws"
GRAPHQL_REFRESH = "https://graphql.haraj.com.sa/?queryName=refreshAccessToken"
GRAPHQL_LOGIN = "https://graphql.haraj.com.sa/?queryName=login"
REFRESH = "mutation refreshAccessToken($token: String!) { refreshAccessToken(refreshToken: $token) { accessToken ATvalidUntil message status } }"
LOGIN = "mutation login($username: String!, $password: String!, $oldToken: String!, $loginByURL: String) { login(username: $username, password: $password, oldRefreshToken: $oldToken, loginByURL: $loginByURL) { accessToken ATvalidUntil refreshToken RTvalidUntil ul username message status } }"
APP_LOGIN_URL = re.compile(r"^https://ios\.haraj\.sa/\?")

# Statuses that mean stop, not slow down.
HARD_STOP = frozenset({401, 402, 403, 429, 451})
SEND_SPACING_SECONDS = 20
SEND_PAUSE_SECONDS = 30 * 60
READ_SPACING_SECONDS = 2
READ_PAUSE_SECONDS = 15 * 60
RENEW_BEFORE_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 15
# Shown to the customer, so it does not name Haraj (Farq's staff inbox says «رسالة حراج غير نصية»).
UNREADABLE = "رسالة غير نصية"


class HarajChatUnavailable(Exception):
    """Nothing was posted: not configured, no session, or paused. Deliveries stay queued."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class HarajRefused(Exception):
    """Haraj refused the call. Nothing was posted."""

    def __init__(self, status: int):
        super().__init__(f"haraj refused: {status}")
        self.status = int(status)
        self.hard_stop = self.status in HARD_STOP


class HarajNotSent(Exception):
    """A failure before the message POST. Safe to try again."""


class HarajSendUncertain(Exception):
    """The message POST may have been published. Never replay it."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SentMessage:
    haraj_conversation_id: str
    haraj_message_id: str
    seq: int | None = None


@dataclass(frozen=True)
class InboundMessage:
    haraj_message_id: str
    body: str
    sent_at: str
    seq: int = 0
    media: tuple = ()


class HarajChat(Protocol):
    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str, attachments: list[dict] | None = None) -> SentMessage:
        """Send into the seller's Haraj conversation, opening it when conversation_id is None.
        attachments: [{content_type, data, name, width, height}] go first, each as its own Haraj message."""

    def fetch(self, *, conversation_id: str, seller_id: str, after_seq: int) -> list[InboundMessage]:
        """The seller's messages in this conversation with seq greater than after_seq, oldest first."""


class NotConnectedChat:
    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str, attachments: list[dict] | None = None) -> SentMessage:
        raise HarajChatUnavailable("NOT_SENT_CONFIGURATION_REQUIRED")

    def fetch(self, *, conversation_id: str, seller_id: str, after_seq: int) -> list[InboundMessage]:
        raise HarajChatUnavailable("CONFIGURATION_REQUIRED")


def author_id(seller_id: str | None) -> str | None:
    """``haraj:seller:19676360`` or ``19676360`` -> ``19676360``; anything else has no Haraj address."""
    value = str(seller_id or "").strip()
    match = re.fullmatch(r"(?:haraj:seller:)?([1-9]\d{0,15})", value)
    return match.group(1) if match else None


def _claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:  # noqa: BLE001
        return {}


class TokenCache(Protocol):
    def get_value(self, key: str) -> str | None: ...

    def set_value(self, key: str, value: str | None) -> None: ...


class HarajSession:
    """The Haraj access token, kept alive without anyone copying it by hand.

    Order: HARAJ_USERNAME + HARAJ_PASSWORD (login the way the iOS app does,
    through HARAJ_APP_LOGIN_URL), then HARAJ_REFRESH_TOKEN, then a fixed
    HARAJ_TOKEN. Tokens live about ten days and are renewed a day early.
    Serverless instances do not share memory, so the current tokens are kept in
    ``cache`` (the server-side store) rather than logging in on every cold start.
    """

    def __init__(
        self,
        env: dict | None = None,
        http: httpx.Client | None = None,
        cache: TokenCache | None = None,
        now: Callable[[], float] = time.time,
        prefix: str = "HARAJ_",
    ):
        env = os.environ if env is None else env
        self._env = env
        self._prefix = prefix
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
        self._cache = cache
        self._now = now
        self._lock = threading.Lock()
        self.username = str(env.get(f"{prefix}USERNAME") or "").strip()
        self._password = str(env.get(f"{prefix}PASSWORD") or "")
        self.can_login = bool(self.username and self._password)
        self._refresh = re.sub(r"^Bearer\s+", "", str(env.get(f"{prefix}REFRESH_TOKEN") or ""), flags=re.I).strip()
        static = re.sub(r"^Bearer\s+", "", str(env.get(f"{prefix}TOKEN") or ""), flags=re.I).strip()
        self.configured = bool(self._refresh or self.can_login or static)
        self.renewable = bool(self._refresh or self.can_login)
        self._current: tuple[str, float] | None = None
        self.last_error: str | None = None
        if cache is not None and self.renewable:
            token, until, refresh = cache.get_value("access_token"), cache.get_value("access_valid_until"), cache.get_value("refresh_token")
            if token and until:
                self._current = (token, float(until))
            if refresh:
                self._refresh = refresh
        if not self.renewable and static:
            exp = float(_claims(static).get("exp") or 0)
            self._current = (static, exp if exp > 0 else float("inf"))

    def _store(self, token: str, valid_until: float, refresh: str | None = None) -> str:
        self._current = (token, valid_until)
        if refresh:
            self._refresh = refresh
        self.last_error = None
        if self._cache is not None:
            self._cache.set_value("access_token", token)
            self._cache.set_value("access_valid_until", str(valid_until))
            if refresh:
                self._cache.set_value("refresh_token", refresh)
        return token

    def _valid_until(self, data: dict, token: str) -> float:
        return float(data.get("ATvalidUntil") or 0) or float(_claims(token).get("exp") or 0) or self._now() + 9 * 24 * 3600

    def _login(self) -> str:
        # Haraj refuses password login from its web endpoint («يجب استعادة الرقم السري»)
        # but accepts it the way its iOS app sends it.
        # The app's login request is the same for every account; an account may still carry its own.
        app_url = str(self._env.get(f"{self._prefix}APP_LOGIN_URL") or self._env.get("HARAJ_APP_LOGIN_URL") or "").strip()
        app_agent = str(self._env.get(f"{self._prefix}APP_USER_AGENT") or self._env.get("HARAJ_APP_USER_AGENT") or "").strip()
        url = app_url if APP_LOGIN_URL.match(app_url) else GRAPHQL_LOGIN
        headers = {"content-type": "application/json"}
        if url != GRAPHQL_LOGIN and app_agent:
            headers.update({"user-agent": app_agent, "accept": "application/json"})
        body = {"operationName": "login", "query": LOGIN, "variables": {"username": self.username, "password": self._password, "oldToken": "", "loginByURL": None}}
        response = self._http.post(url, headers=headers, json=body)
        data = _json(response).get("data", {}).get("login") if response.is_success else None
        if not data or int(data.get("status") or 0) != 200 or not data.get("accessToken") or not data.get("refreshToken"):
            raise HarajChatUnavailable(f"HARAJ_LOGIN_{(data or {}).get('status') or 'EMPTY'}" if response.is_success else f"HARAJ_LOGIN_HTTP_{response.status_code}")
        return self._store(data["accessToken"], self._valid_until(data, data["accessToken"]), data["refreshToken"])

    def _refresh_token(self) -> str:
        body = {"operationName": "refreshAccessToken", "query": REFRESH, "variables": {"token": self._refresh}}
        response = self._http.post(GRAPHQL_REFRESH, headers={"content-type": "application/json"}, json=body)
        data = _json(response).get("data", {}).get("refreshAccessToken") if response.is_success else None
        if not data or int(data.get("status") or 0) != 200 or not isinstance(data.get("accessToken"), str) or not data["accessToken"]:
            raise HarajChatUnavailable(f"HARAJ_REFRESH_{(data or {}).get('status') or 'EMPTY'}" if response.is_success else f"HARAJ_REFRESH_HTTP_{response.status_code}")
        return self._store(data["accessToken"], self._valid_until(data, data["accessToken"]))

    def _renew(self) -> str:
        if not self._refresh:
            return self._login()
        try:
            return self._refresh_token()
        except HarajChatUnavailable:
            # A cancelled refresh token is replaced by a fresh login.
            if not self.can_login:
                raise
            self._refresh = ""
            return self._login()

    def access_token(self) -> str:
        with self._lock:
            current = self._current
            margin = RENEW_BEFORE_SECONDS if self.renewable else 0
            if current and current[1] - self._now() > margin:
                return current[0]
            if not self.renewable:
                if current and current[1] > self._now():
                    return current[0]
                raise HarajChatUnavailable("HARAJ_TOKEN_EXPIRED")
            try:
                return self._renew()
            except (HarajChatUnavailable, httpx.HTTPError) as exc:
                self.last_error = getattr(exc, "code", "HARAJ_REFRESH_FAILED")
                # Still valid but inside the renewal window: keep using it.
                if current and current[1] > self._now():
                    return current[0]
                raise HarajChatUnavailable("HARAJ_SESSION_UNAVAILABLE") from exc

    def invalidate(self) -> None:
        """Haraj refused the token: drop it so the next call renews."""
        if self.renewable:
            with self._lock:
                self._current = None
                if self._cache is not None:
                    self._cache.set_value("access_token", None)


def _json(response: httpx.Response) -> dict:
    try:
        value = response.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _socket_session(token: str, timeout: float = TIMEOUT_SECONDS):
    """A fresh authenticated socket. Only the authentication reply is read."""
    from websockets.sync.client import connect

    socket = connect(CHAT_SOCKET, open_timeout=timeout, close_timeout=2)
    try:
        socket.send(json.dumps({"auth": {"id": "authenticate socket", "access_token": token}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HarajNotSent("WS_TIMEOUT")
            try:
                payload = json.loads(socket.recv(timeout=remaining))
            except ValueError:
                continue
            if not isinstance(payload, dict) or payload.get("id") != "authenticate socket":
                continue
            if payload.get("status") != 200:
                raise HarajRefused(int(payload.get("status") or 0))
            session_id = (payload.get("data") or {}).get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise HarajNotSent("WS_SESSION_MISSING")
            return socket, session_id
    except Exception:
        socket.close()
        raise


class HarajChatClient:
    """Sends from, and reads the conversations of, Taseer's own Haraj account.

    Spacing (20 s between messages) and the 30-minute stop after a refusal are
    enforced by the worker across processes; this class does one call at a time.
    """

    def __init__(self, session: HarajSession, user_id: str, http: httpx.Client | None = None, socket_factory=_socket_session, send_enabled: bool = True, inbox_enabled: bool = True):
        if not re.fullmatch(r"[1-9]\d*", str(user_id or "")):
            raise ValueError("HARAJ_USER_ID must be the sending account's numeric id")
        self.session = session
        self.user_id = str(user_id)
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
        self._socket = socket_factory
        self.send_enabled = send_enabled
        self.inbox_enabled = inbox_enabled

    # -- sending -----------------------------------------------------------

    def _post(self, token: str, path: str, body: dict) -> dict:
        try:
            response = self._http.post(
                f"{CHAT_ENDPOINT}{path}",
                headers={"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}"},
                content=json.dumps(body, ensure_ascii=False).encode(),
            )
        except httpx.HTTPError as exc:
            raise HarajNotSent(type(exc).__name__) from exc
        if not response.is_success:
            raise HarajRefused(response.status_code)
        payload = _json(response)
        # Success is the body's status, not the HTTP status.
        if payload.get("status") != 200:
            raise HarajRefused(int(payload.get("status") or 0))
        return payload.get("data") or {}

    def _open_topic(self, token: str, author: str) -> str:
        data = self._post(token, f"/chat/users/{self.user_id}/topics", {"type": "p2p", "with_id": int(author)})
        topic = (data.get("topic") or {}).get("topic_id")
        if not topic:
            raise HarajNotSent("NO_TOPIC_ID")
        return str(topic)

    def _send_content(self, token: str, topic: str, content: dict) -> SentMessage:
        socket = session_id = None
        # Reconnect only before publishing.
        for attempt in range(2):
            try:
                socket, session_id = self._socket(token)
                break
            except HarajRefused:
                raise
            except Exception as exc:  # noqa: BLE001
                if attempt == 1:
                    raise HarajNotSent("WS_CONNECTION_FAILED") from exc
        try:
            response = self._http.post(
                f"{CHAT_ENDPOINT}/chat/users/{self.user_id}/topics/{topic}/messages",
                headers={"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}"},
                content=json.dumps({"content": content, "session_id": session_id}, ensure_ascii=False).encode(),
            )
        except httpx.HTTPError as exc:
            # The POST may have reached Haraj. It is never replayed.
            raise HarajSendUncertain("NETWORK_ERROR") from exc
        finally:
            socket.close()
        if not response.is_success:
            raise HarajRefused(response.status_code)
        payload = _json(response)
        if payload.get("status") != 200:
            raise HarajRefused(int(payload.get("status") or 0))
        seq = ((payload.get("data") or {}).get("message") or {}).get("seq_id")
        if not isinstance(seq, (str, int)) or str(seq).strip() == "":
            raise HarajSendUncertain("MISSING_MESSAGE_ID")
        # seq_id is topic-local: the receipt keeps its topic.
        return SentMessage(haraj_conversation_id=topic, haraj_message_id=f"{topic}:{seq}", seq=int(seq) if str(seq).isdigit() else None)

    def _send_text(self, token: str, topic: str, text: str) -> SentMessage:
        return self._send_content(token, topic, {"type": "text/plain", "payload": {"text": text}})

    def _upload(self, token: str, attachment: dict) -> str:
        """Haraj's web client: ask chat/uploads for a URL, PUT the bytes there, then send that URL."""
        data = attachment["data"]
        grant = self._post(token, "/chat/uploads", {"mime_type": attachment["content_type"], "file_size": len(data)})
        url = grant.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise HarajNotSent("NO_UPLOAD_URL")
        try:
            put = self._http.put(url, content=data, headers={"content-type": attachment["content_type"]})
        except httpx.HTTPError as exc:
            raise HarajNotSent("UPLOAD_FAILED") from exc
        if not put.is_success:
            raise HarajNotSent(f"UPLOAD_{put.status_code}")
        return url

    def _media_content(self, token: str, attachment: dict) -> dict:
        url = self._upload(token, attachment)
        size = len(attachment["data"])
        if attachment["content_type"] == "application/pdf":
            return {"type": "application/pdf", "payload": {"url": url, "file_size": size, "file_name": attachment.get("name") or "file.pdf"}}
        return {
            "type": "image/jpeg",
            "payload": {"url": url, "file_size": size, "height": int(attachment.get("height") or 0), "width": int(attachment.get("width") or 0)},
        }

    def _attempt(self, conversation_id: str | None, author: str, body: str, attachments: list[dict]) -> SentMessage:
        token = self.session.access_token()
        topic = conversation_id or self._open_topic(token, author)
        contents = [self._media_content(token, item) for item in attachments]
        if body.strip():
            contents.append({"type": "text/plain", "payload": {"text": body}})
        receipt = None
        for index, content in enumerate(contents):
            try:
                receipt = self._send_content(token, topic, content)
            except (HarajRefused, HarajNotSent):
                # Part of this message is already in the seller's chat: never send it all again.
                if index:
                    raise HarajSendUncertain("PARTIAL")
                raise
        if receipt is None:
            raise HarajSendUncertain("EMPTY_MESSAGE")
        return receipt

    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str, attachments: list[dict] | None = None) -> SentMessage:
        if not self.send_enabled:
            raise HarajChatUnavailable("NOT_SENT_CONFIGURATION_REQUIRED")
        author = author_id(seller_id)
        if author is None:
            raise HarajSendUncertain("SKIPPED_NO_RECIPIENT")
        attachments = attachments or []
        try:
            return self._attempt(conversation_id, author, body, attachments)
        except HarajRefused as exc:
            # A rejected token is renewed and the message tried once more; nothing was posted.
            if exc.status not in (401, 403) or not self.session.renewable:
                raise
            self.session.invalidate()
            return self._attempt(conversation_id, author, body, attachments)

    # -- reading -----------------------------------------------------------

    def _get(self, url: str, params: dict) -> httpx.Response:
        def call() -> httpx.Response:
            return self._http.get(url, params=params, headers={"Authorization": f"Bearer {self.session.access_token()}"})

        response = call()
        if response.status_code in (401, 403) and self.session.renewable:
            self.session.invalidate()
            response = call()
        return response

    def fetch(self, *, conversation_id: str, seller_id: str, after_seq: int, max_pages: int = 10, sleep: Callable[[float], None] = time.sleep) -> list[InboundMessage]:
        """Only conversations we already wrote to; pages back until after_seq is reached."""
        if not self.inbox_enabled:
            raise HarajChatUnavailable("DISABLED")
        author = author_id(seller_id)
        if author is None or conversation_id not in (f"p2p{self.user_id}_{author}", f"p2p{author}_{self.user_id}"):
            raise HarajChatUnavailable("AMBIGUOUS")
        url = f"{CHAT_ENDPOINT}/chat/users/{self.user_id}/topics/{conversation_id}/messages"
        found: dict[int, InboundMessage] = {}
        cursor: str | None = None
        scanned = from_seller = 0
        newest_seller = None
        for page in range(max_pages):
            if page:
                sleep(READ_SPACING_SECONDS)
            params = {"limit": "80"}
            if cursor:
                params["last_key"] = cursor
            try:
                response = self._get(url, params)
            except httpx.HTTPError as exc:
                raise HarajChatUnavailable("UNAVAILABLE") from exc
            if not response.is_success:
                raise HarajRefused(response.status_code)
            value = _json(response)
            data = value.get("data") or {}
            messages = data.get("messages")
            if value.get("status") != 200 or str(data.get("user_id")) != self.user_id or data.get("topic_id") != conversation_id or not isinstance(messages, list):
                raise HarajChatUnavailable("INVALID_RESPONSE")
            reached = False
            for item in messages:
                seq_text = str(item.get("seq_id"))
                if not seq_text.isdigit():
                    raise HarajChatUnavailable("INVALID_SEQUENCE")
                seq = int(seq_text)
                scanned += 1
                if str(item.get("from_id")) == author:
                    from_seller += 1
                    newest_seller = max(newest_seller or 0, seq)
                if seq <= after_seq:
                    reached = True
                    continue
                if str(item.get("from_id")) != author or item.get("is_deleted"):
                    continue
                text, media = _read_content(item.get("content") or {})
                found[seq] = InboundMessage(haraj_message_id=f"{conversation_id}:{seq}", body=text, sent_at=_timestamp(item.get("ts")), seq=seq, media=media)
            cursor = data.get("last_key") or None
            if reached or not cursor:
                break
            if not isinstance(cursor, str) or len(cursor) > 2000:
                raise HarajChatUnavailable("INVALID_CURSOR")
        # One line per read: proves a conversation was read and whether the seller wrote at all.
        log.info(
            "haraj read %s: %s messages scanned, %s from seller (newest seq %s), %s new after seq %s",
            conversation_id, scanned, from_seller, newest_seller, len(found), after_seq,
        )
        return [found[seq] for seq in sorted(found)]


def _read_content(content: dict) -> tuple[str, tuple]:
    """A Haraj chat message as (text, media). Photos, videos, PDFs and voice notes carry a URL."""
    kind = content.get("type")
    payload = content.get("payload") or {}
    if kind == "text/plain":
        text = payload.get("text")
        if not isinstance(text, str):
            raise HarajChatUnavailable("INVALID_CONTENT")
        return text, ()
    url = payload.get("url")
    if isinstance(kind, str) and isinstance(url, str) and url.startswith("https://") and kind in ("image/jpeg", "image/png", "image/webp", "video/mp4", "application/pdf", "audio/aac"):
        entry = {"type": kind, "url": url, "size": payload.get("file_size")}
        if kind.startswith("image/"):
            entry.update(width=payload.get("width"), height=payload.get("height"))
        if kind == "application/pdf":
            entry["name"] = payload.get("file_name") or "ملف.pdf"
        if kind == "audio/aac":
            entry["duration"] = payload.get("duration")
        return "", (entry,)
    return UNREADABLE, ()


def _timestamp(value) -> str:
    from datetime import datetime, timezone

    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = float(value)
        return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, tz=timezone.utc).isoformat()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    raise HarajChatUnavailable("INVALID_TIMESTAMP")


class _Prefixed:
    """One account's slice of the shared store. ``legacy`` is read (never written) as a fallback,
    so the older account keeps the tokens it cached before keys carried an account id."""

    def __init__(self, cache: TokenCache, prefix: str, legacy: str | None = None):
        self._cache, self._prefix, self._legacy = cache, prefix, legacy

    def get_value(self, key: str) -> str | None:
        value = self._cache.get_value(self._prefix + key)
        if value is None and self._legacy is not None:
            value = self._cache.get_value(self._legacy + key)
        return value

    def set_value(self, key: str, value: str | None) -> None:
        self._cache.set_value(self._prefix + key, value)


TASEER_PREFIX = "HARAJ_TASEER_"
LEGACY_PREFIX = "HARAJ_"
_ACCOUNT_ID = re.compile(r"[1-9]\d*")


def conversation_account(conversation_id: str | None, accounts) -> str | None:
    """Which of our accounts a Haraj conversation (``p2p<a>_<b>``) belongs to, or None."""
    match = re.fullmatch(r"p2p(\d+)_(\d+)", str(conversation_id or ""))
    if not match:
        return None
    for account in accounts:
        if account and account in match.groups():
            return account
    return None


class AccountChat:
    """Taseer's Haraj channel across accounts: one account sends, every configured account reads.

    send_mode: "open" sends whatever is queued; "canary" sends only the one delivery the owner
    approved (see worker.CANARY_KEY); "closed" sends nothing. A message whose thread lives on
    another account opens a fresh conversation from the sending account."""

    def __init__(self, sender: HarajChatClient | None, readers: dict[str, HarajChatClient], send_mode: str = "closed"):
        self.sender = sender
        self.readers = dict(readers)
        self.send_account_id = sender.user_id if sender is not None else None
        self.send_mode = send_mode if sender is not None and sender.send_enabled else "closed"

    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str, attachments: list[dict] | None = None) -> SentMessage:
        if self.sender is None or self.send_mode == "closed":
            raise HarajChatUnavailable("NOT_SENT_CONFIGURATION_REQUIRED")
        if conversation_id and conversation_account(conversation_id, [self.send_account_id]) is None:
            conversation_id = None
        return self.sender.send(conversation_id=conversation_id, seller_id=seller_id, ad_id=ad_id, body=body, attachments=attachments)

    def fetch(self, *, conversation_id: str, seller_id: str, after_seq: int) -> list[InboundMessage]:
        account = conversation_account(conversation_id, list(self.readers))
        if account is None:
            raise HarajChatUnavailable("ACCOUNT_UNAVAILABLE")
        return self.readers[account].fetch(conversation_id=conversation_id, seller_id=seller_id, after_seq=after_seq)


def _client(env: dict, prefix: str, user_id: str, cache: TokenCache | None, legacy_cache: bool, send: bool, inbox: bool) -> HarajChatClient | None:
    if not _ACCOUNT_ID.fullmatch(user_id):
        return None
    scoped = None
    if cache is not None:
        scoped = _Prefixed(cache, f"session:{user_id}:", legacy="session:" if legacy_cache else None)
    session = HarajSession(env, cache=scoped, prefix=prefix)
    if not session.configured:
        return None
    return HarajChatClient(session, user_id, send_enabled=send, inbox_enabled=inbox)


def chat_from_env(env: dict | None = None, cache: TokenCache | None = None) -> HarajChat:
    """Taseer's Haraj accounts from its own server settings, or a channel that sends nothing.

    HARAJ_SEND_ENABLED / HARAJ_INBOX_ENABLED stay the master switches. Sending goes out from
    the HARAJ_TASEER_* account only, and only once HARAJ_TASEER_SEND_ENABLED=1; until then the
    worker may send just a single owner-approved canary. The older HARAJ_* account is read only."""
    env = os.environ if env is None else env
    send = env.get("HARAJ_SEND_ENABLED") == "1"
    inbox = env.get("HARAJ_INBOX_ENABLED") == "1"
    if not (send or inbox):
        return NotConnectedChat()
    taseer_id = str(env.get(f"{TASEER_PREFIX}USER_ID") or "").strip()
    legacy_id = str(env.get("HARAJ_USER_ID") or env.get("HARAJ_FARQ_USER_ID") or "").strip()
    sender = _client(env, TASEER_PREFIX, taseer_id, cache, legacy_cache=False, send=send, inbox=inbox)
    readers: dict[str, HarajChatClient] = {}
    if sender is not None:
        readers[sender.user_id] = sender
    if legacy_id and legacy_id != taseer_id:
        # The shared account: read the conversations Taseer already has there, never send.
        legacy = _client(env, LEGACY_PREFIX, legacy_id, cache, legacy_cache=True, send=False, inbox=inbox)
        if legacy is not None:
            readers[legacy.user_id] = legacy
    if sender is None and not readers:
        return NotConnectedChat()
    mode = "open" if env.get(f"{TASEER_PREFIX}SEND_ENABLED") == "1" else "canary"
    return AccountChat(sender, readers, send_mode=mode)


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬٫", "01234567890123456789,.")

# -- Request references ---------------------------------------------------------
# Taseer writes from one Haraj account, so a seller has one conversation with us whatever the
# number of buyers. Every message we send carries the request's reference; a reply that quotes
# it is filed under that request, whichever buyers share the conversation.
REFERENCE_LABEL = "رقم الطلب"
_AR = "؀-ۿ"
_REFERENCE = re.compile(rf"(?<![A-Za-z0-9{_AR}])[TtТت]\s?[-_ـ–]?\s?(\d{{6}})(?!\d)")
_LABELLED_REFERENCE = re.compile(r"رقم\s*(?:ال)?طلب\s*[:：\-]?\s*(\d{6})(?!\d)")


def new_reference() -> str:
    """``T-`` and six digits: short enough to read out, hard to mistake for a price."""
    return f"T-{secrets.randbelow(900000) + 100000}"


def reference_line(code: str) -> str:
    return f"{REFERENCE_LABEL}: {code}"


def with_reference(body: str, code: str | None) -> str:
    """The outgoing text with the request's reference as its last line."""
    if not code or code in (body or ""):
        return body
    return f"{body.rstrip()}\n{reference_line(code)}" if (body or "").strip() else reference_line(code)


def find_references(text: str) -> list[str]:
    """Every request reference a seller's message quotes, as ``T-123456``, in order."""
    value = (text or "").translate(_DIGITS)
    found = [match.group(1) for pattern in (_REFERENCE, _LABELLED_REFERENCE) for match in pattern.finditer(value)]
    return list(dict.fromkeys(f"T-{digits}" for digits in found))


# -- Prices in seller replies -------------------------------------------------------
# Conservative on purpose: a reply with no price is shown as text; a wrong price would rank
# the seller as the cheapest. Anything unclear (a range, a unit price, two different
# prices) gives no price.
_AMOUNT = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_THOUSAND = rf"\s*(?:ألف|الف|آلاف|الاف|k)(?![A-Za-z{_AR}])"
_CURRENCY = rf"\s*(?:ريالات|ريال|ر\.?\s?س|﷼|rs|sar)(?![A-Za-z{_AR}])"
_TOKEN = re.compile(rf"(?<![\d.,])(?P<num>{_AMOUNT})(?![\d])(?P<k>{_THOUSAND})?(?P<cur>{_CURRENCY})?", re.IGNORECASE)
_PRICE_WORD = re.compile(r"(?:السعر|سعره|سعرها|بسعر|سعر|المبلغ|الإجمالي|الاجمالي|المجموع)")
_DELIVERY_WORD = re.compile(r"(?:توصيل|شحن|delivery)", re.IGNORECASE)
_MARKER_BEFORE = re.compile(r"(?:(?:السعر|سعره|سعرها|بسعر|سعر|المبلغ|الإجمالي|الاجمالي|المجموع|توصيل|شحن)\s*[:=]?|(?:^|\s)بـ?)\s*$")
_INCLUSION_BEFORE = re.compile(r"(?:شامل|يشمل|مع|بدون|غير)\s*(?:ال)?$")
_FREE_AFTER = re.compile(r"مجان|علينا|شامل")
_DELIVERY_SUFFIX = re.compile(r"^\s*(?:لل|ل)(?:توصيل|شحن)")
_EXCLUDED = re.compile(r"(?:بدون|غير\s*شامل(?:\s*ال)?|لا\s*يشمل(?:\s*ال)?)\s*(?:ال)?(?:توصيل|شحن)|(?:التوصيل|الشحن)\s*(?:على|عل)\s*(?:المشتري|الزبون|العميل|حساب|عليك)")
_INCLUDED = re.compile(r"(?:شامل|يشمل|مع|بـ?)\s*(?:ال)?(?:توصيل|شحن)|(?:التوصيل|توصيل|الشحن|شحن)\s*(?:مجان|مجاني|مجانا|مجاناً|علينا)")
_UNIT = re.compile(
    rf"(?:(?:لل|لكل\s*|/\s*|في\s*ال|بال)(?:قطع[ةه]|حب[ةه]|متر|م2|واحد[ةه]?|ساع[ةه]|يوم|كيلو|كرتون|شهر|ليل[ةه]|نفر|شخص|طن|لتر|سن[ةه])"
    rf"|(?<![{_AR}])ال(?:قطع[ةه]|حب[ةه]|متر|كيلو|كرتون|طن|لتر))(?![{_AR}])"
)
_RANGE = re.compile(rf"(?:{_AMOUNT})(?:{_THOUSAND})?(?:{_CURRENCY})?\s*(?:-|–|—|~|إلى|الى|لين|حتى|او|أو)\s*(?:{_AMOUNT})|بين\s*(?:{_AMOUNT})\s*(?:{_CURRENCY})?\s*و\s*(?:{_AMOUNT})", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?966|00966)?\s?0?5\d[\s-]?\d{3}[\s-]?\d{4}(?!\d)")
_URL = re.compile(r"https?://\S+")
_BARE = re.compile(rf"^\s*(?P<num>{_AMOUNT})(?P<k>{_THOUSAND})?\s*(?:فقط|نهائي|صافي)?\s*[.!؟]*\s*$", re.IGNORECASE)
MIN_PRICE = 10
MAX_PRICE_DIGITS = 8


@dataclass(frozen=True)
class Quote:
    """A price read from a seller's reply. ``uncertain`` means no price should be shown as his offer."""

    base: float | None
    delivery_price: float | None = None
    delivery_included: bool | None = None
    uncertain: str | None = None

    @property
    def total(self) -> float | None:
        if self.base is None or self.uncertain:
            return None
        return self.base + (self.delivery_price or 0.0)


def _plausible(raw: str, value: float) -> bool:
    whole = raw.replace(",", "").split(".")[0]
    return not whole.startswith("05") and len(whole) <= MAX_PRICE_DIGITS and value >= MIN_PRICE


def extract_quote(text: str) -> Quote | None:
    """The seller's price and delivery, or None when the reply names no price.

    A number counts as a price when a currency (ريال, ر.س), «ألف», or a price word (السعر،
    التوصيل) sits next to it, or when the whole reply is that one number."""
    value = _URL.sub(" ", (text or "").translate(_DIGITS))
    value = _LABELLED_REFERENCE.sub(" ", value)
    value = _REFERENCE.sub(" ", value)
    value = _PHONE.sub(" ", value)
    bare = _BARE.match(value)
    if bare:
        amount = float(bare.group("num").replace(",", "")) * (1000 if bare.group("k") else 1)
        return Quote(base=amount) if _plausible(bare.group("num"), amount) else None
    bases: list[float] = []
    deliveries: list[float] = []
    previous_end = 0
    for token in _TOKEN.finditer(value):
        before = value[previous_end : token.start()]
        previous_end = token.end()
        marked = bool(token.group("cur") or token.group("k") or _MARKER_BEFORE.search(before))
        if not marked:
            continue
        amount = float(token.group("num").replace(",", "")) * (1000 if token.group("k") else 1)
        delivery_at = [match for match in _DELIVERY_WORD.finditer(before)]
        price_at = [match.start() for match in _PRICE_WORD.finditer(before)]
        is_delivery = False
        if delivery_at:
            last = delivery_at[-1]
            is_delivery = (
                last.start() > (price_at[-1] if price_at else -1)
                and not _INCLUSION_BEFORE.search(before[: last.start()])
                and not _FREE_AFTER.search(before[last.end() :])
            )
        if _DELIVERY_SUFFIX.match(value[token.end() :]):
            is_delivery = True
        if is_delivery:
            if amount > 0 and _plausible(token.group("num"), max(amount, MIN_PRICE)):
                deliveries.append(amount)
        elif _plausible(token.group("num"), amount):
            bases.append(amount)
    if not bases:
        return None
    if _RANGE.search(value):
        return Quote(base=None, uncertain="range")
    if _UNIT.search(value):
        return Quote(base=None, uncertain="unit_price")
    if len(set(bases)) > 1:
        return Quote(base=None, uncertain="several_prices")
    if len(set(deliveries)) > 1:
        return Quote(base=None, uncertain="several_delivery_prices")
    if deliveries:
        return Quote(base=bases[0], delivery_price=deliveries[0], delivery_included=False)
    if _EXCLUDED.search(value):
        return Quote(base=bases[0], delivery_included=False)
    if _INCLUDED.search(value):
        return Quote(base=bases[0], delivery_price=0.0, delivery_included=True)
    return Quote(base=bases[0])


def extract_price(text: str) -> float | None:
    """The total the seller asked for (price plus any delivery he named), or None when unsure."""
    quote = extract_quote(text)
    return None if quote is None else quote.total
