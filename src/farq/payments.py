"""Moyasar payment helpers. Secret key stays server-side only."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MOYASAR_API = "https://api.moyasar.com/v1"
ASSOCIATION_NAME = "apple-developer-merchantid-domain-association"
WELL_KNOWN_FILE = Path(__file__).resolve().parents[2] / "web" / ".well-known" / ASSOCIATION_NAME


@dataclass(frozen=True)
class MoyasarConfig:
    publishable_key: str
    secret_key: str
    display_name: str = "فرق"
    currency: str = "SAR"

    @classmethod
    def from_env(cls) -> "MoyasarConfig | None":
        publishable = (os.environ.get("MOYASAR_PUBLISHABLE_KEY") or "").strip()
        secret = (os.environ.get("MOYASAR_SECRET_KEY") or "").strip()
        if not publishable or not secret:
            return None
        display = (os.environ.get("MOYASAR_DISPLAY_NAME") or "فرق").strip() or "فرق"
        return cls(publishable_key=publishable, secret_key=secret, display_name=display)

    @property
    def apple_pay_ready(self) -> bool:
        return self.publishable_key.startswith(("pk_live_", "pk_test_")) and bool(apple_pay_association_body())


def apple_pay_association_body() -> bytes | None:
    raw = os.environ.get("MOYASAR_APPLE_PAY_ASSOCIATION")
    if raw and raw.strip():
        return raw.strip().encode("utf-8")
    if WELL_KNOWN_FILE.is_file():
        return WELL_KNOWN_FILE.read_bytes()
    return None


def sar_to_halalas(amount: float) -> int:
    return int(round(float(amount) * 100))


def fetch_payment(payment_id: str, secret_key: str, timeout: float = 12) -> dict:
    token = base64.b64encode(f"{secret_key}:".encode()).decode()
    request = urllib.request.Request(
        f"{MOYASAR_API}/payments/{payment_id}",
        headers={"Authorization": f"Basic {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise ValueError(f"moyasar fetch failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise ValueError("moyasar unreachable") from exc
