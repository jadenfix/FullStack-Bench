# 8. Related work

Every citation below is `[verify]`: the description is what the design document records of the
work and must be checked against the published version before submission. Nothing here claims
a work is wrong; each paragraph says what it measures and what this paper measures that it
does not.

**Cross-layer coding benchmarks.** FullStack-Agent and its FullStack-Bench `[verify]` evaluate
frontend, backend and database functionality and check database interactions rather than a
convincing interface. Cross-layer functionality is not our differentiation; we evaluate a
change to a running system with ongoing users, durable history and independently specified
outcome checks. Our project's working name predates that publication and the paper carries a
distinct title.

**Operations benchmarks on live systems.** AIOpsLab `[verify]` deploys microservice
environments, injects faults, generates workloads and checks the broader system after
mitigation; ITBench `[verify]` evaluates SRE, compliance and financial operations; SREGym
`[verify]` adds layered, correlated and metastable faults; DevOps-Gym `[verify]` chains build,
monitoring and issue work; Cloud-OpsBench `[verify]` separates a correct root-cause answer from
an evidence-backed diagnosis. We adopt their separation of environment, agent interface and
evaluator. What our episodes measure that these do not is the history of a code-to-production
change: what served and what customers experienced between the initial state and the handoff,
and whether the guarantees survived the operator's follow-up, with harm attributed segment by
segment against the task's own fault process.

**Safety-enforced operational agents.** STRATUS `[verify]` studies specialized agents with
state-machine orchestration, rollback and transactional no-regression, under writer
exclusivity and faithful undo, over a specified sequence of externally visible states. It is
the direct conceptual comparator for the harm measurement, and the boundary is explicit: we do
not argue that rollback research is wrong; we ask what happened in between, on a contract
where a double charge refunded, a secret rotated and a checkout rolled back remain prohibited
history events. ST-WebAgentBench and tau-bench `[verify]` distinguish ordinary completion from
policy-compliant completion and report repeated-run reliability (pass^k); safe completion is not
a new metric family, and our contribution is the engineering-specific harm predicates, the
separation of eligibility from success, and the four views.

**Evolving requirements and maintainability.** FeatureBench and SlopCodeBench `[verify]`
separate initial implementation from extension under later requirements and measure
structural erosion. We keep practices and style secondary and never let them decide functional
success; the behavioural maintainability experiment the design describes (a later requirement
given to a fresh solver) is future work.

**Provenance and human resolvability.** SWE-Lancer, SWE-Bench Pro and SWE-Bench ProMax
`[verify]` establish task provenance, meaningful scope and human resolvability with
engineer-reviewed end-to-end checks; the SWE-bench Verified audit `[verify]` and PROBE
(formerly STING) `[verify]` show that graders can be both too permissive and too restrictive
and that surviving incorrect variants and behaviour-preserving transformations expose both.
Our qualification (section 4) requires a materially different correct solution and targeted
wrong solutions per invariant; the held-out mutants and reviewed solver submissions it also
requires are the open item named in section 7.

**Interfaces, baselines and cost.** SWE-agent `[verify]` shows that the agent-computer
interface itself matters; Agentless `[verify]` that an elaborate agent is not automatically a
stronger baseline; "Is Bash All You Need?" `[verify]` that shell-only interfaces can be strong;
"AI Agents That Matter" `[verify]` that holdouts must match the claimed generality and that
cost belongs beside accuracy. The shell track, the transfer arm, matched budgets and the
success-cost reporting in section 6 follow from these.

**Workload and observation.** "Open Versus Closed: A Cautionary Tale" `[verify]` is the reason
each task declares whether its workload is open, closed, partly open or observer only, and
why the accounting identity from scheduled through completed operations is part of the
evidence. Elle `[verify]` is the precedent for finding durable-state violations in
client-observed histories with explanatory counterexamples.

**Benchmark maintenance and statistics.** Terminal-Bench `[verify]` distinguishes changes to
agent-facing conditions from verifier changes and metadata changes and discusses resource
calibration and task repairs; our rerun-versus-regrade rule follows it. "What Makes a
Terminal-Bench Task Hard?" `[verify]` limits what an all-fail collection shows, which is why the
mechanism-study set keeps tasks with measurable successes. The Benchmark Lottery `[verify]`
is why the task mixture is frozen by manifest and the selection funnel is published.
