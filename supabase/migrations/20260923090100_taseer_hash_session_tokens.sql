-- NOT YET APPLIED. Apply only AFTER the code that hashes session tokens is deployed.
-- (Applied earlier, the old code would no longer find any session and everyone would be
-- signed out once - harmless, but avoidable.)
-- TSR-034: sessions keep sha256(token) instead of the bearer token. New sessions are already
-- stored hashed by the code, and the code rehashes a raw token on its next use; this finishes
-- the job for sessions nobody has used since, so no raw token stays at rest.
-- Raw tokens are 43-character url-safe strings; digests are 64 hex characters.
update taseer.sessions
set token = encode(sha256(convert_to(token, 'UTF8')), 'hex')
where length(token) <> 64;
