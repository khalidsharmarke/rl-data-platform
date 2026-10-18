# Problems we chose not to solve

| | |
|---|---|
| Covers | Every place the project deliberately doesn't solve a problem, from 0.1.0 through the planned vendor onboarding and code generation milestones |
| Related | [`LIMITATIONS.md`](LIMITATIONS.md) (where what's built falls short) · [`BUILD.md`](BUILD.md) (scope and stand-ins) · [`DESIGN.md`](DESIGN.md) · [`DECISIONS.md`](DECISIONS.md) |

This page lists choices, not shortfalls: problems the project decided not to solve, what it does instead, and why. Where the built part falls short of its own design, see [`LIMITATIONS.md`](LIMITATIONS.md).

Each entry is one of five kinds:

| Kind | Meaning |
|---|---|
| Out of scope | Not this platform's job. Another system or team owns it |
| Black box | A real part of the system whose inside belongs to specialists. The platform defines the contract around it and owns everything its output leads to |
| Modeled | Something outside the platform, imitated by a stand-in so the platform around it can be real |
| Simplified | Real behavior on simpler infrastructure than production would use |
| Deferred | The platform's job, left for a later slice |

## Out of scope

| Problem | What the project does instead | Why | Where |
|---|---|---|---|
| Training models | Training is a separate system. A stand-in pulls environments from the catalog and, from M17, sends back modeled episodes | It's another team's system, and the platform must never be able to slow it down | DESIGN §7, A23, N4 |
| Rolling back a model | The platform quarantines the version and publishes a recall naming the runs and steps; training decides and reports what it did | Training owns weights and checkpoints; the platform owns the record of what each run received | PD8, D37, TO8 |
| Grading with a model | Graders are tests the builder writes; people review | Every score stays explainable. Claude as a first-pass reviewer stays a nice-to-have | DESIGN §7, NH5 |
| Paying vendors and workers | The platform records what billing needs: contract price and unit, time on task, accepted volume | Payment belongs to finance's systems | DESIGN §7, PD3 |
| Recruiting workers and holding their identities | Workers are pseudonymous to Anthropic; vendors keep their identities | Workers' privacy, and vendors own their rosters | PD1, D12 |
| Telling AI-written text from human text | Detection looks at behavior around a submission: speed, pasting, the same reason everywhere | Text classifiers are unreliable and easy to evade | AB1, A27, [`BUILD.md`](BUILD.md#detecting-ai-pasted-answers-an-honest-note) |

## Black boxes

| Problem | What the project does instead | Why | Where |
|---|---|---|---|
| How worker abuse is detected | A separate detection service behind a contract (events in, findings out, decisions back as labels). Inside are three stand-in rules | Detection is specialist work that changes often; the platform owns what a finding leads to: escalation, cases, holds, clean-up | D23, A31, AB13 |
| How reward hacks are detected | The same kind of contract over the episode stream (episodes in, findings with a severity out, case decisions back as labels). Inside are three stand-in rules: full marks on unusually short attempts, attempts that touch grader files, and abrupt solve-rate jumps | Hack signals are research knowledge to learn from researchers; the platform owns the case, the quarantine and the recall | D35, A14, TO3 |

## Modeled

| Problem | What the project does instead | Why | Where |
|---|---|---|---|
| Real workers | A simulator drives the public API with worker profiles: careful, average, sloppy, AI-pasting, note-pasting | No real workforce is available; everything they touch is real | BUILD M4, M12 |
| Vendor reviewers | In the demo, Northwind's reviewer is simulated: she catches 70% of wrong picks and never makes a right one wrong | Like the workers, a script stands in for people; the waiting, the correction as a second version and what each measure uses are real | D44, M24 |
| Reviewers' judgement | In the demo, the researcher's spot checks are simulated: an answer is judged abusive when its reason is the generic pasted one, and rejected when it picks the worse answer | Judging abuse is reading, which a script can't do honestly; the queue, the judgements and what they measure are real | D40, M21 |
| The model being trained | A modeled policy in the training stand-in: honest attempts whose chance of passing rises with training, and, on an environment marked hackable, a shortcut it discovers partway through the run. Outcomes are drawn, not computed | Real RL is out of scope; the platform reacts to episodes, not to how they were produced | B6 |
| Training infrastructure | A stand-in service that pulls approved versions from the catalog, runs modeled training runs, sends one batch of episodes per step, and acts on recalls by rolling back to a checkpoint and reporting it | Training is out of scope, but the catalog, intake and recall need a counterpart | A23, M16, M17 |
| Finding hacks nobody planted | The demo's hack is one we built into a fixture (`weak_grader`, "slugify edge cases"), and a sandbox test proves `return ""` really passes its grader in a container and fails a sound one | A real model finds holes nobody anticipated; a planted one shows the platform's response honestly | B6 |
| Estimating difficulty | A band from the solve rate training reports (too easy, easy, medium, hard, too hard); with the stand-in, that's the modeled policy's solve rate | Difficulty needs something attempting the task; with no model, the modeled policy is that something | EC3 |
| Time | A demo clock runs 180× real time in `make demo` | Five hours of work in a few minutes; every timestamp goes through it, so durations stay consistent | [`BUILD.md`](BUILD.md#stand-ins) |

## Simplified

| Problem | What the project does instead | Why | Where |
|---|---|---|---|
| Isolating untrusted code | Docker with strict limits (no network, read-only root, memory, CPU, process and time limits) instead of gVisor or Firecracker on dedicated hosts | Enough to show and test the boundary on a laptop | B5, D4 |
| Container registry | A registry with a namespace per vendor and token-based credentials, but no image scanning and no size quotas (planned, M26) | The sandbox's limits are the boundary that matters; scanning and quotas are standard registry features to turn on | D50, A15, VM2 |
| Registry transport | The registry and its token endpoint speak plain HTTP on localhost; Docker trusts localhost registries without TLS | A real deployment puts both behind TLS; nothing else changes | D50 |
| Registry activity in the audit log | Pushes and pulls are in the registry's own log, not the platform's audit log; the audit log records the token exchanges' effects that matter, a version naming an image | The registry can send notifications the platform could record; not needed to show the boundary | D45, D50 |
| Inviting a vendor | Data ops gets a single-use invite link to pass on, instead of the platform sending an email (planned, M27) | Email delivery is a standard part, not the hard problem | D46 |
| Generating programming tasks | Hand-written task families in Python: a parameterized bug, its fix, tests and probes, at several difficulties. No model writes tasks (planned, M28) | Templates make every generated task explainable and its probes known; a model writing tasks is a research project of its own | D48, EC7 |
| Sign-in and the API gateway | Seeded demo logins and a scoping module every route goes through | Standard parts, not the hard problem | [`BUILD.md`](BUILD.md#stand-ins) |
| Message broker and durable log | Postgres tables: the job queue, the outbox read through a cursor feed, and the episodes table that stands in for the durable log in front of the analytics store | Volumes are low enough; the contracts would stay the same on Kafka | D2, D24, B7 |
| Analytics store | Summary tables in Postgres | Same reason | [`BUILD.md`](BUILD.md#stand-ins), B7 |
| Object storage | A Docker volume | Same reason | [`BUILD.md`](BUILD.md#stand-ins) |
| Live dashboards | Polling every few seconds instead of server-sent events; the statistics processor runs on every 10-second tick of the job worker | Meets 10–30 s behind training either way | A9, B7 |
| Training's identity | One shared token, and run labels as free text | Enough to record pulls and recalls; per-run credentials come with a real integration | [`LIMITATIONS.md`](LIMITATIONS.md) |
| Extra gold for a watched worker | The worker meets their vendor's routed gold sooner; no gold is added | Keeps vendor comparisons unchanged without a second gold pool | D26, [`BUILD.md`](BUILD.md#stand-ins) |

## Deferred

These belong to the platform and come later. [`BUILD.md`](BUILD.md#not-in-the-build) and [`LIMITATIONS.md`](LIMITATIONS.md#4-next) give the order.

| Problem | Why it waits | Where |
|---|---|---|
| Scale: ~1.2 million submissions a day, ~1,000 episodes a second | Designed for, not load-tested; the build runs at demo volume | Scale estimate, D2, D5 |
| Task types beyond comparing two answers, including agentic tasks | One task type is enough to show the platform | DC1 |
| Vendor rules that need identities across vendors: removed workers returning, the same answers across vendors | They need a cross-vendor worker match, which is P2 | AB2, AB10 |
| Vendor onboarding, API tokens and registry access | Vendors were seeded (planned, M25–M27) | VM2, VM3, N14 |
| Generated programming tasks | Needed the environment pipeline first, which now exists (planned, M28–M29) | EC7 |
| Vendor-private rules, matching workers across vendors | P2 in the PRD | AB9, AB10 |
