# Haraj authenticated direct sends

Per-user Haraj sends run as a request-scoped background task after the verified
owner action is persisted and returned. The response means queued, not accepted.
The selected request alone is eligible: global cron dispatch is disabled in per-user
mode. Cron still reads replies. Persisted deliveries and Farq provider reservations
are audit/idempotency records, not a background send backlog.

A provider refusal, challenge or rate limit stops that action, marks remaining
recipients unsent, and schedules no retry. An uncertain POST remains uncertain;
its reservation cannot be replayed. Older requests without consent are never
upgraded or automatically sent. Repeating an accepted reservation returns its
existing receipt, not a second message.

Request-scoped store claiming retains PostgreSQL SKIP LOCKED. Credentials and
provider-owner verification remain isolated in the Farq vault. Item debit remains
in the central membership ledger; provider acceptance is distinct from delivery
or read evidence. No global backlog length can promise a send time.

Interrupted or server-failed per-user send actions retain their owner-bound HTTP
idempotency reservation until reconciled, preventing retries from creating a
second RFQ after an uncertain response. Validation failures remain retryable.

## Seller selection and pacing (2026-10-12)

Per-user mode exposes `seller_selection_unlimited` to remove the legacy six-seller
UI limit, matching existing entitlement validation. Legacy shared mode keeps its
plan limits; item billing and the existing request-size limit remain unchanged.

The scoped task waits 30 seconds after each accepted send before the next attempt.
Farq separately enforces a durable minimum 30-second owner reservation interval
across concurrent requests and replicas, including when an old environment value
is zero. A refusal stops the action with no replay. This is an application pacing
policy, not a claim that Haraj permits a particular volume or cannot block it.

Tasks are not recovered automatically after process restart. Persisted statuses
remain available; uncertain attempts require reconciliation. Revert this release
to restore synchronous dispatch and the previous UI metadata.
