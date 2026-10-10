# Paper source

Working title: *Beyond Final-State Correctness: Evaluating Live-System Changes by Coding
Agents*. One Markdown file per section, converted to the venue's format at submission (the
owner's default decision in `docs/PAPER_PLAN.md`). `docs/PAPER.md` is the skeleton these files
expand; `docs/PLAN.md` is the design they describe; `docs/PAPER_PLAN.md` is the schedule and
the review checklist.

Rules for every file here:

- No number is typed by hand. A result appears as `{{report.<key>}}`, naming a key in
  `docs/results/<cohort>/report.json` rendered by `scripts/report_cohort.py` from admitted
  terminal records, and carries that cohort's claim label (executed, reproduced, externally
  reported, estimated, planned). A sentence that would need a number the report does not
  produce is rewritten or dropped.
- Nothing about a cited work is asserted beyond what its published version says; every
  citation is marked `[verify]` until someone has checked it against that version.
- Method sections describe what the code does, in the present tense, and name the file that
  does it. They do not describe what the code will do.
- The adaptation, fidelity and workload tables are filled from `CHANGELOG.md`, `docs/PLAN.md`
  and each task's `task.toml`, never from memory.

| File | Section | Owner (M7) | State |
|---|---|---|---|
| `01-introduction.md` | 1 Introduction | B | drafted |
| `02-safe-success.md` | 2 SafeSuccess and the four views | B | drafted |
| `03-harm-attribution.md` | 3 Harm attribution and observation coverage | B | drafted |
| `04-evidence-path.md` | 4 Evidence path | B | drafted |
| `05-tasks.md` | 5 Tasks | B | drafted; receipt columns wait on M1 |
| `06-experiments.md` | 6 Experiments | A (6.1), B (6.2 to 6.6) | 6.1 outline for A; 6.6 drafted; 6.2 to 6.5 planned |
| `07-threats.md` | 7 Threats to validity | B | drafted; tables wait on evidence |
| `08-related-work.md` | 8 Related work | B | drafted, every citation `[verify]` |
| `09-rusty.md` | Rusty: description, safety assumptions, adaptation table, mechanism transfer | C | held until M5 (about 1,300 words drafted on Lane C's side) |
| `appendix-a-pilot.md` | A Development pilot | B | waits on the pilot-2b ledger |
| `appendix-b-receipts.md` | B Receipts | A | waits on M1 |
