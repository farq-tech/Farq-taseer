# Per-user Haraj release evidence

Branch: codex/haraj-user-connection. Paired Farq branch has the same name.

Before: request creation and customer replies queue deliveries sent by a dedicated central Taseer Haraj account; earlier shared-account conversations are polled by their central account.

After (flag on): the API refuses messaging before request/message/attachment/award/counter writes; workers return without claiming deliveries or updating sync metadata; adapters and already-cached central sessions cannot access central tokens. Password account connection, encrypted storage and refresh remain in the paired Farq API branch. This service contains no copied credentials and no new provider endpoints. Flag off preserves the existing channel.

Implemented locally; not merged, API/UI not deployed, no live journey verified. No live searches or sends, no billing/OAuth/production messaging flag changes. No user-owned messaging is implemented because both HARs omit its protocol. No API status response or successful build establishes provider acceptance, delivery or reply routing.

CI requires Python pytest plus Postgres-backed checks. Local results are appended after execution. GitHub CI is not dispatched until branches are pushed. The correspondence Node script and frontend build belong to the paired Farq repo; this Python backend has no Node correspondence script or frontend package for the consumer web app.

## Final local scope and validation

The user explicitly superseded legacy central-account preservation: central factory/login/refresh/environment/cache use is removed regardless of flag. The existing transport remains isolated legacy code with mocked regression checks; no real client can acquire a central session. New messaging adapter is still evidence-blocked. The UI sends an editable `supplier_message` and both SQLite/Postgres stores preserve it without rewriting as a Farq invite. No artificial user-owned outbound recipient cap is planned; existing central channel throttling code is not a new user-owned adapter.

Python 3.12.13 local normal CI test command (`-m "not integration"`): 1094 passed, 65 skipped (Postgres integration unavailable), 1 live-search test deselected. Scoped new tests 10/10. Missing pywebpush initially caused three environment failures; installed dependency and rerun succeeded. Obsolete central-session tests were replaced by no-env/no-cache/no-provider retirement checks; transport/worker receipt/reconciliation tests remain synthetic. No real PostgreSQL CI server was started; GitHub CI not run. No production versions changed. No sends performed by agent; one diagnostic message manually sent by user on own iPhone.

## Chat HAR received

The third iOS export contains 95 HTTP entries and 224 WebSocket frames.
Farq API now has the evidenced REST text/topic/read adapter, owner-bound
sessions and signed server delegation. Taseer has a server-only bridge with
signature/error tests. The worker remains disabled until consent migration,
owner/account-scoped reply routing, durable pacing and full journey tests are
complete; configuration alone must not activate old central-account queues.
No further messaging HAR is currently requested. Cookie-free compatibility
remains unverified. Targeted bridge/connection tests: 13 passed.

Latest normal local suite: 1097 passed, 65 PostgreSQL tests skipped, 1 live integration test deselected. No merge, deployment or new live send.

## Actual iPhone trial — 2026-10-07

The isolated per-user worker, consent, owner-scoped reply routing and exact buyer
text are implemented and deployed to the separate staging service. The normal
suite now passes **1105 tests**, with **65 PostgreSQL cases skipped** in that run.
A separate disposable real PostgreSQL suite passed **57 tests**. These are
distinct from a live provider delivery claim.

The customer reached the real native review screen for a plumbing RFQ and reported
that sending failed. Haraj connection/verification requests completed, then both
RFQ creation attempts failed with HTTP 500: `dataclasses.replace` received the
unset default Targeting value. This occurred before RFQ persistence or outbound
enqueue. Commit `36256be` initializes the factory default and includes a regression
test exercising request creation without an explicitly injected Targeting instance.
Targeted account/worker/broker tests: **20 passed**; the full normal suite passed
after the fix. The corrected staging deployment is being verified.

No resend was initiated. The native review selection was reduced to one recipient
to prepare a bounded canary. A new explicit approval for the named seller, current
RFQ and exact message is required before the agent presses Send. The provider
receipt, actual seller reply and native inbox/read persistence remain pending.
Neither paired branch is merged; no production service or production OTA changed.
