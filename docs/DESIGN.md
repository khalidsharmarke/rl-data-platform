# RL Data Platform: Design

| | |
|---|---|
| Status | Requirements and high-level design; first slice released as 0.1.0, extended in 0.2.0 (integrity), 0.3.0 (environments) and 0.4.0 (training observability) (see [`BUILD.md`](BUILD.md) and [`LIMITATIONS.md`](LIMITATIONS.md)) |
| Date | 2026-10-13 |
| Source | [`POSTING.md`](POSTING.md), Anthropic's Full-Stack Software Engineer, Reinforcement Learning posting |
| Related | [`PRD.md`](PRD.md) (product requirements) · [`GLOSSARY.md`](GLOSSARY.md) (terms) · [`DECISIONS.md`](DECISIONS.md) (decision register, by reversibility) |

This is the engineering design: *how* the product described in [`PRD.md`](PRD.md) gets built. Requirements are derived from the posting and the PRD, and tied back to them by quotation or capability ID (`C#`). Anything the posting doesn't say outright is listed in the [Assumptions](#8-assumptions) register and cited by ID (`A#`).

## 1. Problem area

The posting summarizes the job in one sentence: build the platforms, tools and interfaces that power *"environment creation, data collection, and training observability"*. The reason it matters: *"The quality of Claude's next generation depends on the quality of the data we train it on"*.

The [PRD](PRD.md) sets out the product problem. Anthropic needs high-quality, high-volume training data and sources it through **vendors**, which differ in what they can supply and how well. Anthropic has to compare vendors on the same work, follow their performance over time, and catch abuse. Vendors have to manage their own people and catch abuse among their own workers.

For engineering, that splits into four areas and one cross-cutting layer:

| Area | Job |
|---|---|
| Environment creation | Get correct, well-calibrated training environments from vendors (and generators) into training |
| Data collection | Get high-quality human data from vendors' workers into training |
| Training observability | Show whether environments and runs behave as intended, catch reward hacks, and attribute training outcomes back to vendors |
| Vendor performance and integrity | Allocate work across vendors, measure every vendor the same way over time, and detect and act on abuse by workers and by vendors |
| Platform (cross-cutting) | Vendor workspaces, access, credentials, onboarding, audit, APIs |

**How the requirements were derived.** The *What You'll Do* (8 bullets) and *Representative Projects* (6 bullets) sections of the posting were grouped by the system each bullet describes and sorted under the areas above. The vendor performance and integrity requirements come from the PRD's capabilities (C1, C4, C5), grounded in the posting's *"vendor management workflows"* and *"quality assurance systems"*. Items from *Strong Candidates May Also Have* are nice-to-haves; where one became a requirement, it's marked (A18). One *What You'll Do* bullet (*"Partner closely with RL researchers…"*) describes how the role works, not something to build, so it produced no requirement.

## 2. Actors

Personas are described in the [PRD](PRD.md#3-who-cares-about-what). Each vendor has a capability setting: human work, environments, or both (A24).

| Actor | Side | Role on the platform | Source |
|---|---|---|---|
| Researcher | Anthropic | Defines projects (human data) and briefs (environments), reviews environments and data, watches training runs | *"researchers, external vendors, and thousands of data labelers use every day"* |
| Data ops | Anthropic | Sets up vendors, allocates work, compares performance, handles vendor abuse | *"data operations, and vendor management"* |
| Vendor admin | Vendor | Manages the vendor's users and quality; handles worker abuse | *"vendor management workflows"* |
| Vendor reviewer | Vendor | Checks workers' output before it goes to Anthropic (some vendors only) | PRD C2.2 (A29) |
| Worker | Vendor | A labeler or expert doing tasks | *"thousands of data labelers"*; *"non-technical labelers"* |
| Environment builder | Vendor | Creates and submits environments | *"let external partners create, submit, and iterate on training environments"* |
| Training infrastructure | System | Consumes approved environments and data; reports run results (A23) | *"RL training infrastructure"* |

## 3. Functional requirements

### Environment creation

| # | Requirement | Source | Assumes |
|---|---|---|---|
| EC0 | A researcher writes an environment brief, a project of the environment kind: what environments are wanted (skills, languages, constraints such as no public benchmark content), how many, the difficulty mix, the review rubric and a deadline. Data ops allocates it to vendors with the environments capability (VP1) | PRD C1.1; *"translate ambiguous requirements into well-scoped, well-designed products"* | |
| EC1 | An environment builder can define an environment against a brief allocated to their vendor: task prompt, container image, grader or reward function, and metadata (language, intended difficulty) | *"environment configuration"* | |
| EC2 | Every change creates an immutable version; history and diffs (file by file) are viewable | *"versioning"* | |
| EC3 | Submitting a version triggers automated checks: the reference solution passes, a no-op solution fails, repeated runs agree (ten, in the build), and difficulty is estimated | *"validate correctness and difficulty calibration before environments hit production training"* | A11, A12 |
| EC4 | An environment builder sees check results and reviewer feedback, then fixes and resubmits | *"iterate on training environments with minimal friction"* | |
| EC5 | A researcher browses environments (filter by brief, status, vendor, difficulty), grades them against the brief's rubric and leaves feedback | *"let researchers browse, grade, and provide feedback on training environments"* | A20 |
| EC6 | Approved versions are promoted and exposed to training infrastructure through an API | *"connect environment authoring tools, data collection systems, and RL training infrastructure"* | A23 |
| EC7 | Programming tasks are generated in bulk across languages and difficulty levels, each with tests as the reward signal, and pass through EC3 | *"scalable code data generation pipelines, producing diverse programming tasks with robust reward signals"* | A21 |
| EC8 | An environment version has three parts, stored separately: the task files the model works in, the grader that scores an attempt, and the reference solution that proves the task can be solved. The model never sees the grader or the reference solution; training receives the task files and the grader, never the reference solution | *"robust reward signals"*; *"catch reward hacks"* | A14 |

### Data collection

| # | Requirement | Source | Assumes |
|---|---|---|---|
| DC1 | A researcher creates a human-data project: task type, instructions, answer schema (rate, compare, write, trajectory review), quality bar, volume and deadline | *"labeling workflows"*; *"many task types"*; *"complex agentic tasks"* | |
| DC2 | Tasks are assigned to workers within the vendors allocated to the project (VP1) | *"human data collection at scale"* | |
| DC3 | Workers complete tasks in a UI built for non-technical users, and see feedback on their rejected work: reviewer rejections with the reviewer's reasons. Gold-task results are never shown per task (PRD PD2) | *"interfaces that are intuitive for both technical researchers and non-technical labelers"*; PRD C2.1 | |
| DC4 | QA: gold tasks with known answers (rotated regularly), overlapping assignments to measure agreement, reviewer spot checks, and a quality score per worker, computed the same way for every vendor (N16) | *"quality assurance systems"* | A13 |
| DC5 | Workers can flag broken tasks, and disagreement spikes raise alerts | *"feedback mechanisms that surface reward signal integrity issues early"* | A13 |
| DC6 | QA-passed data is exported for training | *"connect environment authoring tools, data collection systems, and RL training infrastructure"* | A23 |
| DC7 | Progress and throughput are tracked per project and per vendor | *"data collection progress"* | |
| DC8 | A vendor reviewer can check and correct workers' output before it's submitted to Anthropic. Vendor review adds to platform QA and doesn't replace it | PRD C2.2 | A29 |
| A30 | Per-task contracts pay for every submitted answer, not only accepted ones; hourly contracts pay for time on task | VP2, VP3, D11 | If vendors are paid only for accepted work, cost per accepted unit equals the contract price and stops reflecting quality; the comparison would then lean on acceptance rate instead |
| DC9 | The platform records time on task (assignment to submission) for every submission | PRD C3.5; PRD PD3 | |
| DC10 | Release stage: an accepted answer is released for export only when detection has processed it, it isn't held, the project's minimum gap has passed since it was submitted, its task has all its overlapping answers, and the worker has answered enough gold after it with accuracy at or above the quality bar. How much later gold is enough depends on the worker's integrity grade (DC11). An answer still waiting at the project's cap is released as provisional if the integrity conditions hold. Each exported row records its tier (verified or provisional) and the worker's grades at release | PRD C3.6; PRD PD7; *"surface reward signal integrity issues early"* | A34 |
| DC11 | Contributor grades: every worker has a quality grade (gold accuracy with a 95% interval, read as meets the bar, below it, or uncertain) and an integrity grade (suspended, held, watch, unproven or trusted), computed by the platform the same way for every vendor. Grades set how much evidence release needs and how closely the worker is measured (AB14); they never act on a person. Anthropic sees grades per worker; vendors see only aggregates; workers see neither their grades nor whether their answers have been released | PRD C3.7; PRD PD7; N16, N17 | A35 |

### Training observability

| # | Requirement | Source | Assumes |
|---|---|---|---|
| TO1 | Ingest per-episode results from runs: environment version, run, training step, reward, pass/fail, length, the files the attempt touched, transcript reference | *"training run health"* | A23, A38 |
| TO2 | Per-environment stats over time: solve rate, reward variance, difficulty | *"measure environment difficulty"*; *"environment statistics"* | |
| TO3 | Flag suspected reward hacks: a separate hack detector reads the episode stream and returns findings with a severity. A watch finding sends a sample of the environment's transcripts to researcher review; a case-severity finding opens an environment case, which quarantines the version (D35, D38) | *"catch reward hacks"* | A14 |
| TO4 | Live dashboard and alerts while a run is in progress | *"real-time feedback during production training"* | A9 |
| TO5 | Drill down from a run to an environment to a single episode transcript | *"browsing and analyzing training run results"* | |
| TO6 | From a flag, quarantine an environment and send it back to review (EC5) | Inferred: closes the loop from observability back to environment creation | A19 |
| TO7 | Training outcomes (reward hacks, solve rates) are attributed to the environment version and its vendor, and feed the vendor scorecard (VP3) | PRD C3.4; *"surface reward signal integrity issues early"* | |
| TO8 | Recall: when a version training has used is quarantined, the platform publishes a recall naming every run that used it, the first training step at which it appeared in each, and how many of its episodes were flagged. Training reads recalls, decides what to do with each run (roll back to a checkpoint, or continue) and reports it; the report is recorded on the case. The platform never touches model weights (D37) | *"real-time feedback during production training"*; *"catch reward hacks"* | A37 |

### Vendor performance

| # | Requirement | Source | Assumes |
|---|---|---|---|
| VP1 | Data ops allocates a project across vendors whose capabilities match, and changes the allocation while it runs | PRD C1.2; *"vendor management workflows"* | A24 |
| VP2 | Project view: vendors side by side on one project, comparing quality, volume, cost per accepted unit and turnaround. Hourly contracts are converted using time on task (DC9) | PRD C4.1; PRD PD3 | |
| VP3 | Vendor scorecard: one vendor's performance over time, across its human-data projects and environment briefs, with alerts on significant drops. Environment work is scored on validation pass rate, review grades and training outcomes (TO7) | PRD C4.2; PRD PD3 | |
| VP4 | Vendor's own view: vendor admins see their scorecard as Anthropic sees it, broken down by worker, with each worker's quality history. Reviewer feedback and agreement are live; gold-task accuracy appears only as a per-worker running score after ~20 gold results, refreshed daily | PRD C4.3, C4.4; PRD PD2 | |

### Abuse

| # | Requirement | Source | Assumes |
|---|---|---|---|
| AB1 | Detect worker abuse (answering faster than reading time, near-duplicate answers, AI-generated text where human judgment is required, colluding answer patterns, gold-task farming) and flag it to the vendor admin, using the shared platform rules. Worker detection runs in the detection service (AB13) | PRD C5.1; *"surface reward signal integrity issues early"* | A27 |
| AB2 | Detect vendor abuse (quality falling while volume rises, removed workers returning, environments with gameable graders or copied benchmark content, matching answers across vendors, and a vendor handling its own cases unusually: cases left open, most dismissed, suspended workers soon reinstated) and flag it to data ops, using the shared platform rules | PRD C5.2; PRD PD1 | A27 |
| AB3 | An abuse flag opens a case with its evidence; low-quality signals feed scorecards and feedback and don't open cases. The owner records a decision: the vendor admin for worker cases, data ops for vendor and environment cases | PRD C5.3 | |
| AB4 | Actions on workers: warn, retrain, suspend, remove | PRD C5.4 | |
| AB5 | Actions on vendors: lower allocation, pause, offboard, each after the vendor has had a chance to respond | PRD C5.5 | |
| AB6 | Clean-up: find everything a flagged worker, vendor or environment version produced (lineage), then discard, re-label or quarantine it | PRD C5.6; *"audit trails"* | |
| AB7 | Data ops can see vendors' worker-abuse cases and how they were resolved | PRD C5.7; PRD PD1 | |
| AB8 | While a case is open, the subject's data is protected automatically and reversibly: a worker's unexported submissions are held out of exports, an environment version is quarantined. Actions against people or vendors (AB4, AB5) always need a recorded human decision | PRD C5.8; PRD PD4 | |
| AB9 | Vendors can add private abuse rules that open cases only inside their own workspace and never feed scorecards (P2) | PRD C5.9; PRD PD5 | |
| AB10 | Cross-vendor worker match: vendors submit a keyed hash of a stable identifier, and matches across vendors become case evidence, never an automatic action (P2) | PRD C5.10; PRD PD1 | |
| AB11 | Client signals: the work UI records how each answer was produced (pasted text, time to first input, focus changes, whether the task content was opened) and sends them with the submission. They're marked untrusted: detector input and case evidence, never enough for a finding on their own | PRD C5.11; *"surface reward signal integrity issues early"* | A32 |
| AB12 | API signals: on every claim and submission the API records what the server observes (time on task, claim and submit rates, concurrent sessions per worker, workers per session or device) and enforces rate limits that apply the same way to everyone. It never rejects a valid submission because abuse is suspected | PRD C5.11 | |
| AB13 | Detection service: every submission produces an event on a queue that a separate detection service consumes. The service returns findings (subject, signal, severity, rule or model version, evidence a person can read) through the API, and reports how far through the event stream it has processed. It owns how signals are combined; the platform owns everything a finding leads to | PRD C5.11; *"surface reward signal integrity issues early"* | A31 |
| AB14 | Discreet escalation: before a person decides, the platform responds to findings only in ways the subject can't perceive: more gold and overlap on the worker's tasks, more of their answers sent to spot checks, and holds (AB8). Anything the subject can perceive needs a recorded human decision (AB4, AB5) | PRD C5.12; PRD PD6 | |
| AB15 | Audit sample: a random share of unflagged submissions goes to manual review, mixed in with other spot checks. Audit outcomes and case decisions go back to the detection service as labels, so its precision and its misses can both be measured | PRD C5.13 | A27 |
| AB16 | Shadow mode: a new rule or detector version first records findings without escalating or opening cases, until its precision on reviewed work meets a bar data ops sets | PRD C5.13; N19 | |
| AB17 | Exports wait for detection: a dataset includes only submissions the detection service has processed and that aren't held | PRD C5.14; DC6 | A33 |

### Workspaces and platform

| # | Requirement | Source | Assumes |
|---|---|---|---|
| VM1 | Data ops creates vendor workspaces; vendor admins invite users and assign roles (admin, reviewer, worker, environment builder); vendors see only their own data | *"multi-tenant platforms with role-based access"* (nice-to-have) | A18 |
| VM2 | Vendors manage scoped API tokens and container registry credentials: create, rotate, revoke | *"Docker registry access, API token management"* | |
| VM3 | Self-serve onboarding checks setup for the vendor's capabilities: for human work, workers are invited and a sample task is completed and accepted; for environments, the API token works, an image pushes and a sample environment passes validation | *"onboarding automation and documentation tooling so new vendors and internal users ramp up in hours, not weeks"* | A22 |
| VM4 | Every state change is recorded with who, what and when: configuration and decisions in the append-only audit log; work (assignments and submissions) in its own immutable records | *"audit trails"* (nice-to-have) | A18 |
| VM5 | Everything in the UI is also available through the API | *"backend services and APIs"* | |
| VM6 | Data ops sets each vendor's capabilities (human work, environments, or both, plus domains), which decide what it can be allocated | PRD C1.2 | A24 |
| VM7 | Data ops records each vendor's contracts: the kind of work (human work or environments), price, price unit (per hour, per task or per environment) and effective dates. A vendor can hold several, and allocations point to one | PRD PD3 | |

## 4. Core flows

Five flows carry most of the value. Two entities anchor them: the **vendor workspace** (who supplied the work) and the **environment version** or **task** (what was supplied). Lineage links every piece of training data to both.

1. **Brief → submit → validate → review → approve** (EC0–EC6)
2. **Allocate → collect → QA → release → export** (VP1, DC1–DC11)
3. **Run → monitor → flag → quarantine** (TO1–TO7), which loops back into flow 1
4. **Measure → compare → reallocate** (VP2–VP4, TO7): every vendor scored the same way, per project and over time
5. **Signal → detect → escalate → case → act → clean up** (AB1–AB17), at two levels: vendors over their workers, Anthropic over vendors

## 5. Scale estimate

| Load | Inputs | Math | Conclusion |
|---|---|---|---|
| Labeling | 5,000 active labelers (A1), 30 items/hour over 8-hour days (A2) | 5k × 30 × 8 ≈ 1.2M submissions/day ≈ 14/s average, ~50/s peak | Modest. One Postgres handles it |
| Validation | 50 vendors (A3), 5k version submissions/day (A4), 5 sandbox runs of ~5 min each (A5) | 25k runs × 5 min ≈ 125k container-minutes/day ≈ 90 containers running at once | Compute is the bottleneck, not the API |
| Environment storage | 5k versions/day (A4); versions share base images and differ in ~1 MB of files (A36) | ~5 GB/day of new content, ~2 TB a year; whole images per version (~500 MB) would be ~2.5 TB a day | Store content by hash, so a file or layer shared by many versions is stored once (D34) |
| Training results | 1k episodes/s peak across runs (A6); ~1 KB metadata plus 100 KB–1 MB transcript each (A7) | ~1 MB/s metadata; 100 GB+/day of transcripts | The heaviest write path. Transcripts go in object storage, metadata in an analytics store |

Every labeling submission publishes one small event for the detection service (AB13): about 14 events/s on average and 50/s at peak, a light load for any queue. How much work the service does per event is its own choice (D24). Vendor-level checks (AB2) and scorecards (VP3) are periodic rollups over data already collected.

The system is light on reads, heavy on writes for training results, and compute-heavy for validation. Those three loads need separate paths.

## 6. Non-functional requirements

| # | Quality | Requirement | Why | Assumes |
|---|---|---|---|---|
| N1 | Latency (workers) | UI actions < 200 ms p95; the next item loads in < 1 s | Labelers are paid by time, so friction is a direct cost | A10 |
| N2 | Latency (feedback) | Validation results within minutes; dashboard data 10–30 s behind training | *"real-time feedback during production training"*; *"a loop that closes in hours and days"* | A9 |
| N3 | Availability | Labeling 99.9%; vendor workspace 99.5%; dashboards may degrade | An outage idles thousands of paid people; dashboards are read-only, so brief staleness is acceptable | A8 |
| N4 | Decoupling | Training infrastructure is never blocked by this platform; results are buffered and processed asynchronously | Losing visibility is acceptable; stalling training isn't | A23 |
| N5 | Durability | No submitted label or episode result is lost; processing is idempotent (safe to retry) | *"The quality of Claude's next generation depends on the quality of the data we train it on"* | |
| N6 | Reproducibility | Environment versions are immutable; every episode and dataset references an exact version | An approved environment can't change under a running training job | |
| N7 | Consistency | Strong for state changes (approve, quarantine, assign); eventual for aggregates and dashboards | Double-assigned items or double approvals corrupt QA; a stats lag doesn't | |
| N8 | Isolation of vendor code | Sandboxed runs: no network, CPU/memory/time limits, killed on timeout | Vendors submit arbitrary containers | A15 |
| N9 | Tenant isolation | Vendors see only their own data; Anthropic sees workers only by pseudonymous ID; workers never see gold answers; vendors never see held-out evaluation data | *"multi-tenant platforms with role-based access"*; leaked gold answers let QA be gamed; PRD PD1 | A16, A18 |
| N10 | Fairness between tenants | Per-vendor quotas and fair queueing for validation compute | One vendor flooding submissions mustn't starve the rest | A17 |
| N11 | Credential security | Tokens are scoped, hashed at rest, rotatable and revocable | *"API token management"* | |
| N12 | Audit and lineage | Append-only change log; any episode traces back to its environment version, vendor and reviewer | *"audit trails"*; the trace is how a *"reward signal integrity"* issue is pinned to its source | A18 |
| N13 | Adaptability | New task types and environment schemas ship without a database migration (schema-driven forms and configs); deploys happen many times a day | *"priorities shift"*; *"many task types"*; *"react to what researchers need next"* | |
| N14 | Fast onboarding | A new vendor goes from invite to first accepted work (an accepted sample task or a passing environment) in < 1 day, without help | *"ramp up in hours, not weeks"* | A22 |
| N15 | Monitoring the platform itself | Metrics and alerts on queue backlog, sandbox failure rate, ingestion lag | A dashboard that silently goes stale is worse than none | |
| N16 | Consistent measurement | Quality, volume, cost and turnaround are computed by the platform from its own records, the same way for every vendor. Vendor-reported numbers are never an input | Vendors can only be compared fairly on an identical measure (PRD G2); PRD PD3 | |
| N17 | Detection secrecy | Which tasks are gold, and how abuse detectors work, is never visible to vendors or workers; they see results, not the method. Gold accuracy is shown only in aggregate (VP4) and gold tasks are rotated. Automatic responses to a finding can't be perceived by its subject (AB14) | Known gold tasks and known rules can be gamed, and a visible response reveals what was detected and when; PRD PD2, PD6 | |
| N18 | Due process | Every abuse action is tied to a case holding the evidence behind it, and can be reviewed and reversed | A false finding against a worker or vendor has real consequences (PRD PD4) | |
| N19 | Versioned definitions | Metric definitions, abuse rules and task and config schemas are versioned, and every score, flag, task and config records the version that produced it | A definition can then change without rewriting history: old and new versions can run side by side, and past numbers stay explainable ([decision register](DECISIONS.md)) | |
| N20 | Detection decoupling and lag | Collection never waits on the detection service: events queue durably, and if the service is slow or down, work continues and exports wait (AB17). Findings arrive within 5 minutes of submission at p95 | Labeling availability (N3) can't depend on a separate service; held-back exports are the safe failure | A33 |
| N21 | Answer-key secrecy | During training, the grader runs in a separate step after the model's attempt, on a copy of the task files; nothing the model does can read or change it. The reference solution is used only for validation | A grader the model can read or edit is a reward hack waiting to happen (A14); the environment-side equivalent of secret gold answers (N17) | A14 |
| N22 | Environment retention and access | Every version training has pulled is kept for good, including quarantined ones; only versions that never passed validation may be deleted, after 90 days. A version's content is readable by its vendor, Anthropic reviewers and training (which never gets the reference solution); other vendors can't read it | Lineage and reproducibility (N6, N12) need the exact content training used; vendors' environments are their work product (N9) | A36 |
| A37 | Training keeps checkpoints and can roll a run back to one, and will report what it did with a recall | TO8, D37 | If training can't roll back, a recall still stops further use and says which runs to distrust; the run's owner decides whether to discard it |
| A38 | Every episode carries its run and a training step that only increases within the run | TO1, D36 | Without steps, a recall can name runs but not where in them the bad environment started, so rollback means starting the run over |

## 7. Out of scope

- Actually training models. Training infrastructure is an external system (A23). Until a real integration exists, a script stands in for it, pulling approved environments and sending episode results.
- Grading with a model (LLM-as-judge).
- Paying vendors or workers. The platform produces the numbers billing would use.
- Recruiting workers and holding their real identities. Vendors own both (PRD PD1).

## 8. Assumptions

Each assumption names the requirements that depend on it and what changes if it's wrong.

### Numbers

| ID | Assumption | Used by | If wrong |
|---|---|---|---|
| A1 | ~5,000 labelers are active on a given day (posting says *"thousands"*) | Scale estimate, N1, N3 | 10× more still fits one Postgres for labeling writes (~500/s peak); revisit at 100× |
| A2 | A labeler completes ~30 items/hour over an 8-hour day | Scale estimate | Long agentic items could be 2–5/hour, which cuts write load and raises the cost of losing a submission (N5) |
| A3 | ~50 vendor orgs | Scale estimate, N10 | Fairness (N10) matters more with fewer, larger vendors |
| A4 | ~5,000 environment versions submitted per day | Scale estimate, D2 | Validation compute scales linearly with this. At ~1,000× the volume, the job queue should move out of Postgres (D2) |
| A5 | Each submission takes ~5 sandbox runs (reference, no-op, 3 repeats) of ~5 min each | Scale estimate, EC3 | Longer agentic environments (hours) would need far more compute and partial or sampled validation |
| A6 | ~1,000 episodes/s at peak across concurrent training runs | Scale estimate, N4 | 10× more would require a streaming log (e.g. Kafka) in front of ingestion instead of a simple queue |
| A7 | Episode metadata ~1 KB; transcripts 100 KB–1 MB | Scale estimate | Bigger transcripts change storage cost, not the design, as long as they stay in object storage |
| A8 | Availability targets: labeling 99.9%, vendor workspace 99.5% | N3 | Chosen to rank surfaces by cost of downtime, not taken from the posting |
| A9 | "Real-time" means 10–30 s behind training | N2, TO4 | Sub-second would require push or streaming all the way to the browser |
| A10 | Labeler latency targets of 200 ms p95 and < 1 s to next item | N1 | Industry-typical UI targets, not from the posting |

### Inferred checks and signals

| ID | Assumption | Used by | If wrong |
|---|---|---|---|
| A11 | An environment whose grader passes a no-op or empty solution has a broken reward signal and should fail validation | EC3 | Drop the check; reward hacks would then be caught only after training (TO3) |
| A12 | An environment must give the same result for the same solution across repeated runs | EC3 | Some environments are stochastic by design; would need a tolerance instead of exact agreement |
| A13 | Labeler quality is measured partly by overlapping assignments (several labelers on one item) and agreement between them | DC4, DC5 | Gold items and spot checks alone; costs less labeler time but catches less |
| A14 | Reward-hack signals include full reward on unusually short episodes, edits to test or grader files, and sudden jumps in solve rate | TO3 | The real signals are domain knowledge to learn from researchers; the detector should be pluggable |
| A15 | Vendor environments are arbitrary, untrusted containers (inferred from *"Docker registry access"*) | N8 | If vendors only submit configs for trusted runtimes, sandboxing gets much simpler |
| A16 | Held-out evaluation data exists and must not reach vendors | N9 | Remove the constraint |
| A17 | Vendors compete for shared validation compute | N10 | Per-vendor dedicated capacity would remove the need for fair queueing |

### Interpretation calls

| ID | Assumption | Used by | If wrong |
|---|---|---|---|
| A18 | Multi-tenancy and audit trails are required, though the posting lists them as nice-to-haves | VM1, VM4, N9, N12 | Both are cheap to design in and expensive to add later, so they stay unless scope forces a cut |
| A19 | A flag in observability should be able to quarantine an environment | TO6 | The posting stops at "catch"; without TO6 the pillars don't connect |
| A20 | Researchers reviewing environments and workers producing data are separate workflows with separate users | EC5, DC1–DC5 | They could share a generic task-and-review engine, which would cut build effort |
| A21 | Generated programming tasks go through the same validation as vendor environments | EC7 | A separate pipeline would duplicate EC3 |
| A22 | "Hours, not weeks" onboarding means under a day, end to end, without help | VM3, N14 | |
| A23 | Training infrastructure is a separate system: it pulls approved environments and data and pushes episode results | EC6, DC6, TO1, N4 | If it's tightly coupled, ingestion could read from its storage instead of receiving pushes |
| A24 | A vendor supplies human work, environments, or both, and data ops sets each vendor's capabilities rather than vendors declaring their own. The market has both kinds of vendor, and several large ones do both (see [PRD §2](PRD.md#2-problem)) | Actors, VM6, VP1 | If vendors self-declare, a capability needs checking (for example, a trial project) before work is allocated against it |
| A27 | The abuse signals listed in AB1 and AB2 are starting points; real ones come from investigating cases | AB1, AB2 | As with A14, detectors must be pluggable so new signals can be added without a redeploy |
| A29 | Some vendors review their workers' output before submitting it; the platform supports this but doesn't require it | DC8 | If every vendor must review, DC8 becomes a mandatory step in the labeling workflow |
| A31 | Worker-abuse detection is built and tuned by specialists (trust and safety, or research), so the platform treats it as a separate service and defines only its contract | AB13, D23 | If the platform team owns detection too, the contract still holds; the split is then inside one team |
| A32 | Client signals can be forged by anyone answering through the API or with a modified client | AB11 | If work could only be done in a locked-down client, client signals could carry more weight |
| A33 | Findings within about 5 minutes of submission are early enough, because datasets are exported hourly or less often | N20, AB17, D25 | If training consumes data continuously, waiting for detection adds its lag to every row |
| A34 | Researchers accept a release gap of about an hour by default, longer for production runs | DC10, PD7 | A shorter default lets more data through on less evidence; the setting is per project, so the default is all that changes |
| A35 | The integrity tiers' evidence counts (20 gold answers to stop being unproven; 1, 3 or 5 later gold answers to release) are starting points | DC11, D30 | Too low and a turncoat's answers are released before their later gold shows the change; too high and honest work waits for the cap |
| A36 | Environment versions mostly share base images and differ from each other in about 1 MB of files | Scale estimate, N22, D34 | If each version is a whole new image, storage grows ~500×; layer-level deduplication in the registry becomes essential, not an optimization |

## 9. High-level design

![High-level architecture](diagrams/architecture.png)

Source: [`diagrams/architecture.excalidraw`](diagrams/architecture.excalidraw) (open at excalidraw.com, edit, then re-export the PNG).

The system splits into three planes, because the scale estimate shows three loads that behave differently: low-volume state changes that must be correct, compute-heavy validation, and a high-volume stream of training results.

### How it works, step by step

**The goal in one line:** Claude improves by practicing tasks and getting scored on them. Anthropic gets many of those tasks, and the human judgments used to score answers, from vendors. A broken task or a careless judgment teaches Claude the wrong lesson, and vendors vary in how often that happens. So the system is a **quality-control line** for training material, a **scoreboard** that measures every vendor the same way, and an **integrity system** that catches abuse and cleans up after it.

Each step lists the components involved, what each one is for, and what breaks without it.

**1. Vendors get a workspace, and only see what's theirs.** Data ops sets up a vendor's workspace, records what it supplies (human work, environments, or both) and enters its contracts: price, and whether it's paid by the hour, the task or the environment. The vendor admin then invites their own reviewers, workers and environment builders, and their scripts get API keys.
- *Web app:* one site with a surface for each role. Without it, vendors and workers, many of them non-technical, would need custom tooling.
- *API gateway:* checks who each request comes from and what they may see. Without it, every part of the system would repeat its own permission checks, and one missed check would expose one vendor's work to another. It can be middleware inside the API service; it's drawn separately to show the responsibility.
- *Workspaces module:* creates workspaces, records capabilities and contracts, invites people, issues and revokes keys. Without it, data ops would set up every vendor by hand, against the posting's *"ramp up in hours, not weeks"*.
- *Pseudonymous worker IDs:* Anthropic sees each worker as an ID, never a name. Vendors keep their workers' identities, so they can work with the platform without handing over personal data.

**2. A researcher writes a brief, and an environment builder makes practice tasks for it.** The brief says what's wanted ("500 bug-fix tasks in Rust, mixed difficulty, nothing copied from public benchmarks"), and data ops allocates it to vendors that build environments, just as human-data projects are allocated. The vendor's builder writes each task ("fix this bug"), package the code it runs in as a container, and include an answer key: a reference solution plus the grader that scores attempts.
- *Container registry and object storage:* hold each version's content, copied into storage the platform owns when the version is submitted, so a vendor can't change or delete what training used. Content is stored by its hash: the task files the model works in, the grader and the reference solution as three separate parts (D34).
- *Environments module and Postgres:* record the task. Every edit creates a new version, and old versions never change. Without that rule, training would say "I used task #42" and nobody could tell which #42.

**3. Machines check the task before any person spends time on it.** Like checking an exam before giving it: the answer key should score full marks, a blank answer should score zero, and grading the same answer twice should give the same score.
- *Job queue (a Postgres table):* checks take minutes each and thousands arrive a day, so they wait in line. Submitting a task and queueing its checks happen in one database transaction, so a task can never be "submitted" with no check started. Workers take the next job from whichever vendor has the fewest running, so one vendor's 500 submissions can't push everyone else to the back.
- *Sandbox workers:* run the vendor's code in a sealed box with no internet access and strict time and memory limits. Without them, we'd be running strangers' code on our own machines.

**4. A researcher looks at it.** Machines catch tasks that are broken. People catch tasks that work but aren't worth training on: too easy, off-topic, or teaching the wrong skill. The brief's rubric says what to check, so two vendors' environments for the same brief are graded the same way.
- *Review module:* a queue of tasks for researchers to browse, grade and comment on. Feedback goes back to the environment builder, who fixes the task and resubmits, which sends it back to step 3.

**5. Approved tasks become available to training.**
- *Catalog API:* training asks for "all approved tasks" and gets exact versions: the task files and the grader, never the reference solution. The grader runs after the model's attempt, outside anything the model can touch (N21). Without it, training would read our database directly, and the two systems couldn't change independently.
- *Code task generator:* creates coding tasks in bulk and submits them through the same front door as a vendor, so they get the same checks. Without that reuse, we'd build and maintain a second quality pipeline.

**6. Data ops splits human-data work across vendors.** A researcher defines a project (such as "compare these pairs of answers, 50,000 of them, by Friday"), and data ops allocates it across vendors that can do that kind of work, each under one of its contracts.
- *Allocation module:* records which vendors get how much of each project, and lets data ops shift work while the project runs. Without it, comparing vendors on the same work would be impossible, because they'd never do the same work.

**7. Workers produce judgments.** Workers at each allocated vendor answer tasks. Some vendors have their own reviewers check the answers before they go to Anthropic.
- *Collection and QA module:* hands out tasks, collects answers, and measures each worker's quality three ways: tasks with known answers (gold), tasks sent to several workers for comparison, and reviewer spot checks. The measurement is identical for every vendor. Workers see why a reviewer rejected their work, but never which of their answers were gold. Without QA, careless or rushed answers go straight into training.
- *Gold answers in a separate table:* workers never see them, vendors never learn which tasks were gold, and the gold set is rotated. Without that, the quality test could be gamed.
- *Lineage on every answer:* each accepted answer records the task, the worker's ID, the vendor and the review. Without it, removing a bad worker's answers later would be guesswork.
- *Object storage (exports):* finished, checked batches are saved as files for training to download.

**8. Training sends back reports.** Every attempt Claude makes on a task produces a report: which task, what score, how long it took, and the full transcript. That's about a thousand reports a second.
- *Results intake:* receives reports in batches and ignores duplicates, so a resent report isn't counted twice.
- *Durable log:* a shock absorber. It accepts reports immediately, even when the parts behind it are slow or down. Without it, a slow dashboard could slow down training, which this system must never do.
- *Separate path that skips the gateway:* this flood never touches the database behind the website, so the site stays fast.
- *Stream processor:* rolls raw reports up into per-task and per-run statistics, and credits each result to the vendor that built the task.

**9. Everyone sees the same scoreboard.** Data ops compares vendors on one project and follows each vendor over time, across both human-data projects and environment work. Each vendor admin sees their own scorecard, the same numbers Anthropic sees, broken down by worker. Reviewer feedback and agreement show up as they happen; gold-task accuracy shows up as a daily running score, so no single task can be identified as a test.
- *Scorecard jobs:* background jobs that roll human-data quality, volume, cost per accepted unit and turnaround up per vendor, per project and per worker. The platform computes every number from its own records; vendors' own reports are never an input. Without that, vendors couldn't be compared fairly.
- *Analytics store:* a database built for questions like "this vendor's acceptance rate by week". Without it, crunching millions of rows in Postgres would slow down everything else on it.
- *Dashboard API:* scorecards, comparisons, run dashboards and a live feed, so researchers can watch a training run as it happens.
- *Object storage (transcripts):* holds full transcripts, which are too big to keep anywhere else.

**10. Catch abuse at both levels, then clean up.** Abuse means making data look better, larger or more human than it is. Vendors police their workers; Anthropic polices vendors. The aim is to surface problems early without showing anyone how they were found, so every layer contributes signals, and automatic responses stay out of the subject's sight until a person decides ([Integrity layers](#integrity-layers)).
- *Signals at every layer:* the work UI records how an answer was produced, and the API records what the server sees: timing, request rates, sessions. Client signals can be forged, so they're hints, never proof on their own.
- *Detection service:* a separate service that reads an event for every submission and returns findings with evidence. The platform treats its logic as a black box and depends only on its contract: events in, findings out. Specialists can change how it works without touching the platform.
- *Vendor oversight jobs:* rules over vendors: quality falling as volume rises, removed workers coming back, matching answers across vendors, and how each vendor handles its own workers' cases.
- *Reward-hack detector:* a separate service that watches the training report stream for a model scoring full marks without solving the problem, such as perfect scores on very short attempts or edits to test files. Like the worker detection service, its rules are a black box behind a contract. A strong finding quarantines the environment at once; a weak one sends a sample of its transcripts to a researcher.
- *Recall:* when an environment training has already used is quarantined, the platform tells training which runs used it and from which step. Training decides whether to roll each run back to an earlier checkpoint and reports what it did. The platform rolls back what it owns, the catalog, and never touches the model.
- *Discreet escalation:* a finding first makes the platform measure the worker more closely: more gold tasks, more overlap, more spot checks, none of which the worker can tell apart from normal work. A strong enough finding also holds their data. The worker's experience doesn't change until a person decides.
- *Cases module:* every flag opens a case with its evidence. Worker cases go to the vendor admin; vendor and environment cases go to data ops, who can also see how vendors handled their own cases. While a case is open, the platform automatically holds the affected data back from training, which can be undone. Every action against a person or vendor, from a warning to offboarding, needs a recorded decision, is tied to its case, and can be reviewed and reversed. Low quality on its own doesn't open a case; it shows up on the scorecard instead.
- *Manual review:* people decide cases, and they also review a random sample of work nothing flagged. Without that sample, the platform could count false alarms but never what it missed. Both kinds of outcome go back to the detection service as labels.
- *Release stage:* an answer reaches a dataset only after evidence about it has arrived: detection has checked it, nothing holds it, and the worker's later test answers show they were still working well. Trusted workers need less of that evidence than new or suspect ones, but everyone waits at least the project's minimum gap. Data that waits too long is released as provisional rather than lost.
- *Contributor grades:* each worker is graded on quality and, separately, on integrity, with how sure the platform is of each. Grades decide how much evidence release needs and how closely someone is measured. They never act on a person, and the person never sees them.
- *Clean-up through lineage:* once a case is decided, a lineage query finds everything the worker, vendor or environment produced, and it's discarded, re-labeled or quarantined. A quarantined task goes back to review (step 4).

**Throughout: the paper trail.**
- *Audit log:* an append-only record of every approval, allocation, case decision and permission change. When something looks wrong in training, it traces back to the exact task version or answer, the worker and vendor who produced it, and the reviewer who approved it.

### Integrity layers

Each layer sees something the others can't, and each is limited in what it may do on its own. The earlier a layer acts, the less it knows, so early layers only record and protect; people act on people.

| # | Layer | When it acts | What it sees | What it may do on its own | Requirements |
|---|---|---|---|---|---|
| 1 | Accounts and access | Onboarding and sign-in | Invites, tokens, sessions, devices | Scope and revoke tokens; later, match workers across vendors as evidence (AB10) | VM1 VM2 N11 AB10 |
| 2 | Work UI (client) | While the worker answers | How the answer was produced: pasting, typing, focus, whether content was opened | Record signals. They can be forged (A32) | AB11 |
| 3 | API | On every claim and submission | Server time on task, request rates, sessions per worker, assignment ownership | Record signals; apply rate limits that are the same for everyone. Never reject work because abuse is suspected | AB12 DC9 |
| 4 | Detection service | Within minutes, from the event queue | Each submission with the worker's history, signals from layers 2 and 3, and gold results | Raise findings with evidence. It can't act on anything itself | AB1 AB13 N20 |
| 5 | Platform response | When a finding arrives | The finding and its severity | Escalate measurement and hold data, invisibly to the subject (AB14). Open a case | AB3 AB8 AB14 |
| 6 | Manual review | Hours | Cases with evidence; a random sample of unflagged work | Decide cases and act on people. Label outcomes for layer 4 | AB3–AB5 AB15 |
| 7 | Vendor oversight | Daily roll-ups | Every vendor's scorecards, cases and case decisions | Raise vendor findings to data ops | AB2 AB7 VP3 |
| 8 | Release stage | When a dataset is built | How far detection has processed, holds, removals, overlap, the worker's later gold and grades | Keep unchecked and held data out of training; tag the rest verified or provisional | AB17 DC6 DC10 DC11 |
| 9 | Training feedback | After training uses the data | Reward-hack flags; outcomes traced back by lineage | Raise findings against environments, vendors and the datasets involved | TO3 TO7 D14 |

**Escalation.** Responses grow with the evidence. The platform acts early on data and late on people (PD6):

| Step | Response | Triggered by | Who can perceive it |
|---|---|---|---|
| 1. Observe | Record the finding. New rules stay at this step in shadow mode (AB16) | Any finding | Anthropic only |
| 2. Measure more | More gold (from gold the worker hasn't seen), more overlap and more spot checks on the worker's next tasks | A finding below case severity (D32), or a falling grade | Nobody: the extra tasks look like any other (D8) |
| 3. Protect data | Open a case and hold the worker's unexported answers | A finding at case severity, or step 2 confirming it | The vendor admin, through the case. Not the worker |
| 4. Act on the worker | Warn, retrain, suspend, remove | The vendor admin's decision | The worker |
| 5. Act on the vendor | Lower allocation, pause, offboard, after the vendor responds | Data ops' decision | The vendor |

Step 2 also turns a suspicion into evidence: a few dozen extra gold answers confirm or clear a worker faster than waiting for normal gold to accumulate.

### Components

| Component | Plane | Job | Requirements |
|---|---|---|---|
| Web app | Edge | One React + TS app with a surface per role: vendor workspace (admin, reviewer, worker, environment builder), researcher review, data ops console, dashboards and scorecards. The work UI records client signals | EC4 EC5 DC3 DC8 TO4 TO5 VP2–VP4 AB3 AB11 |
| API gateway | Edge | Authenticates (SSO for people, API tokens for vendors and services), applies roles and workspace scoping, rate-limits | VM1 VM2 N9 N11 AB12 |
| API service | Control | One service split into modules: workspaces, capabilities and contracts, environments and versions, review and quarantine, projects and allocation, collection and QA (with API signals), escalation, cases and actions, lineage and clean-up, grades and release, audit | EC0–EC6 DC1–DC11 VP1 AB3–AB8 AB12 AB14–AB17 VM1–VM7 TO6 |
| Postgres | Control | Source of truth: immutable versions, the version state machine, contracts, allocations, assignments and submissions with lineage, cases and holds, the audit log, and the job queue (a table) | N6 N7 N12 N18 N19 |
| Job queue | Control (a Postgres table) | Holds validation, abuse-detection and scorecard jobs. Workers claim the next one with `SELECT … FOR UPDATE SKIP LOCKED`; validation jobs are taken from the vendor with the fewest running first | N10 |
| Sandbox workers | Execution | Run each submitted version: reference solution must pass, no-op must fail, repeat runs must agree, difficulty estimated. No network; CPU, memory and time limits | EC3 N8 |
| Background jobs | Execution | Trusted platform code: vendor oversight rules, and scorecard roll-ups written to the analytics store | AB2 VP2–VP4 N16 N17 |
| Outbox and relay | Control | Submission events written in the same transaction as the submission, then relayed to the detection queue | AB13 N5 D24 |
| Detection service | Execution (separate service) | A black box behind a contract: consumes submission events, returns findings and how far it has processed through the API, and receives labels from manual review | AB1 AB13 AB15 AB16 N17 N20 |
| Container registry | Execution | Base images, and vendor images copied in on submission and pinned by digest; per-vendor push and pull credentials | VM2 D34 |
| Code task generator | Execution | Generates programming tasks and submits them through the public API, like any vendor | EC7 A21 |
| Results intake | Ingestion | Accepts batches of episode results from training; deduplicates by episode id; acknowledges once written to the log | TO1 N5 |
| Durable log | Ingestion | Buffers bursts so training is never slowed by downstream processing | N4 |
| Stream processor | Ingestion | Aggregates per-environment and per-run stats, credits outcomes to the environment's vendor, writes transcripts to object storage | TO1 TO2 TO7 |
| Reward-hack detector | Ingestion (separate service) | A black box behind a contract: reads the episode stream, returns findings with a severity and how far it has processed, receives decided cases as labels | TO3 AB3 A14 D35 D38 |
| Analytics store | Ingestion | Stats tables built for time-range queries (e.g. ClickHouse): per environment, run, vendor, project and worker | TO2 TO7 VP2–VP4 |
| Dashboard API | Ingestion | Scorecards, vendor comparisons, run dashboards and drill-down, plus a live feed over server-sent events | TO2 TO4 TO5 VP2–VP4 |
| Object storage | Shared | Environment content by hash (task files, grader, reference solution), episode transcripts, exported datasets, validation logs | TO5 DC6 EC8 N22 |

### Key decisions

| # | Decision | Why | Trade-off |
|---|---|---|---|
| D1 | The control plane is one modular service, not microservices | Priorities shift fast (N13); one deploy and one database keep iteration quick | Modules must keep clean boundaries so the ingestion or execution side can split off later |
| D2 | The job queue is a Postgres table. Submitting a version updates its status and inserts its jobs in one transaction; workers claim jobs with `SKIP LOCKED`, then re-check capacity in a fresh statement, because the claiming query's snapshot predates the lock | ~25k validation runs a day is about 0.3 jobs/s (A4, A5), and batched abuse checks add little (§5), far below what Postgres handles. One transaction means a version can't be "submitted" with no job, or have a job after a rollback (N5, N7), with no message broker to run for the platform's own jobs | Workers poll the database, and a crashed worker's job is retried after a lease expires, so jobs must be idempotent. Move to a dedicated broker (with an outbox) only if volume grows ~1,000× |
| D3 | Environment versions are immutable, and their status changes only through a state machine (draft → submitted → validating → failed or in review → approved → quarantined; a quarantined version goes back to review, TO6) | Training always refers to an exact, unchanging version (N6); every transition is a single audited event (N12) | Fixing a typo means a new version |
| D4 | Validation runs in sandboxes, taking jobs from the queue with a fair share per vendor | Vendor code is untrusted (N8, A15); one vendor can't starve the rest (N10) | Sandboxing (e.g. gVisor or Firecracker) adds startup time to each run |
| D5 | Training results bypass the gateway and the control plane: intake → durable log → processors | Training is never blocked by the platform (N4); the heaviest write path stays off the control database | Two paths to secure and monitor; dashboards are eventually consistent (N7) |
| D6 | Intake deduplicates by episode id, and every processor is idempotent | Retries can't double-count results (N5) | Producers must assign stable episode ids |
| D7 | Detectors raise flags through the API, not by writing to the database directly | Quarantine and other actions go through the same state machine, cases and audit trail as a human decision (TO6, AB3, VM4) | One extra network hop on a path that isn't latency-sensitive |
| D8 | Gold answers live in their own table, which worker- and vendor-facing endpoints never read. Gold tasks are drawn from the same mix of questions as regular work, and nothing about a task's content, position or routing sets it apart | Workers and vendors can't identify gold tasks, so QA can't be gamed (N9, N17) | QA scoring needs a separate, privileged code path |
| D9 | Task types and environment configs are described by JSON schemas stored as data | New task types ship without a migration or a new UI form (N13) | Validation and rendering are generic, so very custom task UIs need extension points |
| D10 | Training pulls approved environments from a catalog API and downloads datasets via short-lived signed URLs | Training stays decoupled (A23); large files never pass through the API (DC6) | Signed URLs must expire and be scoped to one dataset |
| D11 | Every performance number is computed by the platform, by one code path for all vendors, and vendors read the same numbers Anthropic does | Fair comparison needs an identical measure (N16, VP2, VP3); vendors can only fix what they can see (VP4) | Scorecards lag by the roll-up interval, and gold-task accuracy reaches vendors only as a daily aggregate (PRD PD2) |
| D12 | Workers are pseudonymous outside their vendor: each has a platform ID scoped to the vendor, and identity fields live only in the vendor's workspace | Anthropic can trace data and spot patterns without holding personal data (N9, PRD PD1) | A worker removed by one vendor can't be recognized at another until the keyed-hash match (AB10) is added |
| D13 | One flag → case → action model serves both levels. The case owner follows the subject: worker cases to the vendor admin, vendor and environment cases to data ops | One audited path for every enforcement decision (AB3–AB5, N18, VM4); data ops can review vendors' cases (AB7). Low-quality signals never open cases (PRD PD4) | The case model is generic, so level-specific workflows need care |
| D14 | Lineage is recorded when data is created: every accepted submission stores task, worker ID, vendor, project and review; every dataset row and episode references its source | Clean-up after abuse is a query, not an investigation (AB6, N12) | Wider rows; datasets must be re-exportable without removed items |
| D15 | Abuse detectors are pluggable rules: worker rules in the detection service (AB1, D23), vendor rules as background jobs (AB2) and reward-hack rules over the episode stream (TO3). Shared rules are the only ones that feed scorecards; vendor-private rules (AB9) run inside one workspace. Rule definitions are never exposed through vendor-facing APIs | New signals ship without a redeploy (A27); every vendor is measured by the same rules (N16, PRD PD5); published rules could be gamed (N17) | Vendors see the outcome of a rule, not its logic, which can feel opaque; case evidence (N18) has to make up for it |
| D16 | Data is protected automatically; people are acted on by people. An open case triggers reversible holds (AB8); suspensions, removals and vendor actions need a recorded decision | Damage from abuse is contained within minutes without punishing anyone on a false positive (N18, PRD PD4) | Held data delays exports; a burst of false flags can stall a project until cases are reviewed |
| D17 | Regular tasks are routed to one vendor in proportion to its allocation share, interleaved through the claim order (smooth weighted round-robin); gold tasks are routed to every allocated vendor. Changing an allocation re-routes only tasks nobody has started | Every vendor is measured on the same gold tasks, so gold accuracy is comparable (N16, VP2); interleaving keeps gold evenly mixed into each vendor's stream, where contiguous blocks would end a vendor's stream in a run of gold (D8); work already started stays with the vendor that started it | Gold tasks are answered once per vendor, which costs more than answering them once; agreement on regular tasks is measured within a vendor |
| D18 | Acceptance (metric quality-v1): an answer on a regular task is accepted unless a reviewer rejected it or its worker's gold accuracy is below the project's quality bar, once the worker has enough gold results to judge | Ties accepted volume, and so cost per accepted unit (PRD PD3), to measured quality rather than to volume alone | A worker's later gold results can change whether their earlier answers count; the metric version (N19) makes that explainable |
| D19 | A vendor scorecard raises an alert when gold accuracy over the last hour falls at least 8 points below the vendor's earlier accuracy, the drop is significant by a two-proportion z-test (z ≥ 2), and each side has at least 60 gold answers | Data ops needs early warning of real changes (VP3) without chasing noise. Testing against the recent window's uncertainty alone treated a lucky early baseline as exact and raised false alarms; the two-proportion test counts both sides | A small vendor's real drop takes longer to reach significance; worker-level rules (AB1) catch individual abuse sooner |
| D20 | After a case is decided, the abuse rules judge only the worker's answers submitted after the decision | A cleared or reinstated worker isn't re-flagged on evidence a person has already weighed (N18) | Abuse that continues right after a decision needs a fresh window of answers (10) before it's flagged again |
| D21 | Clean-up removes every answer the worker gave, not only the flagged ones; re-label also reopens those tasks for other workers at the same vendor | Nobody can tell exactly when abuse began, and answers given before detection may already be in exported datasets; lineage says which (AB6) | Honest early work by the same worker is lost, and re-labeling costs another answer per task |
| D22 | Removed answers still count as delivered in the vendor's scorecard history, but are never accepted or exported | The vendor's record stays truthful: the dip happened. Only the data used for training changes | Delivered volume includes answers that were later thrown away; acceptance shows the difference |
| D23 | Worker-abuse detection is a separate service behind a fixed contract: submission events in, findings out through the API (D7). The platform owns everything a finding leads to: escalation, cases, holds, decisions and clean-up | Detection is specialist work that changes often and may belong to another team (A31). The contract lets it change without touching the platform, and enforcement keeps one audited path (D13) | The event and finding schemas become a contract with another team. A signal the event doesn't carry can't be used until the contract changes, so events carry raw signals rather than derived ones |
| D24 | Every submission publishes an event, written to an outbox table in the submission's transaction and relayed to the detection queue. The service runs cheap checks on every event and costly ones (text similarity, model-based checks) on a sample weighted toward risk: new workers, workers with findings, vendors whose scorecards are falling | No submission skips detection because of a crash between commit and publish (N5). About 50 events/s at peak is cheap to send in full, so sampling is the service's cost decision, not the platform's. A random, weighted sample can't be predicted and dodged, just as gold can't (D8) | An outbox and relay to run. Costly checks miss some abuse by low-risk workers; the audit sample (D28) measures how much |
| D25 | A dataset includes only submissions up to the point the detection service reports it has processed, and none that are held. This is part of the release stage (DC10) | Data reaches training only after it has been checked (AB17). The lag is minutes against exports that run hourly or less often (N20, A33) | When detection is down, exports stall. That's the safe failure, and platform monitoring alerts on it (N15) |
| D26 | Automatic responses are ones the subject can't perceive (PD6, AB14): extra gold drawn from gold the worker hasn't seen, extra overlap, spot-check routing, holds. The API never rejects a submission because abuse is suspected, and case evidence shown to vendors describes what was observed, never thresholds or which tasks were gold | A response the subject can see tells them what was detected and when, which teaches evasion (N17), and harms an honest worker on a false positive (N18). Measuring more turns a suspicion into evidence quickly | Extra gold and overlap cost money. Extra gold counts toward the worker's quality but not the vendor's comparison, which keeps vendors comparable (D17). Abuse continues, into held data, until a person decides |
| D27 | New rule and detector versions start in shadow mode: their findings are recorded but don't escalate or open cases, until their precision on reviewed work meets data ops' bar | A bad rule can't flood vendors with false cases or hold a project's data (the trade-off in D16). Old and new versions run side by side (N19) | A new signal takes days to go live |
| D28 | A random share of unflagged submissions joins the spot-check queue (DC4), where reviewers can't tell it from other spot checks. Audit outcomes and case decisions go back to the detection service as labels | Reviewing only flagged work measures false alarms but never misses. Labels are how detection improves (A27) | Review time spent on mostly honest work. The sample rate sets how quickly a miss rate can be estimated |
| D29 | Release is decided at export time from evidence, not stored as a state: detection progress, holds, removals, overlap, the minimum gap, and the worker's gold submitted after the answer. Accuracy on that later gold must be at or above the quality bar | Evidence from the answer's own period catches a contributor who changes behavior; their earlier answers stop being released as soon as later gold falls (PD7). Computing release from current evidence means a new hold or a worse grade takes effect at the next export, with no state to update | An answer near the end of a worker's run has little later gold and waits for the cap. Release needs the whole project's facts at each export, which grows with project size |
| D30 | Grades have two axes. Quality is gold accuracy with a 95% Wilson interval, read against the project's bar: meets it (lower bound at or above), below it (upper bound below) or uncertain. Integrity is a tier: suspended (a person decided to suspend or remove the worker), held (an open case), watch (a finding below case severity that later gold hasn't yet cleared), unproven (fewer than 20 gold answers), trusted. Release needs 1, 3 or 5 later gold answers for trusted, unproven and watch workers; suspended and held workers' answers aren't released | Low quality and abuse need different responses (PRD PD4), so one combined score would blur them. The interval keeps "90% on 20 answers" apart from "90% on 200" | The counts are starting points (A35). Trust is earned from gold only, so domains with little gold rely on the cap |
| D31 | An answer still unreleased at the project's cap is released as provisional if detection has processed it, nothing holds it and the minimum gap has passed. Every exported row records its tier and the worker's grades at release | Integrity stays a hard gate while quality confidence becomes data researchers can filter on, so slow-to-prove contributors aren't silently dropped (PD7) | Training has to choose what to do with provisional rows; a grade not recorded at release can't be recovered later |
| D32 | Detector findings carry a severity. A single signal is a watch finding and leads to escalation (AB14); two or more signals agreeing on the same answers lead to a case. A client signal on its own is never more than a watch finding (AB11). After at least 10 later gold answers, a watch finding is cleared when they meet the bar, becomes a case only when the 95% interval (D30) is wholly below the bar, and otherwise keeps being measured; at 30 answers an unsettled watch is cleared as inconclusive | Weak evidence earns closer measurement, not a case the vendor admin sees; a client signal can be forged (A32). Requiring the interval, not the raw score, below the bar keeps an honest worker's bad luck, or plain low quality (PRD PD4), from becoming an abuse case | A worker abusing in a way only one signal sees is measured for longer before anyone looks; a worker just under the bar is watched for up to 30 gold answers |
| D33 | One project model with two kinds: human-data projects and environment briefs. Both are created by a researcher, allocated by data ops under contracts, and scored on the vendor scorecard; every environment belongs to the brief it answers | Vendors fulfil Anthropic's request in both kinds of work. Tying environments to a brief gives review a standard (its rubric), lets vendors be compared on the same request, and lets progress and contracts be tracked against a target | Some fields apply to one kind only, so the project has kind-specific parts; a vendor's own idea for an environment needs a brief before it can be submitted |
| D34 | An environment version is a manifest in Postgres pointing at content stored by hash in platform-owned storage, in three parts: task files, grader and reference solution. Submitting copies the content in; nothing references a vendor's own registry. During validation the sandbox mounts the grader only for the grading step; in training the grader runs after the attempt on a copy of the task files | A hash names exactly one content, so immutability holds even outside Postgres (N6), and identical files across versions are stored once (A36). Keeping the parts apart is what stops the model reading or editing its own answer key (N21). Hashes also make diffs cheap (EC2) and let identical or copied content across vendors be spotted (AB2) | Builders must mark which files are which; an environment whose tests need to live inside the task files has to be restructured. Grading becomes a separate step with its own container start |
| D35 | Reward-hack detection is a black box behind the same contract as worker-abuse detection (D23): episodes in, findings with a severity out, decided cases back as labels | Hack signals are research knowledge that changes often (A14); the platform owns the response and the record, not the rules | Signals the episode record doesn't carry can't be used until the contract changes, so episodes carry raw facts (length, files touched, reward), not conclusions |
| D36 | Every episode records its run and training step, and lineage keeps them | A recall can say where in each run a bad version first appeared, which is what a rollback needs (TO8) | Producers must send steps; one more column on the heaviest write path |
| D37 | Rollback is a recall the platform publishes and training acts on. The platform rolls back what it owns (the catalog, through quarantine) and never touches models | Training owns weights and checkpoints (A23); the platform owns the evidence of which runs used what and when. Splitting it this way keeps the systems decoupled (N4) while still making rollback possible | Whether a run is rolled back is training's call; the platform can only record what training reports |
| D38 | Environment findings carry a severity. A watch finding queues a sample of the version's transcripts for researcher review; a case-severity finding opens an environment case and quarantines the version at once. People decide the outcome (retire or clear) | Quarantine is protective and reversible, like a hold (D16), so it can be automatic; retiring a vendor's work is a judgment | A false case-severity finding pulls a good environment until someone clears it |
| D39 | Observability doesn't depend on detection: the platform computes per-environment, per-run and per-vendor statistics from episodes itself, and a researcher can flag a version from any view | A detector only finds what its rules look for; a researcher watching solve rates jump can see a hack forming before any rule fires (A14) | Two paths to the same case: the detector's and a person's |
| D40 | Every spot check asks the reviewer two things: is the answer good enough to accept, and does it look like abuse. The audit sample (a random share of answers from workers with no finding) and the answers flagged by shadow rules join the same queue, so reviewers can't tell which items are audits. Abuse judgements, case decisions and escalation outcomes all go back to detection as labels. The platform measures each rule version's precision (the share of its reviewed flagged answers judged abusive) and detection's miss rate (the share of audited unflagged answers judged abusive) | D28 needs a judgement that's about abuse, not quality: a sloppy honest answer is rejected but isn't abuse (PRD PD4). A reviewer who knew an item was an audit would judge it differently | Every spot check takes a few seconds longer, and the judgement is itself a person's opinion. The miss rate needs many audits before it means much |
| D41 | Rule modes are the platform's record. Every rule or detector version the platform hasn't seen before is in shadow mode. The detector reads the modes through its contract and counts only live rules when deciding a finding's severity, and the platform ignores findings from versions that aren't live. Data ops promotes a version once at least 20 of its flagged answers have been reviewed and its precision meets the bar they set, and can demote one at any time; both are recorded decisions | D27 needs someone to own each rule's mode. Kept in the platform, a detector redeploy can't make a rule live by itself, and a shadow rule can't tip a live finding into a case by agreeing with it | The detector must poll the modes, so a promotion takes effect a few seconds late. A rule that rarely fires takes a long time to earn promotion |
| D42 | Vendor rules run as platform jobs over data the platform already rolls up (D15): quality falling while volume rises, environment versions quarantined, and a vendor handling its own cases unusually. A vendor finding opens a vendor case owned by data ops (D13), which stays inside Anthropic until data ops has read it and asks the vendor to respond; a rule's mistake never reaches a vendor without a person. The vendor admin then sees what was observed and responds on the case. Data ops can lower the vendor's share on a project, pause it on one project or all, or offboard it, only after the vendor has responded or the response window (three days from being asked) has passed; each action is a recorded decision that routing follows at once. A decided vendor case isn't reopened on the same rule for a week | AB5: a vendor is a business partner with a contract, so it hears the evidence and answers before anything happens to it | A vendor that's gaming the platform keeps its allocation until the window passes; holds (AB8) still protect the data in the meantime |
| D43 | A worker can flag a task as broken, with a reason. A task flagged by two different workers stops being routed and goes to researchers, who restore or retire it; gold tasks are flagged the same way, so flagging never reveals which tasks are gold. A flag never changes the flagger's measures. A disagreement alert fires when the tasks from one upload batch have agreement well below what the same workers achieve on the project's other tasks (at least 8 points, and 3 standard deviations, since every batch is checked continuously), and lists that batch's tasks with the most disagreement. Each answer is measured against its own worker's agreement elsewhere, because a project's agreement also moves with which workers happen to be working, and a worker who disagrees everywhere is detection's matter, not the tasks' | A broken task punishes honest workers' accuracy and agreement and pollutes the data (DC5). Two flags rather than one stop a single worker from removing tasks they find hard | Flagging can be used to skip hard tasks; each worker's flag rate is visible to Anthropic, so a worker who flags far more than others stands out |
| D44 | Vendor review is set per allocation. When it's on, a worker's answer waits for one of the vendor's reviewers, who accepts it or corrects it before it reaches Anthropic. A correction is stored as a new version of the answer with the reviewer in its lineage; the original is never overwritten. Detection, gold accuracy and the worker's grade judge the worker's original answer; the vendor's scorecard, acceptance and datasets use the answer the vendor submitted | DC8: vendor review adds to platform QA and must not replace it. If corrections replaced originals, a vendor reviewer could hide a worker's abuse or low accuracy from Anthropic. The vendor is still accountable for what it submits | Answers wait for the vendor's reviewers, which slows release. Gold tasks are reviewed like any other task, so reviewers never learn which are gold, and their corrections on gold improve the vendor's accuracy, which is the point |
| D45 | API tokens belong to the vendor as an organization, for its own tooling (a builder's CI, a reporting script). The vendor admin creates each one with a name and scopes: environments (submit versions to the vendor's briefs) read (the vendor's own scorecards, cases and data) and, from M26, registry (push and pull the vendor's own images, D50). A token never acts for a person: workers and builders sign in themselves, and an action taken with a token is recorded as the named token's. Tokens are shown once, stored only as a hash, expire after 90 days, record when they were last used, and are rotated by creating a new one before revoking the old | VM2 and PRD C6.3: credentials for the vendor's systems, not its people's. A leaked token reaches only its vendor's data and its scopes, and is useless once revoked or expired; naming it keeps every action attributable without pretending a person took it | Vendors must rotate tokens every 90 days; a token can't do the work a person must do (answering tasks, deciding cases) |
| D46 | Onboarding is a checklist the platform computes from what actually happened, not what the vendor reports. Data ops creates the vendor and invites its admin with a single-use link that expires in 7 days. For human work: a worker is invited and answers a sample task set correctly. For environments: one of the vendor's API tokens has been used and a sample environment, submitted with it, passes the sandbox's checks. Samples run on the real path but in an onboarding project that never feeds scorecards, datasets or the catalog. Data ops can allocate real work only once the vendor's checklist is complete | VM3 and N14: a vendor that has proved its setup works can start the same day, without help; a broken setup is found on a sample, not on a real project | A vendor can't be rushed past a failed check; data ops can only wait or help |
| D47 | An environment version may carry probe solutions: known-wrong answers stored apart from the task files, like the reference solution. A fourth sandbox check runs each probe, and every one must fail the grader. Generated tasks always carry probes for the shortcuts their family invites | A grader that only checks some cases passes the existing checks (the demo's weak grader does) and is found only after a model games it in training (TO3). A probe that returns the input unchanged, or hard-codes the visible example, finds it before training | Probes only find the shortcuts someone thought of; each probe costs a sandbox run |
| D48 | Programming tasks are generated by a separate code task generator that is a client of the platform, like any vendor: an internal org with the environments capability, allocated to a brief, signing in with its own token, submitting versions through the public API (A21). It generates from task families, each a parameterized bug with its fix, tests and probes, across difficulty levels; a version whose content matches an earlier one is never resubmitted, and a family whose versions keep failing checks is stopped | Generated tasks get the same checks, review, catalog, quarantine and training attribution as a vendor's, with no second pipeline to keep in step | The generator is limited to what the public API offers; a family's quality depends on its template |
| D49 | Generated tasks are reviewed by sample: a researcher reviews a random sample of each submitted batch (the square root of its size, at least three); approving every sampled version approves the batch, and sending one back stops the batch and its family until the template is fixed. Once training has used them, each family shows its intended difficulty against the difficulty measured from solve rates | Researchers can't read hundreds of generated tasks, but one bad sample says the template is bad. Comparing intended and measured difficulty is how the families get calibrated (EC3) | An unsampled bad task can reach the catalog; quarantine and the hack detector remain the backstop |
| D50 | A vendor may bring its own runtime image: language, packages and tools, but no task content. The registry gives each vendor a namespace, and its credentials are the vendor's API tokens with a `registry` scope, exchanged at the platform's token endpoint for push and pull on that namespace only. A version names its image by digest; submitting copies the image into the platform's namespace, and the sandbox and training use only that copy. Task files, grader and reference solution are still uploaded and stored apart (D34) | A15 and VM2: environments need runtimes the base image doesn't have. Keeping content out of the image keeps the grader out of what the model runs in (N21); pinning by digest and copying in keeps what training used reproducible after the vendor changes or deletes its image (N6, N22); one credential per vendor tooling, revoked in one place, keeps registry access as auditable as the API's (D45) | An image is untrusted code with more room to hide in than a bundle, so sandboxing matters more (N8); images cost storage until shared layers are deduplicated (A36) |

### Core entities

| Entity | Key fields | Notes |
|---|---|---|
| Org | id, name, kind (vendor or internal), capabilities (human work, environments; domains) | A vendor workspace is a vendor org (VM1, VM6) |
| User | id, org, role (researcher, data ops, vendor admin, vendor reviewer, worker, environment builder), pseudonymous id | Identity fields visible only inside the user's own org (D12) |
| ApiToken | id, org, scopes, hash, expires_at, revoked_at | Stored hashed (N11) |
| Environment | id, org, project (the brief it answers), name | Groups versions |
| EnvironmentVersion | id, environment, number, manifest (base image digest, test command, and the content hash of each file, marked task, grader or reference), status | Immutable (D3); parts stored separately (D34) |
| ContentObject | hash, size, stored_at | One stored file or layer, shared by every version that contains it (D34) |
| CatalogPull | version, puller, at | Who received which version; kept with the version for lineage (N22) |
| ValidationRun | id, version, check (reference, no-op, repeat), result, logs_uri | |
| Review | id, subject (version or submission), reviewer, grade, rubric scores, feedback | Researcher or vendor reviewer (EC5, DC8) |
| Project | id, kind (human data or environment brief), deadline, volume; human data: task_type schema and schema version, instructions, quality bar, overlap, gold rate; brief: description, constraints, difficulty mix, review rubric | DC1 EC0 D33 |
| Contract | id, vendor, kind of work (human work or environments), price, price unit (hour, task or environment), effective from, effective to | Raw cost inputs (VM7, PRD PD3) |
| Allocation | id, project, vendor, contract, share, status | VP1 |
| TaskRoute | task, vendor | Which vendors may work on a task (D17) |
| Task / Assignment / Submission | task payload; assignment to a worker; the answer, with worker ID, vendor, review, acceptance, time on task, client and API signals, and hold status | One open assignment per worker and task (N7); lineage (D14); signals (AB11, AB12) |
| SubmissionEvent | id, submission, signals, created_at, relayed_at | Outbox for the detection service (D24) |
| DetectionProgress | detector version, processed up to (event id), reported_at | The release stage reads it (D25) |
| WorkerGrade | worker, project, gold n, gold correct, 95% interval, quality grade, integrity grade, computed_at | Computed by the platform (DC11, D30); a snapshot is stored on each exported row |
| Escalation | worker, project, extra gold rate, extra overlap, spot-check share, finding, expires_at | AB14, D26 |
| AuditSample | submission, review, outcome (abuse, low quality, fine) | AB15, D28 |
| GoldAnswer | task, answer | Separate table (D8) |
| GoldRetirement | task, retired by, reason, when | Rotation (PRD PD2): retired gold tasks are no longer handed out; past answers on them still count |
| Dataset / DatasetRow | dataset: project, uri, metric version, row count, the dataset it supersedes; row: dataset, submission, tier (verified or provisional), the worker's grades at release | An exported snapshot (DC6); each row points to its source submission (D14) |
| Cleanup / Removal | clean-up: case, worker, mode (discard or re-label), counts; removal: submission, clean-up, whether its task reopens | Submissions stay immutable; removal is recorded beside them (AB6, D21) |
| TrainingRun / Episode | run: label, started_at; episode: episode_id, run, step, version, vendor, reward, passed, length, files touched, transcript_uri | Episodes live in the analytics store, not Postgres (D5); run and step make rollback points findable (D36) |
| Recall / RecallReport | recall: case, version, issued_at, and per run the first step, episodes and flagged share; report: recall, run, action (rolled back or continued), checkpoint step, note, reported_at | TO8, D37 |
| Flag | id, subject (worker, vendor or version), source (detection service, vendor rule, reward-hack detector or person), rule or detector version, severity, mode (live or shadow), evidence | AB1 AB2 AB13 AB16 TO3 |
| Case | id, subject, owner, flags, holds, vendor response, status, decision, decided_by | D13, N18 |
| Action | id, case, type (warn, retrain, suspend, remove, lower allocation, pause, offboard, discard, re-label, quarantine) | AB4–AB6 |
| Scorecard row | vendor, project (human data or environment brief), worker or environment builder (optional), period, metric version, quality, volume, cost per accepted unit, turnaround | Analytics store (D11) |
| AuditEvent | id, actor, action, entity, before, after, at | Append-only (VM4) |

### API sketch

| Call | Who | Requirements |
|---|---|---|
| `POST /orgs`, `PUT /orgs/{id}/capabilities`, `POST /orgs/{id}/contracts` | Data ops | VM1 VM6 VM7 |
| `POST /orgs/{id}/invites`, `POST /orgs/{id}/tokens`, `DELETE /tokens/{id}`, `GET /onboarding/checks` | Vendor admin | VM1–VM3 |
| `GET /projects?kind=environment` (briefs allocated to the builder's vendor), `POST /environments`, `POST /environments/{id}/versions`, `POST /versions/{id}/submit`, `GET /versions/{id}` | Environment builder | EC0–EC4 |
| `GET /versions?status=in_review&project={brief}`, `POST /versions/{id}/reviews` | Researcher | EC5 |
| `POST /versions/{id}/approve`, `POST /versions/{id}/quarantine` | Researcher, detector | EC6 TO6 |
| `GET /catalog` | Training | EC6 |
| `POST /projects` (either kind), `POST /projects/{id}/tasks` | Researcher | DC1 EC0 |
| `POST /projects/{id}/allocations`, `PATCH /allocations/{id}` | Data ops | VP1 |
| `POST /assignments/next`, `POST /assignments/{id}/submit`, `POST /tasks/{id}/flag`, `GET /me/feedback` | Worker | DC2 DC3 DC5 |
| `POST /submissions/{id}/review` | Vendor reviewer | DC8 |
| `GET /projects/{id}/quality`, `POST /projects/{id}/exports`, `GET /projects/{id}/release` | Researcher | DC4 DC6 DC7 DC10 |
| `GET /projects/{id}/grades` | Data ops, researcher | DC11 |
| `GET /projects/{id}/vendors` | Data ops, researcher | VP2 |
| `GET /vendors/{id}/scorecard`, `GET /workers/{id}/quality` | Data ops; vendor admin (own vendor only) | VP3 VP4 |
| `GET /cases`, `POST /cases/{id}/responses`, `POST /cases/{id}/decision`, `POST /cases/{id}/actions` | Vendor admin, data ops | AB3–AB5 AB7 AB8 |
| `GET /lineage`, `POST /cleanups` | Data ops | AB6 |
| `POST /detection/findings` (batch, with how far it has processed), `GET /detection/labels` | Detection service | AB13 AB15 D25 |
| `PUT /detectors/{version}/mode` (shadow or live) | Data ops | AB16 |
| `POST /ingest/episodes` (batch), `GET /recalls`, `POST /recalls/{id}/reports` | Training | TO1 TO8 N5 |
| `GET /hack-detection/episodes?after=N`, `POST /hack-detection/findings`, `GET /hack-detection/labels` | Hack detector | TO3 D35 |
| `GET /runs/{id}/stats`, `GET /runs/{id}/live` (SSE), `GET /episodes/{id}` | Researcher | TO2 TO4 TO5 |

## 10. Next steps

1. **First slice: built.** Multi-vendor human data (core flows 2, 4 and 5), scoped in [`BUILD.md`](BUILD.md) and described in the [project README](../README.md).
2. **Integrity: built in 0.2.0.** A separate detection service, the release stage, contributor grades and discreet escalation (the [integrity layers](#integrity-layers)).
3. **The environment slice: built** in [`BUILD.md`](BUILD.md#environment-milestones) M13–M16: briefs, versions stored in three parts, sandboxed validation, review, the catalog and quarantine (flow 1 and the end of flow 3).
4. **Next:** training observability, planned in [`BUILD.md`](BUILD.md#training-observability-milestones) as M17–M20: episode intake, statistics and dashboards, a black-box reward-hack detector, and the recall (TO1–TO5, TO7, TO8). What's modeled rather than solved is listed in [`NOT-SOLVED.md`](NOT-SOLVED.md).
5. **Deeper diagrams if needed:** the environment-version state machine (D3), the case life cycle (D13), and sequence diagrams for the five core flows.
