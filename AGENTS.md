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

## Models

- Authoring and QA use the NVIDIA endpoint (`NVIDIA_API_BASE`). QA always uses
  a different model family from the task's author. Record `author_model` per
  task.
- Calibration and reporting use frontier models through the fixed
  mini-swe-agent scaffold. Selection runs are never reused as reporting runs.

## Task contract (see `docs/PLAN.md`)

- Harbor layout: `task.toml`, `instruction.md`, `environment/`, `solution/`,
  `tests/`. The verifier runs in separate mode and rebuilds the stack from
  declared artifacts on a fresh SimCloud.
- `instruction.md` follows the brief contract: every graded behaviour and every
  deciding condition is stated; implementation steps are not.
- Graded taste decisions come only from `taste/catalogue.yaml`.
- Binary reward (AND of all checks), with each check reported as a sub-key.
  No LLM judge in the reward.
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
