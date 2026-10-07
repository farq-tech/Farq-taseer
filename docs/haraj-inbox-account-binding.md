# Haraj inbox account binding

Reply sync must carry the stored provider account identifier alongside the conversation and Farq owner. The shared SQLite/Postgres conversation projection previously omitted it, so the signed bridge rejected reads even after a successful send.

Current conversations retain their stored account. Legacy conversations without independently stored account evidence receive no current-account credentials and fail closed with an ownership error. Missing or refused reads remain visible failures; successful sync timestamps are never fabricated. This change does not send or replay messages.
