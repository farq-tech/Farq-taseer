# Subscriptions & payments — setup status

This documents exactly what is wired up, what is configured, and what still
needs a human with dashboard access. Nothing below is guessed; every secret
name is a real env var the code already reads (`src/farq/config.py`,
`PaymentsConfig`).

## 1. Domain: taseer.farq.sa (blocking)

`taseer.farq.sa` (and `saer.farq.sa`, which this project must never use) are
currently attached to the **`farq-construction-staging`** Vercel project
(a Vite app), not to `farq-taseer`. This was found during setup, not caused
by this change. No traffic-serving action was taken on that project - domain
reassignment was confirmed with you but there is no MCP tool available here
to detach a domain from a project, so this one step must be done by hand:

1. Vercel dashboard → `farq-construction-staging` project → Settings →
   Domains → remove `taseer.farq.sa`.
2. Vercel dashboard → `farq-taseer` project → Settings → Domains → add
   `taseer.farq.sa` (or ask the assistant to call `add_project_domain` again
   - it will succeed once step 1 is done).
3. Verify DNS: Vercel will show the exact A/CNAME record it expects at the
   registrar for `farq.sa`.

Until this is done, `farq-taseer` is only reachable at its `*.vercel.app`
URLs (see the report for the current one).

## 2. Database (blocking - subscriptions cannot run without this)

Production must use Postgres, not the local SQLite file (`store.py`), which
lives on Vercel's ephemeral `/tmp` and is wiped on every cold start / spread
across instances - see `create_default_app()` in `src/farq/api.py`, which
now **refuses to boot on Vercel without `DATABASE_URL`** for exactly this
reason.

The schema already exists: a dedicated `taseer` schema inside the existing
**`farq-main`** Supabase project (`mpgbvtaguerncgbzvpwg`), fully isolated
from Farq Compare's tables (its own schema, RLS enabled, zero shared
tables). A brand-new Supabase project was attempted first but the
`create_project` call timed out twice with nothing created - `farq-main` was
already Pro-tier and healthy, so this was the safer path rather than
retrying a flaky provisioning call.

What's needed: the Postgres connection string (with password), which this
session's Supabase access does not expose:

1. Supabase dashboard → `farq-main` project → Project Settings → Database →
   Connection string → **Transaction pooler** (port 6543, recommended for
   serverless).
2. Add it to Vercel as `DATABASE_URL` (Production + Preview), **as an
   Encrypted/Sensitive env var**, not plain text.

Once set, `PgStore` (`src/farq/store_pg.py`) takes over automatically - no
code change needed.

## 3. Moyasar keys (blocking - payments cannot run without this)

None of these were invented; the account already exists per your message,
but no key was available in this session:

| Env var | Where to get it | Secret? |
|---|---|---|
| `MOYASAR_SECRET_KEY` | Moyasar dashboard → Settings → API Keys → **Secret key** (use the **test** key first) | Yes - server only |
| `MOYASAR_PUBLISHABLE_KEY` | Same page → **Publishable key** | No (safe client-side), but still set as an env var, not hardcoded |
| `MOYASAR_WEBHOOK_SECRET` | Moyasar dashboard → Settings → Webhooks → create a webhook pointing to `https://taseer.farq.sa/v1/payments/moyasar/webhook`, copy its secret token | Yes |

Set all three on Vercel (`MOYASAR_SECRET_KEY` and `MOYASAR_WEBHOOK_SECRET`
as Encrypted; `MOYASAR_PUBLISHABLE_KEY` can be plain).

Until `MOYASAR_PUBLISHABLE_KEY` is set, `/v1/subscriptions/checkout`
returns a clear 422 ("payments are not configured yet") instead of a broken
payment form - verified locally.

## 4. Apple Pay (blocking parts require Apple Developer access)

What's ready in code:
- `PaymentsConfig.apple_pay_merchant_id` reads `APPLE_PAY_MERCHANT_ID`.
- The static file server (`web/{full_path}` route in `api.py`) already
  serves anything placed under `web/`, including a `.well-known/` folder -
  no route changes needed once the verification file exists.
- The subscribe page (`web/app.js`) only shows the Apple Pay button when
  `window.ApplePaySession.canMakePayments()` is true, and passes
  `methods: ['creditcard', 'applepay']` to Moyasar.js only in that case.

What needs you / Apple Developer access (cannot be done from here):
1. Apple Developer account → Certificates, Identifiers & Profiles →
   Merchant IDs → create one, e.g. `merchant.sa.farq.taseer`.
2. In the same Merchant ID, create a **Payment Processing Certificate** and
   upload it to Moyasar's dashboard (Moyasar → Settings → Apple Pay) - this
   is what lets Moyasar perform merchant validation on your behalf.
3. Apple Developer → the Merchant ID → **Domains** → add `taseer.farq.sa`
   and download the domain association file. Save it at:
   `web/.well-known/apple-developer-merchantid-domain-association`
   (exact filename, no extension) and deploy. Do this only after step 1 of
   the domain section above, since Apple will fetch it from the live domain
   to verify.
4. Set `APPLE_PAY_MERCHANT_ID` on Vercel to the identifier from step 1.

Apple Pay cannot be tested end-to-end without steps 1-3.

## 5. In-App Purchase question (Phase 10)

This repository has no Capacitor/iOS wrapper - it is a plain web app
(`web/index.html` + `web/app.js`, no native shell found anywhere in this
repo). So the App Store IAP-vs-Apple-Pay distinction the task raised does
not currently apply: there is nothing being distributed through the App
Store to review. If a separate Capacitor/iOS project for Taseer exists in
another repository, it wasn't in this session's scope (only
`farq-tech/Farq-taseer` was) - point it out and it can be reviewed
separately, since wrapping this exact web subscription flow in a native
shell *would* trigger Apple's IAP requirement for digital subscriptions.

## 6. The placeholder price

`subscription_plans.monthly_placeholder` was seeded with 100 halalas
(1 SAR) and `is_placeholder_price = true` in both the dev SQLite schema and
the Supabase `taseer` schema, purely so the full flow could be exercised in
Sandbox. The UI marks it visibly as a placeholder. Nothing should charge a
real user at this price - update `subscription_plans` (one row, one place)
with the real plan(s) before going live; nothing else in the codebase
hardcodes a price.
