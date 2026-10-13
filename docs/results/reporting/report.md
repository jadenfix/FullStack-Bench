# Cohort report: reporting

Claim label: **executed**. Tasks: merge-duplicate-contacts, ship-checkout-v2, stop-double-charges, stop-report-connection-leak. FullStack-Bench revisions: a7f08b046f39b66ec9b592a7116762cb367267c5. Records: 84.

Proportions are over eligible attempts with a 95% Wilson interval; a view with a check that did not run is unknown, not failed.

## Per harness

| Harness | Eligible / planned | SafeSuccess | Measurement eligible | SafeSuccess among measured | Accepted → independent | Accepted w/o success | Harm observed | Infra / invalid / missing |
|---|---|---|---|---|---|---|---|---|
| mini | 12 / 12 | 2/12 = 0.17 [0.05, 0.45] | 12/12 = 1.00 [0.76, 1.00] | 2/12 = 0.17 [0.05, 0.45] | n/a (0) | 0 | 4 (unobserved 0) | 0 / 0 / 0 |
| mini-verify | 12 / 12 | 4/12 = 0.33 [0.14, 0.61] | 12/12 = 1.00 [0.76, 1.00] | 4/12 = 0.33 [0.14, 0.61] | 4/10 = 0.40 [0.17, 0.69] | 6 | 3 (unobserved 0) | 0 / 0 / 0 |
| rusty-baseline | 12 / 12 | 5/12 = 0.42 [0.19, 0.68] | 12/12 = 1.00 [0.76, 1.00] | 5/12 = 0.42 [0.19, 0.68] | 5/7 = 0.71 [0.36, 0.92] | 2 | 1 (unobserved 0) | 0 / 0 / 0 |
| rusty-careful | 12 / 12 | 4/12 = 0.33 [0.14, 0.61] | 12/12 = 1.00 [0.76, 1.00] | 4/12 = 0.33 [0.14, 0.61] | 4/10 = 0.40 [0.17, 0.69] | 6 | 2 (unobserved 0) | 0 / 0 / 0 |
| rusty-combined | 11 / 12 | 4/11 = 0.36 [0.15, 0.65] | 11/11 = 1.00 [0.74, 1.00] | 4/11 = 0.36 [0.15, 0.65] | 4/8 = 0.50 [0.22, 0.78] | 4 | 2 (unobserved 0) | 0 / 1 / 0 |
| rusty-shell | 12 / 12 | 2/12 = 0.17 [0.05, 0.45] | 12/12 = 1.00 [0.76, 1.00] | 2/12 = 0.17 [0.05, 0.45] | 2/4 = 0.50 [0.15, 0.85] | 2 | 4 (unobserved 0) | 0 / 0 / 0 |
| rusty-verify | 12 / 12 | 5/12 = 0.42 [0.19, 0.68] | 12/12 = 1.00 [0.76, 1.00] | 5/12 = 0.42 [0.19, 0.68] | 5/10 = 0.50 [0.24, 0.76] | 5 | 2 (unobserved 0) | 0 / 0 / 0 |

## Four views (eligible attempts; unknown = a check did not run)

| Harness | final_artifact | deployed_at_handoff | whole_episode | recovery |
|---|---|---|---|---|
| mini | 9/12 = 0.75 [0.47, 0.91] (unknown 0) | 7/12 = 0.58 [0.32, 0.81] (unknown 0) | 8/12 = 0.67 [0.39, 0.86] (unknown 0) | 6/12 = 0.50 [0.25, 0.75] (unknown 0) |
| mini-verify | 10/12 = 0.83 [0.55, 0.95] (unknown 0) | 8/12 = 0.67 [0.39, 0.86] (unknown 0) | 9/12 = 0.75 [0.47, 0.91] (unknown 0) | 7/12 = 0.58 [0.32, 0.81] (unknown 0) |
| rusty-baseline | 8/12 = 0.67 [0.39, 0.86] (unknown 0) | 8/12 = 0.67 [0.39, 0.86] (unknown 0) | 11/12 = 0.92 [0.65, 0.99] (unknown 0) | 8/12 = 0.67 [0.39, 0.86] (unknown 0) |
| rusty-careful | 9/12 = 0.75 [0.47, 0.91] (unknown 0) | 7/12 = 0.58 [0.32, 0.81] (unknown 0) | 10/12 = 0.83 [0.55, 0.95] (unknown 0) | 8/12 = 0.67 [0.39, 0.86] (unknown 0) |
| rusty-combined | 9/11 = 0.82 [0.52, 0.95] (unknown 0) | 8/11 = 0.73 [0.43, 0.90] (unknown 0) | 9/11 = 0.82 [0.52, 0.95] (unknown 0) | 6/11 = 0.55 [0.28, 0.79] (unknown 0) |
| rusty-shell | 9/12 = 0.75 [0.47, 0.91] (unknown 0) | 4/12 = 0.33 [0.14, 0.61] (unknown 0) | 8/12 = 0.67 [0.39, 0.86] (unknown 0) | 5/12 = 0.42 [0.19, 0.68] (unknown 0) |
| rusty-verify | 10/12 = 0.83 [0.55, 0.95] (unknown 0) | 8/12 = 0.67 [0.39, 0.86] (unknown 0) | 10/12 = 0.83 [0.55, 0.95] (unknown 0) | 9/12 = 0.75 [0.47, 0.91] (unknown 0) |

## Failure classes and budget

| Harness | operator_setup | coverage_limitation | harness | solver | Coverage limited | Budget exhausted | Calls | Input tokens | Output tokens |
|---|---|---|---|---|---|---|---|---|---|
| mini | 0 | 0 | 0 | 10 | 0 | 0 | 1931 | 59756889 | 605020 |
| mini-verify | 0 | 0 | 0 | 8 | 0 | 2 | 1805 | 49844837 | 599320 |
| rusty-baseline | 0 | 0 | 0 | 7 | 0 | 2 | 1454 | 55505836 | 1186537 |
| rusty-careful | 0 | 0 | 0 | 8 | 0 | 1 | 1395 | 41086989 | 1235819 |
| rusty-combined | 0 | 0 | 0 | 7 | 0 | 2 | 1240 | 43614090 | 1046286 |
| rusty-shell | 0 | 0 | 0 | 10 | 0 | 3 | 2198 | 60282827 | 2701104 |
| rusty-verify | 0 | 0 | 0 | 7 | 0 | 1 | 1159 | 40991079 | 960366 |

## Termination (every recorded attempt)

| Harness | n | handoff | budget exhausted (calls / input / output / wall / other) | harness stop | infrastructure | unknown |
|---|---|---|---|---|---|---|
| mini | 12 | 12 | 0 (0 / 0 / 0 / 0 / 0) | 0 | 0 | 0 |
| mini-verify | 12 | 10 | 2 (2 / 0 / 0 / 0 / 0) | 0 | 0 | 0 |
| rusty-baseline | 12 | 9 | 2 (0 / 2 / 0 / 0 / 0) | 1 | 0 | 0 |
| rusty-careful | 12 | 10 | 1 (0 / 1 / 0 / 0 / 0) | 1 | 0 | 0 |
| rusty-combined | 12 | 8 | 2 (0 / 2 / 0 / 0 / 0) | 2 | 0 | 0 |
| rusty-shell | 12 | 4 | 3 (1 / 1 / 1 / 0 / 0) | 5 | 0 | 0 |
| rusty-verify | 12 | 10 | 1 (0 / 1 / 0 / 0 / 0) | 1 | 0 | 0 |

## Attempts active at admitted call k (eligible attempts)

| Harness | n | median calls | max calls | cap | active at k |
|---|---|---|---|---|---|
| mini | 12 | 155.50 | 255 | 300 | 0:12 10:12 20:12 30:12 40:12 50:12 60:12 70:12 80:11 90:11 100:10 110:9 120:8 130:8 140:7 150:6 160:6 170:4 180:4 190:3 200:3 210:3 220:3 230:3 240:3 250:3 260:0 270:0 280:0 290:0 300:0 |
| mini-verify | 12 | 120.00 | 300 | 300 | 0:12 10:12 20:12 30:12 40:12 50:12 60:12 70:12 80:12 90:10 100:8 110:8 120:6 130:4 140:4 150:4 160:3 170:3 180:3 190:3 200:3 210:3 220:3 230:2 240:2 250:2 260:2 270:2 280:2 290:2 300:2 |
| rusty-baseline | 12 | 94.50 | 231 | 300 | 0:12 10:12 20:12 30:12 40:12 50:12 60:10 70:7 80:7 90:6 100:6 110:4 120:4 130:4 140:4 150:4 160:4 170:4 180:4 190:4 200:3 210:3 220:2 230:1 240:0 250:0 260:0 270:0 280:0 290:0 300:0 |
| rusty-careful | 12 | 103.00 | 212 | 300 | 0:12 10:12 20:12 30:12 40:12 50:12 60:12 70:11 80:11 90:8 100:7 110:5 120:3 130:3 140:3 150:3 160:3 170:2 180:2 190:1 200:1 210:1 220:0 230:0 240:0 250:0 260:0 270:0 280:0 290:0 300:0 |
| rusty-combined | 11 | 92 | 240 | 300 | 0:11 10:11 20:11 30:11 40:11 50:10 60:9 70:9 80:7 90:6 100:5 110:5 120:3 130:2 140:2 150:2 160:2 170:2 180:2 190:2 200:2 210:2 220:1 230:1 240:1 250:0 260:0 270:0 280:0 290:0 300:0 |
| rusty-shell | 12 | 192.00 | 300 | 300 | 0:12 10:12 20:12 30:12 40:12 50:12 60:11 70:11 80:11 90:10 100:9 110:9 120:8 130:8 140:7 150:7 160:7 170:6 180:6 190:6 200:6 210:6 220:5 230:4 240:4 250:3 260:3 270:3 280:2 290:2 300:1 |
| rusty-verify | 12 | 75.00 | 225 | 300 | 0:12 10:12 20:12 30:12 40:11 50:10 60:10 70:9 80:5 90:4 100:4 110:3 120:3 130:2 140:2 150:2 160:2 170:2 180:2 190:2 200:1 210:1 220:1 230:0 240:0 250:0 260:0 270:0 280:0 290:0 300:0 |

## Trajectory (eligible attempts with a trajectory summary; shares of tool calls)

| Harness | Summarised / missing | Calls | explore | edit | run | verify | other | Shell share | Out-of-interface calls (attempts ending on one; interface unknown) | Without edit | Repatches mean |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mini | 12 / 0 | 1898 | 0.20 | 0.13 | 0.65 | 0.02 | 0.00 | 1.00 | 0 (0; 0) | 0 | 9.42 |
| mini-verify | 12 / 0 | 1690 | 0.20 | 0.12 | 0.66 | 0.02 | 0.00 | 1.00 | 0 (0; 0) | 0 | 4.83 |
| rusty-baseline | 12 / 0 | 1449 | 0.24 | 0.15 | 0.33 | 0.04 | 0.24 | 0.49 | 2 (0; 0) | 3 | 5.08 |
| rusty-careful | 12 / 0 | 1157 | 0.32 | 0.06 | 0.38 | 0.01 | 0.22 | 0.40 | 6 (0; 0) | 3 | 2.42 |
| rusty-combined | 11 / 0 | 1071 | 0.22 | 0.09 | 0.32 | 0.04 | 0.33 | 0.38 | 8 (0; 0) | 3 | 5.00 |
| rusty-shell | 12 / 0 | 2192 | 0.12 | 0.20 | 0.67 | 0.01 | 0.00 | 1.00 | 6 (0; 0) | 1 | 24.00 |
| rusty-verify | 12 / 0 | 1158 | 0.27 | 0.08 | 0.44 | 0.04 | 0.17 | 0.51 | 3 (0; 0) | 3 | 3.58 |

## Compactions (what followed each boundary, first five calls)

| Harness | Attempts with one | Compactions (by trigger) | Repeated calls after | First-step errors | Re-reads after | Unknown |
|---|---|---|---|---|---|---|
| mini | 0 | 0 (none) | 0 | 0 | 0 | 0 |
| mini-verify | 0 | 0 (none) | 0 | 0 | 0 | 0 |
| rusty-baseline | 0 | 0 (none) | 0 | 0 | 0 | 0 |
| rusty-careful | 0 | 0 (none) | 0 | 0 | 0 | 0 |
| rusty-combined | 0 | 0 (none) | 0 | 0 | 0 | 0 |
| rusty-shell | 0 | 0 (none) | 0 | 0 | 0 | 0 |
| rusty-verify | 0 | 0 (none) | 0 | 0 | 0 | 0 |

## Cost estimate (charged tokens at the manifest's pricing)

| Harness | USD | Input $/M | Output $/M | Attempts priced | Source |
|---|---|---|---|---|---|
| mini | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| mini-verify | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-baseline | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-careful | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-combined | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-shell | n/a | n/a | n/a | 0 | no pricing block in the manifest |
| rusty-verify | n/a | n/a | n/a | 0 | no pricing block in the manifest |
