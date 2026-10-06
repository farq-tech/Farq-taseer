# Haraj user account boundary (proposed)

2026-10-06. Feature flag: `HARAj_USER_ACCOUNT_CONNECTION`, default off.

Farq API owns the encrypted per-Farq-user credential vault. Taseer must not receive raw provider tokens or fall back to central Haraj accounts when the flag is on. Its local user id is distinct from the linked `users.farq_user_id`; future authenticated requests must resolve that linkage server-side and bind request owner, connection, item and consent before dispatch.

The two supplied HARs contain password auth/refresh/logout/profile but no private-message transport. The second has 136 entries/22 operations; all requests are POST ios.haraj.sa. `postContact` retrieves ad contact information. Legacy chat code is not new HAR evidence. Therefore flag-on customer send routes return a bounded 501 before writes/billing; workers stop before queue claims/sync timestamp changes; cached central sessions cannot provide tokens. The user subsequently required complete central-account removal, so the environment factory and central session now ignore all former credentials and caches regardless of flag. Other channel behavior stays intact. Existing inbox contents and sync metadata remain available, never replaced by a false successful empty sync.

Replace this release boundary only after actual message-send/receive evidence exists. Owner-specific send/read adapters must be added to the existing delivery queue and inbox, with explicit RFQ consent, reconciliation of uncertain sends and credential-free audits. No artificial recipient/message-count cap for normal contact from the user's own account; maintain configurable pacing and honor provider restrictions/challenges. No production flag changes were performed.

Capture api-chat.haraj.com.sa as well as ios.haraj.sa, including WebSocket frames. Open ad → private message → test send → inbox → seller reply → second reply → export full request/response bodies and separate WebSocket frame export if HAR omits them.

User-authored `supplier_message` passes from the review UI to both stores verbatim. Future owner-scoped sender must bypass legacy invite/reference rendering. Central credential login/refresh/cache code was removed, not merely hidden behind the new feature flag.

Third HAR satisfies the missing plain-text messaging evidence. REST calls
belong to Farq API; Taseer delegates signed owner/RFQ operations and never
receives provider credentials. Keep workers closed until existing central
queues cannot be replayed under user accounts and new consent is persisted.
