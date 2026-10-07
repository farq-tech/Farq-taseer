# Legacy shared-account inbox isolation

Per-user reads require the RFQ's original consent to bind the Farq owner, provider account and selected recipient. Historical shared-account threads have no such evidence and remain explicit consent failures; they never borrow newly connected credentials or compete for routing a new reply. No history is deleted and no send is replayed.

A regression creates two owners' historical threads and one consented current thread in the same provider conversation. Only the consented owner/request is read and receives the supplier offer.
