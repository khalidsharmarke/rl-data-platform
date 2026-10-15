# Limitations

| | |
|---|---|
| Version | 0.2.0 (the 0.1.0 MVP plus integrity milestones M9–M12), plus environment milestone M13 (see [`BUILD.md`](BUILD.md#environment-milestones)) |
| Covers | Slice A from [`BUILD.md`](BUILD.md): multi-vendor human data, with allocation, gold QA, scorecards, worker-abuse cases and lineage clean-up; a separate detection service; the release stage; contributor grades; discreet escalation |
| Related | [`POSTING.md`](POSTING.md) · [`PRD.md`](PRD.md) · [`DESIGN.md`](DESIGN.md) · [`DECISIONS.md`](DECISIONS.md) |

What the build does not do, where it falls short, and what is still undecided. The design describes the full platform; this page describes the gap between the design and the build.

## 1. Coverage of the posting

The posting names three areas: *"environment creation, data collection, and training observability."* The build covers the core of data collection, plus the vendor management, workspaces and audit trail it needs. It covers 28 of the design's 55 functional requirements.

| Posting line | Coverage in the build |
|---|---|
| *"platforms for human data collection at scale, including labeling workflows, quality assurance systems, and feedback mechanisms that surface reward signal integrity issues early"* | Mostly. Labeling, gold QA, agreement, spot checks and worker feedback are built. DC5 isn't: workers can't flag broken tasks, and disagreement spikes raise no alert |
| *"a unified platform for human data collection that integrates labeling workflows, vendor management, and QA for complex agentic tasks"* | Except for agentic tasks. There is one task type: pick the more helpful of two answers, with a reason |
| *"evaluation dashboards and observability UIs that give researchers real-time insight into environment quality, training run health, and reward hacking"* | Partly. Vendor scorecards and drop alerts exist; nothing covers environments, training runs or reward hacking |
| *"backend services and APIs that connect environment authoring tools, data collection systems, and RL training infrastructure"* | Partly. Datasets are exported as files. Nothing comes back from training |
| *"web platforms for RL environment creation, management, and quality review"* and *"vendor-facing interfaces… to create, submit, and iterate on training environments"* | Started. Researchers write briefs, data ops allocates them, and builders upload versions that never change, stored by hash in three parts (EC0, EC1, M13). Sandboxed checks, review, the catalog and quarantine are M14–M16 |
| *"scalable code data generation pipelines"* | No (EC7) |
| *"onboarding automation… Docker registry access, API token management, and environment validation"* | No. Vendors and users are seeded (VM2, VM3) |

[`BUILD.md`](BUILD.md#not-in-the-build) lists every requirement left out and why.

## 2. Technical limitations

### Detection

| Limitation | Effect | What would address it |
|---|---|---|
| The paste signal is reported by the browser | A worker answering through the API, or with a modified client, can omit it. Pasting alone is only ever a watch finding (D32), so a forged signal can't open a case, but omitting it removes one of the signals that make a case | Give more weight to server signals (AB12) |
| The speed rule uses server-measured time on task | A worker who waits before submitting passes it | Combine speed with quality signals: gold accuracy over time and agreement with other workers |
| The duplicate rule compares reason text | Paraphrased reasons pass it, and an LLM paraphrases for free | Similarity on meaning rather than wording, and patterns across workers |
| One bad worker hardly moves a vendor's numbers | At 5% gold and a 25% share, one worker in ten pasting moved Brightline from about 80% to about 71% gold accuracy. The vendor drop alert (D19: two-proportion test, at least 60 gold per side, at least 8 points) fires on some runs of the demo and not on others. The worker rules catch the worker on every run | More gold, longer windows, or vendor-level rules built on behavior signals as well as gold (AB2). Each costs money or time |
| Gold stays secret only if nothing reveals it indirectly | Gold can be recognized from how it's spread across questions, where it sits in a vendor's stream, or counts in events vendors can read. D8 forbids all three and tests cover them, but each new feature that touches routing, counts or timing can open another way | Tests for every vendor-visible endpoint and event, reviewed whenever routing changes |
| Escalation only reorders gold | A watched worker meets their vendor's gold sooner, but gets no extra overlap or spot checks (AB14), and the gold they use up is gold their colleagues would otherwise have answered | Add gold for the watched worker instead of reordering it, kept out of the vendor's comparison (D26) |
| A cleared worker isn't watched again for the same signal | The detection service reports each rule once per worker until a person decides a case; a cleared escalation isn't a decision, so the same signal later in the project raises nothing new | Send escalation outcomes to the detection service as labels, like case decisions |
| No audit sample or shadow mode | The platform can count false alarms but not what its rules miss (AB15), and a new rule version goes live at once (AB16) | D27, D28 |
| Trust is earned from gold only | A worker on a project with little gold, or a rare expert with none, stays unproven and their answers wait for the cap, then go out as provisional (D30, A35) | Other evidence routes into the grade: reviewer spot checks, agreement among experts |
| Grades are per project | A worker trusted on one project starts unproven on the next | Carry evidence across projects, weighted by how similar the work is |
| Vendors get no view of grades | DC11 gives vendors an aggregate (for example, how much of their work is released); the build shows them nothing | An aggregate on the vendor's own scorecard, coarse enough not to single out a watched worker |
| Rule thresholds were set against simulated workers | The demo's results depend on the simulator's settings (clock speed, Rosa's accuracy, when she switches, how often workers take breaks). Real workers will behave differently | Recalibrate the thresholds on real cases. Rules are versioned (N19), so a change shows in every flag |

### Data and cost

| Limitation | Effect | What would address it |
|---|---|---|
| Clean-up stops at the dataset | Lineage names every shipped row from a removed worker. Nothing records which training runs used which datasets, so the platform can't say which models are affected | Record dataset use by training runs (TO1, D14) |
| Clean-up removes every answer the worker gave (D21) | Honest work from before the behavior started is removed too | Remove from a defensible start point, once there is a reliable way to find it |
| Time on task is measured on the server | It includes network and page time. Under the 180× demo clock this adds about 2–3 simulated seconds to every answer, which narrows the gap between pasting and typing. In real time it is small next to the time a task takes | Use start and end times reported by the client, checked against the server's |
| Hourly cost depends on time on task | Cost per accepted answer for hourly contracts (PD3) inherits the error above | Same as above |

### Scale

The design's scale estimate is about 1.2 million submissions a day ([`DESIGN.md`](DESIGN.md), scale estimate). The build has run at about 6,600 answers per project and has no load test.

| Limitation | Effect at the design's scale |
|---|---|
| Each scorecard roll-up recomputes the whole project | Work grows with project size on every run, not with new answers |
| Release is recomputed from the whole project's facts at every export (D29) | The same: an export's cost grows with the project, not with what's new since the last one |
| The detection service keeps every worker's recent answers in memory and rebuilds them on start by replaying the whole event stream | Memory and restart time grow with the stream. A real service would keep its own store and replay only from a checkpoint |
| Claiming a task checks several conditions in one query, under a row lock and a recount | Claims could become the bottleneck at peak (about 50 per second) |
| The job queue and summary tables live in Postgres (D2) | Fine at this load. The design moves them to a queue and an analytics store at much higher volume |

### Testing and operations

| Limitation | Effect |
|---|---|
| The frontend has no automated tests | Screens are checked by hand |
| `make demo` has no end-to-end test | A change can break the demo story while the backend tests still pass |
| No CI, cloud deployment or real sign-in | Runs locally with Docker Compose and seeded logins (see the stand-ins in [`BUILD.md`](BUILD.md#stand-ins)) |

## 3. Open questions

### Product and policy

| Question | Depends on it |
|---|---|
| Are vendors paid for every submitted answer, or only for accepted ones? The build assumes every submitted answer (A30) | Cost per accepted answer; whether cheap vendors look expensive |
| Should clean-up remove all of a worker's answers, or only those after the behavior started? (D21) | How much honest data is lost per case |
| A suspended worker can rejoin through another vendor, because workers are pseudonymous (PD1). When is a cross-vendor match on a keyed hash (C5.10) worth its privacy cost? | Whether suspensions hold across vendors |
| Who sets the gold rate and alert thresholds for a project, and how much gold is worth paying for? | How quickly a vendor-level drop can be detected |
| If data is released only after evidence arrives (PD7), are vendors paid at submission or at release? | Who carries the risk of the gap, and vendors' cash flow if it's long |
| How long should data be allowed to wait before it's released as provisional, and how should training treat provisional rows? | Whether slow-to-prove contributors (new workers, rare experts, domains with little gold) end up under-represented in training data |

### Assumptions to confirm with the team

| Assumption | Why it matters |
|---|---|
| Volumes: labelers, items per hour, vendors, environment submissions (A1–A7) | The scale limits above, and when D2's Postgres queue must move |
| Reward-hack signals (A14) and abuse signals (A27) | Both rule sets are guesses until checked against real cases |
| Vendor environments are untrusted containers (A15) | Most of the environment pipeline's cost is sandboxing |
| Some vendors review their workers' output before submitting (A29) | Whether the vendor reviewer step (DC8) is optional or required |

## 4. Next

In order, from [`BUILD.md`](BUILD.md#stretch-in-order) and the gaps above:

1. The environment slice, milestones M13–M16 in [`BUILD.md`](BUILD.md#environment-milestones): briefs, versions, sandboxed checks, review, a catalog and quarantine (EC0–EC6, TO6). It covers the most posting lines the build doesn't.
2. The audit sample and shadow mode (AB15, AB16), which measure what detection misses and keep a new rule from acting before it's proven. Escalation outcomes sent back to detection as labels.
3. Broken-task flags and disagreement alerts (DC5).
4. Vendor-level abuse rules and vendor actions (AB2, AB5).
5. The vendor reviewer step (DC8).
