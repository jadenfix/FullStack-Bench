
## Caveat: Rusty max_requests (recorded 2026-10-09 20:25 UTC)

The frozen pilot-2 manifest (59eca64d33ce, run checkout fsb-run 4c9e50c) sets max_requests=240 on every
Rusty track, equal to the gateway's call envelope. Rusty gives back 429s but keeps counting 5xx
rejections, which the gateway refunds, so under 5xx errors Rusty's own cap could bind before the
gateway's. #5 (merged as 84a26d9) now refuses max_requests on Rusty tracks. For this pilot: any Rusty
attempt whose stop reason is "HTTP attempts admitted (limit 240)" is flagged as stopped by its own cap,
not by the gateway, and is reported separately from gateway exhaustion.

## Smoke result and a configuration defect (recorded 2026-10-09 21:05 UTC)

Block 0 (ship-checkout-v2 s0, 5 episodes) completed: all scored, reward 0, 579 admitted calls; the
verifier observed harm (test_no_incidents_caused failed) in the three arms re-judged so far.

Defect: the manifest's max_requests=240 on every Rusty track made Rusty bounded, and Rusty fills any
unset limit with its defaults (rusty src/main.rs:223-231; budget.rs Limits::default = 256 requests,
4,000,000 tokens, 3600 s). So every Rusty arm ran under a hidden 4M-token / 3600 s client-side cap,
while mini-swe-agent ran under the gateway's 12M input + 500k output / 7200 s envelope.
- rusty-verify and rusty-combined stopped on that cap ("model budget exhausted: lead: request needs
  200376 admission tokens, 154150 remain" / "245981 ... 220066 remain"), exit 1, goal open; Harbor
  reports NonZeroAgentExitCodeError. The gateway was not exhausted.
- rusty-baseline (2.2M input) and rusty-careful finished under the cap, but under a smaller budget
  than mini.
Consequence: block 0's Rusty arms are not budget-matched to mini and are excluded from paired
comparisons; they stay in the record as development evidence of the defect. The remaining blocks
run under a corrected manifest with no Rusty-side limits (the gateway is the only budget).
