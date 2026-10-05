# Changelog

## 2026-10-04: Model smoke test and default author

- What: `scripts/smoke_models.py` makes one bounded call per candidate model
  (120 s timeout, no retries; keys read from `.env`). The prompt is a tiny
  structured-output instruction with a known answer.
- Result:
  - `z-ai/glm-5.3`: correct JSON, about 44 s. Now the default author.
  - `nvidia/nemotron-3-ultra-550b-a55b`: correct JSON, about 6 s. Second author and QA family.
  - `moonshotai/kimi-k3`: empty content twice (finish `stop`, about 33 reasoning tokens).
  - `deepseek-ai/deepseek-v4.1-flash`: timed out twice.
- Tradeoffs: the determinacy panel needs three model families, and only two
  work today. Kimi-K3 and DeepSeek are re-checked before each batch rather
  than worked around.

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
