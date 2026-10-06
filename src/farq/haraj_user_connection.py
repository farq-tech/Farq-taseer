"""Per-user Haraj release boundary. Messaging evidence exists; durable owner/consent routing is pending.

Credentials belong to the Farq API vault; Taseer must never receive raw tokens.
When owner-scoped queue and inbox wiring passes release gates, replace this boundary
with authenticated owner + RFQ consent checks, never a central-account fallback.
"""
import os
from fastapi import HTTPException

EVIDENCE_REQUIRED = "HARAJ_MESSAGING_NOT_READY"

def enabled(env=None) -> bool:
    values = os.environ if env is None else env
    return str(values.get("HARAj_USER_ACCOUNT_CONNECTION", "0")).lower() in ("1", "true")

def require_messaging_evidence() -> None:
    if enabled():
        raise HTTPException(status_code=501, detail={
            "code": EVIDENCE_REQUIRED,
            "message": "مراسلات حراج غير متاحة حاليًا داخل فرق",
        })
