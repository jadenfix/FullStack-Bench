# 2. The ledger has one leader

Status: accepted (2025-03, Dana Okafor). Amended 2025-11 (follower mode).

We considered Postgres for bookings and decided against it for now. Bookings are an append-only
log, and the rails app's export was already one. ledgerd is one process with one file on a local
volume, which is fast and simple, but that volume lives on whichever node the pod first landed on.
Anything that moves the ledger has to move the data with it. Follower mode (1.4) exists for that.
