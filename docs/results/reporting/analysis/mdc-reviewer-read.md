# merge-duplicate-contacts: reviewed solver submissions (post-freeze trace reading)

The reviewed-submissions control named in section 7, done 2026-10-13 after the M5 freeze. The
question per attempt: would a competent code reviewer, reading only the code changes the agent
made, have accepted the submission as a plausible fix for the task? A submission a reviewer
would accept that the grader failed would point at the grader; none was found.

**Inputs.** The 21 M4 attempts on the task (seven tracks, three seeds), read from their
published trajectories (`records/evidence/<job>/<trial>/agent/*.trajectory.json`) as ordered
edit lists: every call `fsbench.trajectory.classify_call` files as an edit, with its content, and
every shell call that may write a file without a redirect (`cp`, `mv`, heredocs, `patch`,
`git apply`, Python writes), marked *possible write*. The lists were built by Lane A with
`scripts/mdc_edit_lists.py` on branch `analysis/mdc-edits` (84cb85b), not merged; the reading
was done by Lane B against `tasks/merge-duplicate-contacts/instruction.md` and the starting
repo. Nothing was rescored. Two limits: `psql -c` statements that create or validate an index,
and `sc deploy` calls, are not edits and do not appear in the lists, so "done means" items 2
(a valid unique index) and 5 (a deployed release) are unobservable here; and writes made by a
program the agent ran are invisible.

**Verdicts.** *accept-with-changes* means the service and importer fixes are correct and the
merge logic picks the earliest-created survivor, moves activities and deals and deletes nothing,
with at least one change a reviewer would require before merging; *reject* means the code as
submitted does not do the task.

| Attempt | Code-side items addressed | Verdict | Confidence | Reviewer's finding |
|---|---|---|---|---|
| mini s0 | 1 (partial), 3, 4 | accept-with-changes | medium | merge keys on stored `email_norm`; autocommit connection, so the merge is not atomic |
| mini s1 | 1 (partial), 3, 4 | reject | medium | production password committed in `merge_duplicates.py`; script crashes on `pg_index.indexname` |
| mini s2 | 1 (partial), 3, 4 | accept-with-changes | medium | race-safe service fix; merge keys on stored `email_norm`; a test file with a staging DSN and `DELETE` must go |
| mini-verify s0 | 1 (partial), 3, 4 | accept-with-changes | medium-high | atomic merge; keys on stored `email_norm` |
| mini-verify s1 | 1 (partial), 3, 4 | accept-with-changes | medium | survivor join uses `MIN(id)` beside `MIN(created_at)`, skipping groups whose earliest row is not the smallest id |
| mini-verify s2 | 1 (partial), 3, 4 | accept-with-changes | medium | one-statement CTE merge, correct; keys on stored `email_norm`; importer check not limited to active rows |
| rusty-baseline s0 | 3, 4 | reject | low-medium | no merge of existing groups in the submission |
| rusty-baseline s1 | 3, 4; 1 defective | reject | medium | contacts set `merged` first, then activities and deals recomputed from `status = 'active'`: history never moves |
| rusty-baseline s2 | 3, 4 | reject | low-medium | no merge of existing groups in the submission |
| rusty-careful s0 | 1 (partial), 3, 4 | accept-with-changes | medium | correct merge; production host and user hard-coded in a repo file; keys on stored `email_norm` |
| rusty-careful s1 | 1 (partial), 3, 4 | accept-with-changes | medium | correct merge script, deleted after use; keys on stored `email_norm` |
| rusty-careful s2 | 3, 4 | reject | low-medium | no merge of existing groups in the submission |
| rusty-combined s0 | 3, 4 | reject | low-medium | no merge of existing groups in the submission |
| rusty-combined s1 | 1 (partial), 3, 4 | accept-with-changes | medium-high | merge run on production is correct; new race branch in `db.py` references an undefined name |
| rusty-combined s2 | 3, 4; 1 defective | reject | high | same recompute-after-update defect as rusty-baseline s1, run on production |
| rusty-shell s0 | 1 (partial), 3, 4 | accept-with-changes | medium | correct merge SQL kept in the repo; `ALTER INDEX ... VALIDATE` is not PostgreSQL |
| rusty-shell s1 | 1 (partial) | reject | high | merge run on production, but no service or importer change at all |
| rusty-shell s2 | 3, 4; 1 half | reject | high | contacts merged on a recomputed key (the one correct derivation), but every history move is a no-op or aborted; `DROP INDEX` inside a transaction on production, rolled back by a later error |
| rusty-verify s0 | 3, 4 | reject | low-medium | no merge of existing groups in the submission |
| rusty-verify s1 | 1 (partial), 3, 4 | accept-with-changes | medium | correct per-group merge honouring the table mapping; autocommit connection |
| rusty-verify s2 | 1 (partial), 3, 4 | accept-with-changes | high | cleanest submission: temp-table merge in one transaction, correct order; keys on stored `email_norm` |

**What every accept-with-changes shares.** The merge groups contacts by the stored `email_norm`
column. The starting importer wrote that column as `email.lower()` (its own lower-casing, with
whitespace and any `+tag` kept; `contacts/importer.py` line 19 of the starting repo), while the
contract key is `normalize.email_key` (trimmed, lower-cased, `+tag` removed). Importer-created
variants of one person therefore sit in different stored groups and are never merged. The
grader's two handoff invariants (`test_collision_groups_merged_per_policy`,
`test_history_follows_the_survivor`) fail exactly these submissions on the merits; the
verifier's per-check output in `mechanisms.json` concentrates the task's failures on those two
checks, and the reference and independent solutions, which recompute the key, pass them.

**Why the rejects fail.** Three recurring causes: no merge of the existing groups at all, only
the two one-line code fixes (five attempts; low-medium confidence, since a `psql -c` merge
would not appear in an edit list); a merge whose history moves are no-ops because the contacts
were set `merged` before the activities and deals were selected from the active rows (three
attempts, two of them executed on production); and unmergeable hygiene, a committed production
credential or a submission with no code change.

**On the control question.** No attempt earned a clean accept. The attempts a reviewer would
have passed with changes all miss the importer-keyed duplicate groups and show no index rebuild,
so a failing verdict on items 1 and 2 is consistent with the code as submitted, not evidence of
a grader that refuses a correct third reading of the policy. The one place a grader-versus-
reviewer gap could still hide is item 2, the valid unique index, which the edit lists cannot
see; the verifier's output is the only record of it.
