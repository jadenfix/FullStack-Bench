# 8. Related work

Every citation below was checked against the published version named in the reference list
(2026-10-13); a clause the published version does not state was reworded to what it does. One
`[verify]` marker remains, on a page read through a secondary source. Nothing here claims a
work is wrong; each paragraph says what it measures and what this paper measures that it does
not.

**Cross-layer coding benchmarks.** FullStack-Agent and its FullStack-Bench (Lu et al., 2026)
evaluate frontend, backend and database functionality with execution-based test cases, not only
the rendered interface. Cross-layer functionality is not our differentiation; we evaluate a
change to a running system with ongoing users, durable history and independently specified
outcome checks. Our project's working name predates that publication and the paper carries a
distinct title.

**Operations benchmarks on live systems.** AIOpsLab (Chen et al., 2025a) deploys microservice
environments, injects faults, generates workloads and checks the state of the whole system
after mitigation; ITBench (Jha et al., 2025) evaluates site reliability, compliance and
security, and financial operations; SREGym (Clark et al., 2026) adds faults at different layers
and metastable and correlated failures; DevOps-Gym (Tang et al., 2026) chains build,
monitoring, issue resolution and test generation in end-to-end pipeline tasks; Cloud-OpsBench
(Wang et al., 2026) separates a correct root-cause answer from an evidence-backed diagnosis. We
adopt their separation of environment, agent interface and evaluator. What our episodes measure
that these do not is the history of a code-to-production change: what served and what
customers experienced between the initial state and the handoff, and whether the guarantees
survived the operator's follow-up, with harm attributed segment by segment against the task's
own fault process.

**Safety-enforced operational agents.** STRATUS (Chen et al., 2025b) studies specialized
agents with state-machine orchestration, rollback and transactional no-regression, under
writer exclusivity and faithful undo, over a specified sequence of externally visible states.
It is the direct conceptual comparator for the harm measurement, and the boundary is explicit:
we do not argue that rollback research is wrong; we ask what happened in between, on a
contract where a double charge refunded, a secret rotated and a checkout rolled back remain
prohibited history events. ST-WebAgentBench (Levy et al., 2026) distinguishes nominal
completion from completion under policy and reports all-pass@k over repeated runs; tau-bench
(Yao et al., 2024) reports pass^k reliability over repeated trials. Safe completion is not a
new metric family, and our contribution is the engineering-specific harm predicates, the
separation of eligibility from success, and the four views.

**Evolving requirements and maintainability.** FeatureBench (Zhou et al., 2026) evaluates
feature-level development that spans multiple commits, checking that existing features keep
working; SlopCodeBench (Orlanski et al., 2026) has agents extend their own earlier solutions
under evolving specifications and measures structural erosion and verbosity. We keep practices
and style secondary and never let them decide functional success; the behavioural
maintainability experiment the design describes (a later requirement given to a fresh solver)
is future work.

**Provenance and human resolvability.** SWE-Lancer (Miserendino et al., 2025), SWE-Bench Pro
(Deng et al., 2025) and SWE-Bench ProMax (Shi et al., 2026) establish task provenance and
meaningful scope with engineer-reviewed checks, and SWE-Bench Pro additionally verifies
resolvability. The SWE-bench Verified audit (OpenAI, 2026) `[verify: figures read from Epoch
AI's review of the page, not the page itself]` shows graders that reject correct solutions, and
PROBE (Li et al., 2026) shows graders that accept incorrect ones: incorrect variants that
survive the suite expose the gaps, and behaviour-preserving transformations keep the added
tests from over-constraining. Our qualification (section 4) requires a materially different
correct solution and targeted wrong solutions per invariant; the held-out mutants and reviewed
solver submissions it also requires are the open item named in section 7.

**Interfaces, baselines and cost.** SWE-agent (Yang et al., 2024) shows that the
agent-computer interface itself matters; Agentless (Xia et al., 2025) that a simple
localize-repair-validate pipeline without agentic tool use outperformed the open-source agents
of its time on SWE-bench Lite at lower cost; "Is Bash All You Need?" (Mak et al., 2026) that
shell-only interfaces can be strong on enterprise agent tasks; "AI Agents That Matter" (Kapoor
et al., 2025) that holdouts must match the claimed generality and that cost belongs beside
accuracy. The shell track, the transfer arm, matched budgets and the success-cost reporting in
section 6 follow from these.

**Component-level harness studies.** Fan et al. (2026) fix a harness's execution loop and vary
three components, planning, the action space (predefined tools against a bare shell) and
context management, across four models including Nemotron-3 120B, on SWE-bench Verified and
Terminal-Bench 2.1, with exact McNemar tests on task-paired outcomes and Benjamini-Hochberg
correction. They report that context management matters most when the window binds, that
rule-based elision staged before summarization is the cheapest effective policy while a
recoverable-recall tool is rarely used, that planning helps weak models finish and saves
strong models cost, and that a bare shell helps or hurts by the model's shell proficiency, with
a small gap for the 120B model. Their method is the closest to section 6: one loop, one
component varied per comparison, paired tests, and a trajectory analysis that ties each effect
to the failure mode it removes. What they measure is final-state success and cost on
single-task coding and terminal problems; what section 6 measures is SafeSuccess on a running
system with the same discipline, and the mechanisms varied are fixed verification and careful
execution rather than context or planning. Their bash-only result is why the shell track
carries no preregistered direction. Rusty's context handling as pinned (stale tool output
elided at 60% of the window, summarization at 80%, no recall tool; Appendix B records the
thresholds from the source at the pin) resembles their most cost-effective tier minus the
recall tool, which they found rarely used; the compaction modes added to Rusty after the pin
are not part of this cohort.

**Workload and observation.** "Open Versus Closed: A Cautionary Tale" (Schroeder et al., 2006)
is the reason each task declares whether its workload is open, closed, partly open or observer
only, and why the accounting identity from scheduled through completed operations is part of
the evidence. Elle (Kingsbury and Alvaro, 2021) is the precedent for finding durable-state
violations in client-observed histories with a concise explanation of each anomaly.

**Benchmark maintenance and statistics.** Terminal-Bench's release policy (Marten, 2026a;
2026b) distinguishes changes to agent-facing conditions, which force a rerun, from verifier
changes, which force a regrade, and from metadata changes, which allow reuse, and its releases
calibrate task resources and repair tasks; our rerun-versus-regrade rule follows it. The
benchmark itself is Merrill et al. (2026); its current release is 4.0, and the 2.1 release Fan
et al. used is an earlier one. "What Makes a Terminal-Bench Task Hard?" (Lue Chee Lip et al.,
2026) limits what an all-fail collection shows, which is why the mechanism-study set keeps
tasks with measurable successes. The Benchmark Lottery (Dehghani et al., 2021) is why the task
mixture is frozen by manifest and the selection funnel is published. The paired exact test
with Benjamini-Hochberg adjustment (Benjamini and Hochberg, 1995), which Fan et al. apply at a
false discovery rate of 0.05, is the multiplicity rule of section 6.6; the comparison families
it is applied within are this paper's.

## References

- Benjamini, Y. and Hochberg, Y. "Controlling the False Discovery Rate: A Practical and
  Powerful Approach to Multiple Testing." J. R. Statist. Soc. B 57(1):289–300, 1995.
- Chen, Y. et al. "AIOpsLab: A Holistic Framework to Evaluate AI Agents for Enabling Autonomous
  Clouds." MLSys 2025; arXiv:2501.06706. (2025a)
- Chen, Y. et al. "STRATUS: A Multi-agent System for Autonomous Reliability Engineering of
  Modern Clouds." NeurIPS 2025; arXiv:2506.02009v2. (2025b)
- Clark, J. et al. "SREGym: A Live Benchmark for AI SRE Agents with High-Fidelity Failure
  Scenarios." arXiv:2605.07161v3, 2026.
- Dehghani, M. et al. "The Benchmark Lottery." arXiv:2107.07002, 2021.
- Deng, X. et al. "SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering
  Tasks?" arXiv:2509.16941v2, 2025.
- Fan, R.-Z. et al. "An Empirical Study of Harness Design for Coding Agents."
  arXiv:2609.20804v1, 2026.
- Jha, S. et al. "ITBench: Evaluating AI Agents across Diverse Real-World IT Automation Tasks."
  ICML 2025 (PMLR 267); arXiv:2502.05352.
- Kapoor, S. et al. "AI Agents That Matter." TMLR 2025; arXiv:2407.01502.
- Kingsbury, K. and Alvaro, P. "Elle: Inferring Isolation Anomalies from Experimental
  Observations." PVLDB 14(3), 2021; arXiv:2003.10554.
- Levy, I. et al. "ST-WebAgentBench: A Benchmark for Evaluating Safety and Trustworthiness in
  Web Agents." ICLR 2026; arXiv:2410.06703v7.
- Li, Z. et al. "Probe to Generate: Program Variant-Guided Test Augmentation for
  Repository-Level Repair Benchmarks." ASE 2026; arXiv:2604.01518v2. (v1 of the same record
  presented the framework as STING; the reported figures are the same in both versions.)
- Lu, Z. et al. "FullStack-Agent: Enhancing Agentic Full-Stack Web Coding via
  Development-Oriented Testing and Repository Back-Translation." arXiv:2602.03798, 2026.
- Lue Chee Lip et al. "What Makes a Terminal-Bench Task Hard? Separating Genuine Hardness from
  Fake-Hardness on an Adjudicated Agentic Corpus." arXiv:2609.26826, 2026.
- Mak, A. et al. "Is Bash All You Need? An Empirical Study of Tool Interfaces for Enterprise
  Digital Worker Agents." arXiv:2609.11999, 2026.
- Marten, A. "Continuous Benchmarks." tbench.ai/news, 30 July 2026. (2026a)
- Marten, A. "Terminal-Bench 4.0." tbench.ai/news, 28 August 2026. (2026b)
- Merrill, M. et al. "Terminal-Bench: Benchmarking Agents on Hard, Realistic Tasks in Command
  Line Interfaces." arXiv:2601.11868, 2026.
- Miserendino, S. et al. "SWE-Lancer: Can Frontier LLMs Earn $1 Million from Real-World
  Freelance Software Engineering?" arXiv:2502.12115v4, 2025.
- OpenAI. "Why we no longer evaluate SWE-bench Verified." openai.com, 23 February 2026.
- Orlanski, G. et al. "SlopCodeBench: Benchmarking How Coding Agents Degrade Over Long-Horizon
  Iterative Tasks." arXiv:2603.24755v2, 2026.
- Schroeder, B., Wierman, A. and Harchol-Balter, M. "Open Versus Closed: A Cautionary Tale."
  NSDI 2006.
- Shi, Y. et al. "SWE-Bench ProMax: Benchmarking Agents on Large-Scale Multilingual Code
  Refactoring." COLM 2026; arXiv:2608.09802.
- Tang, Y. et al. "DevOps-Gym: Benchmarking AI Agents in Software DevOps Cycle." ICLR 2026;
  arXiv:2601.20882.
- Wang, H. et al. "Cloud-OpsBench: A Reproducible Benchmark for Agentic Root Cause Analysis in
  Cloud Systems." arXiv:2603.00468v2, 2026.
- Xia, C. S. et al. "Agentless: Demystifying LLM-based Software Engineering Agents." FSE 2025;
  arXiv:2407.01489.
- Yang, J. et al. "SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering."
  NeurIPS 2024; arXiv:2405.15793.
- Yao, S. et al. "tau-bench: A Benchmark for Tool-Agent-User Interaction in Real-World
  Domains." arXiv:2406.12045, 2024.
- Zhou, X. et al. "FeatureBench: Benchmarking Agentic Coding for Complex Feature Development."
  ICLR 2026; arXiv:2602.10975.
