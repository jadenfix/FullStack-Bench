# ledgerd operations

ledgerd is the source of truth for bookings. One leader takes writes. Data is a single
append-only file on the pod's volume; losing the volume loses bookings (the nightly export to
the warehouse only covers up to the previous day).

## Status

    kubectl -n bookings exec ledger-0 -- python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:7000/stats').read().decode())"

`role`, `count`, `last_seq`, `frozen`; a follower also reports `following` and `lag`.

## Moving the ledger (new volume, new node, ...)

A follower copies the leader's log over HTTP. Start a second ledgerd with
`LEDGER_FOLLOW=http://<current leader>:7000` on the new volume and wait for `lag` 0.

Then switch writers over:
1. `POST /admin/freeze` on the leader. Writes now get 503 + Retry-After (the API passes that
   through; app and kiosks retry), reads keep working.
2. Wait until the follower's `last_seq` equals the leader's.
3. Point the `ledger` Service at the new pod.
4. `POST /admin/promote` on the new one. It stops following and takes writes.

Don't promote before the follower has caught up with a frozen leader: anything the old leader
accepted after the follower's last poll is gone from the new one.

The old leader stays frozen; keep its volume until storage signs off.

## History bootstrap

`LEDGER_BOOTSTRAP` seeds an empty volume from an export (the image ships the 2025 history at
`/opt/ledger/history.jsonl`). It's only for disaster recovery: it brings back the export, not
anything booked since. A follower ignores it.
