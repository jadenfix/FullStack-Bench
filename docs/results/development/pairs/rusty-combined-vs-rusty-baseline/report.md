# Cohort report: development

Claim label: **exploratory (development cohort; not admitted)**. Tasks: ship-checkout-v2, stop-double-charges. FullStack-Bench revisions: 2bf96753040c1bf44c9cf5212c6261b1fd5bc382. Records: 20.

Proportions are over eligible attempts with a 95% Wilson interval; a view with a check that did not run is unknown, not failed.

## Per harness

| Harness | Eligible / planned | SafeSuccess | Measurement eligible | SafeSuccess among measured | Accepted → independent | Accepted w/o success | Harm observed | Infra / invalid / missing |
|---|---|---|---|---|---|---|---|---|
| mini | 3 / 4 | 1/3 = 0.33 [0.06, 0.79] | 3/3 = 1.00 [0.44, 1.00] | 1/3 = 0.33 [0.06, 0.79] | n/a (0) | 0 | 1 (unobserved 1) | 0 / 0 / 1 |
| rusty-baseline | 3 / 4 | 2/3 = 0.67 [0.21, 0.94] | 3/3 = 1.00 [0.44, 1.00] | 2/3 = 0.67 [0.21, 0.94] | 2/3 = 0.67 [0.21, 0.94] | 1 | 1 (unobserved 1) | 0 / 0 / 1 |
| rusty-careful | 3 / 4 | 1/3 = 0.33 [0.06, 0.79] | 3/3 = 1.00 [0.44, 1.00] | 1/3 = 0.33 [0.06, 0.79] | 1/2 = 0.50 [0.09, 0.91] | 1 | 1 (unobserved 1) | 0 / 0 / 1 |
| rusty-combined | 3 / 4 | 3/3 = 1.00 [0.44, 1.00] | 3/3 = 1.00 [0.44, 1.00] | 3/3 = 1.00 [0.44, 1.00] | 3/3 = 1.00 [0.44, 1.00] | 0 | 0 (unobserved 1) | 0 / 0 / 1 |
| rusty-verify | 3 / 4 | 2/3 = 0.67 [0.21, 0.94] | 3/3 = 1.00 [0.44, 1.00] | 2/3 = 0.67 [0.21, 0.94] | 2/3 = 0.67 [0.21, 0.94] | 1 | 1 (unobserved 1) | 0 / 0 / 1 |

## Four views (eligible attempts; unknown = a check did not run)

| Harness | final_artifact | deployed_at_handoff | whole_episode | recovery |
|---|---|---|---|---|
| mini | 2/3 = 0.67 [0.21, 0.94] (unknown 0) | 2/3 = 0.67 [0.21, 0.94] (unknown 0) | 2/3 = 0.67 [0.21, 0.94] (unknown 0) | 2/3 = 0.67 [0.21, 0.94] (unknown 0) |
| rusty-baseline | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 2/3 = 0.67 [0.21, 0.94] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) |
| rusty-careful | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 2/3 = 0.67 [0.21, 0.94] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) |
| rusty-combined | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) |
| rusty-verify | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) | 2/3 = 0.67 [0.21, 0.94] (unknown 0) | 3/3 = 1.00 [0.44, 1.00] (unknown 0) |

## Failure classes and budget

| Harness | operator_setup | coverage_limitation | harness | solver | Coverage limited | Budget exhausted | Calls | Input tokens | Output tokens |
|---|---|---|---|---|---|---|---|---|---|
| mini | 0 | 0 | 0 | 2 | 0 | 1 | 580 | 18560464 | 150676 |
| rusty-baseline | 0 | 0 | 0 | 1 | 0 | 0 | 277 | 8473700 | 266581 |
| rusty-careful | 0 | 0 | 0 | 2 | 0 | 1 | 456 | 18633936 | 373555 |
| rusty-combined | 0 | 0 | 0 | 0 | 0 | 0 | 415 | 11524225 | 352255 |
| rusty-verify | 0 | 0 | 0 | 1 | 0 | 0 | 257 | 7539491 | 226502 |

## Termination (every recorded attempt)

| Harness | n | handoff | budget exhausted (calls / input / output / wall / other) | harness stop | infrastructure | unknown |
|---|---|---|---|---|---|---|
| mini | 3 | 2 | 1 (1 / 0 / 0 / 0 / 0) | 0 | 0 | 0 |
| rusty-baseline | 3 | 3 | 0 (0 / 0 / 0 / 0 / 0) | 0 | 0 | 0 |
| rusty-careful | 3 | 2 | 1 (0 / 1 / 0 / 0 / 0) | 0 | 0 | 0 |
| rusty-combined | 3 | 3 | 0 (0 / 0 / 0 / 0 / 0) | 0 | 0 | 0 |
| rusty-verify | 3 | 3 | 0 (0 / 0 / 0 / 0 / 0) | 0 | 0 | 0 |

## Attempts active at admitted call k (eligible attempts)

| Harness | n | median calls | max calls | cap | active at k |
|---|---|---|---|---|---|
| mini | 3 | 177 | 240 | 240 | 0:3 10:3 20:3 30:3 40:3 50:3 60:3 70:3 80:3 90:3 100:3 110:3 120:3 130:3 140:3 150:3 160:3 170:2 180:1 190:1 200:1 210:1 220:1 230:1 240:1 |
| rusty-baseline | 3 | 86 | 116 | 240 | 0:3 10:3 20:3 30:3 40:3 50:3 60:3 70:3 80:2 90:1 100:1 110:1 120:0 130:0 140:0 150:0 160:0 170:0 180:0 190:0 200:0 210:0 220:0 230:0 240:0 |
| rusty-careful | 3 | 129 | 227 | 240 | 0:3 10:3 20:3 30:3 40:3 50:3 60:3 70:3 80:3 90:3 100:3 110:2 120:2 130:1 140:1 150:1 160:1 170:1 180:1 190:1 200:1 210:1 220:1 230:0 240:0 |
| rusty-combined | 3 | 132 | 187 | 240 | 0:3 10:3 20:3 30:3 40:3 50:3 60:3 70:3 80:3 90:3 100:2 110:2 120:2 130:2 140:1 150:1 160:1 170:1 180:1 190:0 200:0 210:0 220:0 230:0 240:0 |
| rusty-verify | 3 | 83 | 118 | 240 | 0:3 10:3 20:3 30:3 40:3 50:3 60:2 70:2 80:2 90:1 100:1 110:1 120:0 130:0 140:0 150:0 160:0 170:0 180:0 190:0 200:0 210:0 220:0 230:0 240:0 |

## Trajectory (eligible attempts with a trajectory summary; shares of tool calls)

| Harness | Summarised / missing | Calls | explore | edit | run | verify | other | Shell share | Out-of-interface calls (attempts ending on one; interface unknown) | Without edit | Repatches mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mini | 0 / 3 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a (n/a; n/a) | n/a | n/a |
| rusty-baseline | 0 / 3 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a (n/a; n/a) | n/a | n/a |
| rusty-careful | 0 / 3 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a (n/a; n/a) | n/a | n/a |
| rusty-combined | 0 / 3 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a (n/a; n/a) | n/a | n/a |
| rusty-verify | 0 / 3 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a (n/a; n/a) | n/a | n/a |

## Compactions (what followed each boundary, first five calls)

| Harness | Attempts with one | Compactions (by trigger) | Repeated calls after | First-step errors | Re-reads after | Unknown |
|---|---|---|---|---|---|---|
| mini | n/a | n/a | n/a | n/a | n/a | n/a |
| rusty-baseline | n/a | n/a | n/a | n/a | n/a | n/a |
| rusty-careful | n/a | n/a | n/a | n/a | n/a | n/a |
| rusty-combined | n/a | n/a | n/a | n/a | n/a | n/a |
| rusty-verify | n/a | n/a | n/a | n/a | n/a | n/a |

## Cost estimate (charged tokens at the manifest's pricing)

| Harness | USD | Input $/M | Output $/M | Attempts priced | Source |
|---|---|---|---|---|---|
| mini | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-baseline | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-careful | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-combined | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-verify | n/a | n/a | n/a | 0 | no pricing block in the manifest |

## Paired: rusty-combined vs rusty-baseline on 3 shared (task, seed) slots

| Outcome | rusty-combined only | rusty-baseline only | both | neither | unknown | sign test p |
|---|---|---|---|---|---|---|
| SafeSuccess | 1 | 0 | 2 | 0 | 0 | 1.000 |
| final_artifact | 0 | 0 | 3 | 0 | 0 | n/a |
| deployed_at_handoff | 0 | 0 | 3 | 0 | 0 | n/a |
| whole_episode | 1 | 0 | 2 | 0 | 0 | 1.000 |
| recovery | 0 | 0 | 3 | 0 | 0 | n/a |

paired slots span 2 task(s); the sign test treats slots as exchangeable, which seeds within one task are not, so read it as descriptive.
