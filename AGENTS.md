# Agent notes

Rules for anyone (human or agent) changing this repo. The design lives in
`docs/PLAN.md`; decisions and tradeoffs go in `CHANGELOG.md`, change details
and validation in commit bodies.

## Secrets and visibility

- This repository is **public**. Keys live only in `.env` (gitignored, mode
  600). Never print, log or commit a key, and never pass one on a command line.
  Load `.env` in code. `.env.example` lists variable names only.
- The private held-out split never enters this repo (`private/` is ignored).
  It lives in a separate private store.
- Task content carries the canary; `instruction.md` never does.

## Publication

- Commit messages and pull-request titles/descriptions use plain language about
  the change and its validation. Do not mention coding assistants or coding agents, automated
  authorship, or tool co-authors; do not add generated-by signatures. Technical
  documentation must still identify the actual evaluation harness.
- Pull-request descriptions must be written by a human. Do not generate or
  rewrite the submitted description; use the human's supplied text verbatim.
  If no text has been supplied, finish and validate the code, then request it
  before opening or editing the pull request.

## Models

- Authoring and QA use the NVIDIA endpoint (`NVIDIA_API_BASE`). QA always uses
  a different model family from the task's author. Record `author_model` per
  task.
- Every candidate selected for the pilot must be evaluated with both the
  pinned mini-SWE scaffold and Rusty CLI. Keep their results separate and pin
  each harness revision, configuration, model, task revision and resource limits.
  mini-SWE remains the standardized primary track; Rusty is a required
  companion track. Selection runs are never reused as reporting runs.
- Rusty defaults to `agents=off` for the paired comparison. A different mode is
  a separate experiment and must not be pooled with this cohort.

## Task contract (see `docs/PLAN.md`)

- Harbor layout: `task.toml`, `instruction.md`, `environment/`, `solution/`,
  `tests/`. The verifier runs in separate mode and rebuilds the stack from
  declared artifacts on a fresh SimCloud.
- `instruction.md` follows the brief contract: every graded behaviour and every
  deciding condition is stated; implementation steps are not.
- Graded taste decisions come only from `taste/catalogue.yaml`.
- Docs drift comes only from `fsbench/drift.py`'s catalogue. Each entry needs a discoverability probe in `tests/test_drift.py`, and the base docs in `skills/simcloud` are never drifted.
- `skills/simcloud/reference/` is generated: run `uv run python scripts/gen_skill_docs.py` after changing kinds, actions, errors, CLI commands or MCP tools.
- Three scores per run, all deterministic, no LLM judge:
  - `reward` (binary, the headline): did they do it? The AND of the outcome checks, each also a sub-key.
  - `practices` (0-1): engineering practice on the agent's final repo against a pristine copy of
    the starting repo (`fsbench/quality.py`, configured by `tests/quality.toml`).
  - `style` (0-1): new lint, complexity and naming findings, diff noise and hygiene in changed files.
  `test.sh` writes `reward.txt` (binary) and `reward.json` (all three plus sub-keys). Run
  `scripts/sync_quality.py` after changing `fsbench/quality.py` or a task's repo.
- Gates before shipping: oracle 10× = 1, second oracle = 1, nop 3× = 0, wrong
  solutions and mutants = 0, verifier stable 3×, egress canary blocked, leak
  scan clean, human sign-off.

## Running things

- Every paid run is bounded: explicit timeouts, step and cost limits, attempt
  caps. Start with the smallest run that answers the question.
- Give every Harbor job a unique `--job-name`; Harbor deletes unfinished trials
  when a name is reused.
- Infrastructure errors not caused by the agent are recorded and replaced.
  Running out of the task budget, or resource exhaustion caused by the agent,
  is a failure.

## Workflow

- Small commits that each do one thing, each with a short `CHANGELOG.md` entry
  (what, why, tradeoffs) and validation in the commit body.
- Gate commits on the test command's own exit code, never on piped output.
- Don't add dependencies, frameworks or documents without a concrete need.
