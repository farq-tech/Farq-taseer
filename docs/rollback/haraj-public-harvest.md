# Haraj public harvest rollback

Target: Supabase farq-main (mpgbvtaguerncgbzvpwg), private taseer schema. This change does not alter the comparison schema, account vault, subscriptions or messaging.

Set FARQ_HARAJ_PUBLIC_HARVEST_ENABLED=0 in the Taseer production deployment and redeploy the same revision. The scheduled route becomes a no-op and paged search reverts to public live Search. Keep all four additive haraj_public_* tables and their saved observations. Reverting the API/UI commits is also safe after the flag is disabled; neither deletes archive data. Do not drop tables as a routine rollback.

Predeployment rehearsal: schema migration and leased continuation tested on disposable Postgres, plus source failure/429/challenge preserving archived details and last-success. To resume an operator-resolved public-source challenge, inspect actual source evidence before resetting the server-only control row; never reset to evade a challenge.
