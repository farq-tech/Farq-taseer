# Explicit account quota exemption

Server configuration TASEER_QUOTA_EXEMPT_FARQ_USER_IDS may grant a private quota exception to verified immutable Farq user UUIDs. It defaults to empty, requires per-user Haraj mode, and does not reuse legacy unlimited, email or client-controlled attributes. Exempt accounts bypass seller caps and item credit consumption; ordinary accounts remain capped at twenty and charged centrally. Removing the UUID revokes the exception on redeployment.

Connection verification, consent, owner-scoped search provenance, recipient identity deduplication, provider pacing and uncertain-send reconciliation remain mandatory. This flag grants no administrative role, other-user access or provider protection bypass.

Credit idempotency keys now hash each item's need text using SHA256, keeping the key below the central ledger's 300-character limit for Arabic specifications. Request IDs still isolate different requests; each distinct item retains a distinct key and reversals use the same exact keys.

A large accepted request is not a delivery guarantee. Existing request-scoped sending can exceed hosting execution duration and cannot be blindly resumed; inspect each recipient's outcome before continuing. A stalled production idempotency reservation must be reconciled separately, never bypassed with a new key.
