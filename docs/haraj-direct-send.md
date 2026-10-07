# Haraj authenticated direct sends

Per-user Haraj sends execute synchronously in the verified owner's request action.
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
