# changes

- 2026.09.3: kiosk v0 payload accepts `n` for party size
- 2026.09.2: faster shutdown (PLAT-212): don't wait out the grace period
- 2026.08.1: readiness checks the ledger, so a pod that can't reach it leaves rotation
- 2026.07.4: pinned api to zone-2 next to the ledger while we chased p99 (temporary!)
- 2026.06.1: removed SETTINGS_FILE
- ledger 1.4.2: follower mode, freeze/promote
