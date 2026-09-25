"""Signing in with the Farq account, for the customer who arrives inside farq.sa.

Farq keeps its users in Supabase Auth; Taseer keeps its own users table. Inside Farq the
customer has already signed in once, and a second email-and-password door was the reason no
request could ever be sent from the embed. So the embed hands Taseer the Farq session's
access token, Taseer asks Farq's Supabase whether that token is real, and signs the same
person into a Taseer account tied to his Farq user id.

The token is verified with Supabase itself (GET /auth/v1/user), never by decoding it here:
a signature check would need Farq's JWT secret on this host, and a call needs only the
public anon key that every farq.sa page already ships with. Nothing is trusted from the
client but the token, and the token is trusted only once Supabase says so.

Off unless both FARQ_AUTH_SUPABASE_URL and FARQ_AUTH_SUPABASE_ANON_KEY are set; then the
endpoint answers 503 and the embed falls back to asking Farq for a sign-in it cannot use,
which is exactly the state this file exists to end - so keep them set.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

log = logging.getLogger("farq.farq_auth")

TIMEOUT_SECONDS = float(os.environ.get("FARQ_AUTH_TIMEOUT", "6"))


@dataclass(frozen=True)
class FarqIdentity:
    user_id: str
    email: str
    name: str | None
    email_verified: bool


def _url() -> str:
    return (os.environ.get("FARQ_AUTH_SUPABASE_URL") or "").strip().rstrip("/")


def _anon_key() -> str:
    return (os.environ.get("FARQ_AUTH_SUPABASE_ANON_KEY") or "").strip()


def configured() -> bool:
    return bool(_url() and _anon_key())


def _display_name(user: dict) -> str | None:
    meta = user.get("user_metadata") if isinstance(user.get("user_metadata"), dict) else {}
    for key in ("full_name", "name", "display_name"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:60]
    return None


def verify(access_token: str) -> FarqIdentity | None:
    """The Farq user behind a Supabase access token, or None. Never raises."""
    token = (access_token or "").strip()
    if not token or not configured():
        return None
    try:
        response = httpx.get(
            f"{_url()}/auth/v1/user",
            timeout=TIMEOUT_SECONDS,
            headers={"apikey": _anon_key(), "Authorization": f"Bearer {token}"},
        )
    except Exception:  # noqa: BLE001 - a bad network answer is "not signed in", not a crash
        log.warning("farq auth unavailable", exc_info=True)
        return None
    if response.status_code != 200:
        return None
    try:
        user = response.json()
    except ValueError:
        return None
    if not isinstance(user, dict):
        return None
    user_id = str(user.get("id") or "").strip()
    email = str(user.get("email") or "").strip().lower()
    if not user_id or not email:
        return None
    return FarqIdentity(
        user_id=user_id,
        email=email,
        name=_display_name(user),
        email_verified=bool(user.get("email_confirmed_at")),
    )
