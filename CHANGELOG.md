# Changelog

## 2026-10-04: Bootstrap

- What: repo skeleton. `.gitignore` (secrets, run state, private split),
  `.env.example`, `AGENTS.md` / `CLAUDE.md` rules, and the design in
  `docs/PLAN.md`.
- Why: the benchmark needs its rules in place before any paid run. The repo
  is public, so secrets and the private held-out split are excluded from the
  first commit onward.
- Tradeoffs:
  - SimCloud, a simulated provider-neutral platform, is used instead of real
    vendor clouds or emulators. It tests judgement and engineering rather than
    vendor-API recall, and it resists contamination. The cost is the
    engineering to build it, plus a transfer risk that a later study measures.
