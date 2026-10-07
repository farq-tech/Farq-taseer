# Membership authority for per-user Haraj

When the per-user Haraj connection and central credit ledger are enabled, every RFQ item consumes the Farq membership balance through the existing owner-bound, idempotent billing contract. A legacy Taseer unlimited flag is not an exemption from membership credits.

The displayed allowance uses the same central balance. Existing flag-off behavior remains intact. Historical requests are not retroactively billed or sent by this change. Tests exercise one debit on idempotent request replay and refusal before provider sending when the balance is empty.
