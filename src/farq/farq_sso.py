"""Sign-in from inside Farq: a Farq customer uses Taseer on his Farq account.

Taseer runs embedded in the Farq app. The customer has already signed in there, so a
second email-and-password screen inside the frame would be a second account for the same
person. Instead Farq's API mints a one-minute ticket for its signed-in user, the Farq page
hands it to the frame, and /v1/auth/farq-sso trades it for an ordinary Taseer session.

The ticket is an HS256 JWT signed with FARQ_SSO_SECRET, which only the two APIs hold.
The verifier is deliberately narrow, the same rules as Farq's own token verifier:

- the algorithm is never read from the header to choose anything; exactly HS256 is accepted
- the signature is compared in constant time and checked before any claim is trusted
- iss and aud must be Farq's SSO values, so a Farq access token cannot be replayed here
- exp is required and the ticket may not live longer than MAX_TTL_SECONDS
- a ticket is spent on first use (per process); its one-minute life bounds the rest

With no secret configured the whole path is off and answers 503, never open.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time

ISSUER = "farq-auth"
AUDIENCE = "taseer-sso"
MAX_TTL_SECONDS = 120
MIN_SECRET_LENGTH = 32


class TicketError(Exception):
    """The ticket is not one Farq issued for Taseer, or it is no longer valid."""


def sso_secret() -> str | None:
    secret = os.environ.get("FARQ_SSO_SECRET", "").strip()
    return secret if len(secret) >= MIN_SECRET_LENGTH else None


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class _SpentTickets:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def spend(self, jti: str, exp: float) -> bool:
        now = time.time()
        with self._lock:
            self._seen = {key: until for key, until in self._seen.items() if until > now}
            if jti in self._seen:
                return False
            self._seen[jti] = exp
            return True


_spent = _SpentTickets()


def verify_ticket(ticket: str, secret: str, now: float | None = None) -> dict:
    """The Farq identity in a valid ticket: {farq_user_id, email, email_verified, name}."""
    if not isinstance(ticket, str) or ticket.count(".") != 2:
        raise TicketError("malformed")
    header_part, payload_part, signature_part = ticket.split(".")
    try:
        signature = _b64decode(signature_part)
    except (ValueError, TypeError) as exc:
        raise TicketError("malformed") from exc
    expected = hmac.new(secret.encode(), f"{header_part}.{payload_part}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise TicketError("bad signature")
    try:
        header = json.loads(_b64decode(header_part))
        payload = json.loads(_b64decode(payload_part))
    except (ValueError, TypeError) as exc:
        raise TicketError("malformed") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise TicketError("wrong algorithm")
    if not isinstance(payload, dict) or payload.get("iss") != ISSUER or payload.get("aud") != AUDIENCE:
        raise TicketError("wrong audience")
    current = time.time() if now is None else now
    exp, iat = payload.get("exp"), payload.get("iat")
    if not isinstance(exp, (int, float)) or not isinstance(iat, (int, float)):
        raise TicketError("no expiry")
    if exp <= current or exp - iat > MAX_TTL_SECONDS or iat > current + 30:
        raise TicketError("expired")
    sub, email, jti = payload.get("sub"), payload.get("email"), payload.get("jti")
    if not isinstance(sub, str) or not sub or not isinstance(jti, str) or not jti:
        raise TicketError("no subject")
    if not isinstance(email, str) or "@" not in email:
        raise TicketError("no email")
    if not _spent.spend(jti, exp):
        raise TicketError("already used")
    name = payload.get("name")
    return {
        "farq_user_id": sub,
        "email": email.strip().lower(),
        "email_verified": payload.get("email_verified") is True,
        "name": name.strip()[:60] if isinstance(name, str) and name.strip() else None,
    }
