"""Haraj chat channel. Sellers only ever talk to us inside Haraj.

Taseer shows the customer one conversation per item; every outgoing message is
routed to the Haraj conversation of each targeted seller, and seller replies are
pulled back by the sync worker.

The transport is the one Farq Construction uses (farq repo:
api/lib/construction/haraj-session.js, haraj-invite.js, haraj-chat.js,
haraj-inbox.js; docs/construction/HARAJ_DISPATCH_AND_QUOTE_TOTALS.md), ported
as-is. Taseer sends from its own Haraj account: the same environment variable
names, with Taseer's own values on Taseer's server. Farq's token is never used.

The seller's address is the Haraj author id only (``haraj:seller:19676360`` ->
``19676360``). No phone number is looked up, and ``postContact`` is never used.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import httpx

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
UNREADABLE = "رسالة حراج غير نصية"


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


class HarajChat(Protocol):
    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str) -> SentMessage:
        """Send into the seller's Haraj conversation, opening it when conversation_id is None."""

    def fetch(self, *, conversation_id: str, seller_id: str, after_seq: int) -> list[InboundMessage]:
        """The seller's messages in this conversation with seq greater than after_seq, oldest first."""


class NotConnectedChat:
    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str) -> SentMessage:
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

    def __init__(self, env: dict | None = None, http: httpx.Client | None = None, cache: TokenCache | None = None, now: Callable[[], float] = time.time):
        env = os.environ if env is None else env
        self._env = env
        self._http = http or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False)
        self._cache = cache
        self._now = now
        self._lock = threading.Lock()
        self.username = str(env.get("HARAJ_USERNAME") or "").strip()
        self._password = str(env.get("HARAJ_PASSWORD") or "")
        self.can_login = bool(self.username and self._password)
        self._refresh = re.sub(r"^Bearer\s+", "", str(env.get("HARAJ_REFRESH_TOKEN") or ""), flags=re.I).strip()
        static = re.sub(r"^Bearer\s+", "", str(env.get("HARAJ_TOKEN") or ""), flags=re.I).strip()
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
        app_url = str(self._env.get("HARAJ_APP_LOGIN_URL") or "").strip()
        app_agent = str(self._env.get("HARAJ_APP_USER_AGENT") or "").strip()
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

    def _send_text(self, token: str, topic: str, text: str) -> SentMessage:
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
                content=json.dumps({"content": {"type": "text/plain", "payload": {"text": text}}, "session_id": session_id}, ensure_ascii=False).encode(),
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

    def _attempt(self, conversation_id: str | None, author: str, body: str) -> SentMessage:
        token = self.session.access_token()
        topic = conversation_id or self._open_topic(token, author)
        return self._send_text(token, topic, body)

    def send(self, *, conversation_id: str | None, seller_id: str, ad_id: str | None, body: str) -> SentMessage:
        if not self.send_enabled:
            raise HarajChatUnavailable("NOT_SENT_CONFIGURATION_REQUIRED")
        author = author_id(seller_id)
        if author is None:
            raise HarajSendUncertain("SKIPPED_NO_RECIPIENT")
        try:
            return self._attempt(conversation_id, author, body)
        except HarajRefused as exc:
            # A rejected token is renewed and the message tried once more; nothing was posted.
            if exc.status not in (401, 403) or not self.session.renewable:
                raise
            self.session.invalidate()
            return self._attempt(conversation_id, author, body)

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
                if seq <= after_seq:
                    reached = True
                    continue
                if str(item.get("from_id")) != author or item.get("is_deleted"):
                    continue
                content = item.get("content") or {}
                text = (content.get("payload") or {}).get("text") if content.get("type") == "text/plain" else UNREADABLE
                if not isinstance(text, str):
                    raise HarajChatUnavailable("INVALID_CONTENT")
                found[seq] = InboundMessage(haraj_message_id=f"{conversation_id}:{seq}", body=text, sent_at=_timestamp(item.get("ts")), seq=seq)
            cursor = data.get("last_key") or None
            if reached or not cursor:
                break
            if not isinstance(cursor, str) or len(cursor) > 2000:
                raise HarajChatUnavailable("INVALID_CURSOR")
        return [found[seq] for seq in sorted(found)]


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
    def __init__(self, cache: TokenCache, prefix: str):
        self._cache, self._prefix = cache, prefix

    def get_value(self, key: str) -> str | None:
        return self._cache.get_value(self._prefix + key)

    def set_value(self, key: str, value: str | None) -> None:
        self._cache.set_value(self._prefix + key, value)


def chat_from_env(env: dict | None = None, cache: TokenCache | None = None) -> HarajChat:
    """Taseer's own account from its own server settings, or a channel that sends nothing."""
    env = os.environ if env is None else env
    send = env.get("HARAJ_SEND_ENABLED") == "1"
    inbox = env.get("HARAJ_INBOX_ENABLED") == "1"
    user_id = str(env.get("HARAJ_USER_ID") or env.get("HARAJ_FARQ_USER_ID") or "").strip()
    session = HarajSession(env, cache=None if cache is None else _Prefixed(cache, "session:"))
    if not (send or inbox) or not session.configured or not re.fullmatch(r"[1-9]\d*", user_id):
        return NotConnectedChat()
    return HarajChatClient(session, user_id, send_enabled=send, inbox_enabled=inbox)


_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬", "01234567890123456789,")
_PRICE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:ر\.?\s?س|ريال|rs|sar)", re.IGNORECASE)


def extract_price(text: str) -> float | None:
    """A price only when the seller wrote a currency next to the number, e.g. "٢٥٠ ريال"."""
    found = _PRICE.search((text or "").translate(_DIGITS))
    if not found:
        return None
    value = float(found.group(1).replace(",", ""))
    return value if value > 0 else None
