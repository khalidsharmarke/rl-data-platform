# Build plan

| | |
|---|---|
| Status | 0.1.0 (MVP): M0–M8. 0.2.0: integrity milestones M9–M12. Environment milestones M13–M16 planned. Stretch goals not started. Known limitations in [`LIMITATIONS.md`](LIMITATIONS.md) |
| Timebox | About a week: ~33 hours across nine milestones |
| Stack | Python (FastAPI, async) · Postgres · React + TypeScript (Vite) · Docker Compose |
| Delivered as | This repo, runnable with `make demo`, plus a 100-second recorded walkthrough linked from the README |
| Related | [`PRD.md`](PRD.md) · [`DESIGN.md`](DESIGN.md) · [`DECISIONS.md`](DECISIONS.md) · [`LIMITATIONS.md`](LIMITATIONS.md) · [`GLOSSARY.md`](GLOSSARY.md) |

## 1. What the build shows

The build covers the PRD's core: **several vendors working on the same project, measured the same way, with abuse caught and cleaned up.** These are core flows 2, 4 and 5 in [`DESIGN.md`](DESIGN.md#4-core-flows). The environment pipeline (flows 1 and 3) isn't part of this build.

### The demo story

The seeded scenario runs on its own, so a reviewer can watch it from start to finish:

1. A researcher creates a project: *"Which of these two answers is more helpful?"*, 3,000 comparisons, 5% gold tasks drawn at random.
2. Data ops allocates it across three vendors with different contracts: Vendor A per task, Vendor B hourly, Vendor C per task at a lower price.
3. Simulated workers at each vendor start answering. Each vendor has a mix of careful, average and sloppy workers.
4. The project view compares the three vendors on quality, volume, cost per accepted unit and turnaround, using one measure for all.
5. Partway through, one worker at Vendor C starts pasting answers far faster than anyone could read the task.
6. Vendor C's gold accuracy and agreement drop. The speed and paste rules flag the worker, a case opens, and the worker's pending answers are **held** automatically.
7. Vendor C's admin sees the case with its evidence and suspends the worker. Data ops can see the case and its outcome. (`make demo` plays the people's decisions too: the admin's suspension after a review delay and data ops' clean-up. Set `DEMO_AUTOPILOT=false` to make them by hand; the researcher's half-hourly exports run either way.)
8. Data ops runs the clean-up: lineage finds every answer the worker submitted, they're removed, and the dataset is re-exported without them.
9. The audit log shows every step, and Vendor C's scorecard shows the dip and the recovery.

## 2. Scope

### In the build

| Area | Requirements | Notes |
|---|---|---|
| Workspaces and roles | VM1, VM4, VM6, N9 | Seeded demo logins per role instead of real SSO. Workspace scoping is enforced by a shared scoping module that every route goes through (FastAPI dependencies), which stands in for the gateway |
| Projects, contracts and allocation | DC1, VP1, VM7 | Contracts hold price and price unit (PD3); each allocation points to a contract |
| Doing the work | DC2, DC3, DC9 | Assignment uses the Postgres job-queue pattern (`SKIP LOCKED`, D2). Time on task is recorded on every submission. Workers see reviewer rejections with reasons, never per-task gold results |
| Measuring quality | DC4, DC7, N16, N17 | Hidden, rotatable gold tasks, overlap and agreement, worker quality scores |
| Scorecards | VP2, VP3, VP4, D11 | Background jobs compute roll-ups into Postgres summary tables, which stand in for the analytics store. The vendor's own view follows PD2 |
| Abuse | AB1, AB3, AB4, AB6, AB7, AB8, D13, D16, N18 | Worker-level rules, cases, automatic holds, human decisions, lineage clean-up |
| Export | DC6 | Datasets exported as files; re-export excludes removed data |
| Versioning | N19 | Metric and rule versions recorded with every score and flag; task schema version recorded with every task |
| Simulator | none (demo tooling) | Synthetic workers with behavior profiles, driven through the public API like real clients |

### Stand-ins

These keep the boundaries from [`DESIGN.md`](DESIGN.md) but use simpler parts:

| Design component | In the build |
|---|---|
| API gateway | Authentication and a shared scoping module in the FastAPI app |
| Analytics store | Summary tables in Postgres |
| Object storage | A local directory mounted into the container |
| Background jobs | An async worker process (`python -m app.jobs`) claiming jobs from the Postgres queue table |
| SSO | Seeded users with a role picker on the login page |
| A message broker between the platform and the detection service (D24) | The outbox table, read by the detection service through a cursor feed on the API. Event ids are made commit-ordered by a transaction-scoped advisory lock, so a reader never skips an event that commits late. The detection service keeps its state in memory and rebuilds it on start by replaying the stream up to its recorded progress |
| Extra gold for a watched worker, kept out of the vendor's comparison (D26) | The worker's next tasks are drawn from their vendor's own routed gold first, half the time. No gold is added, so the vendor's gold count and comparison are unchanged; the worker simply meets gold sooner, and colleagues at the same vendor meet slightly less of it |
| Real time, for the scripted demo | A demo clock (`make demo` only) that runs 180× real time, so about five simulated hours of work take under three minutes. Every timestamp the platform writes or compares goes through it, so time on task stays consistent. Off for `make up` and in tests |

### Not in the build

| Left out | Requirements | Why |
|---|---|---|
| Parts of the environment pipeline | EC3's difficulty estimate, EC7, vendor-built images (A15, VM2) | The [environment milestones](#environment-milestones) build a thin slice; these come after it |
| Training ingestion and observability | TO1–TO7, D5, D6 | Depends on environments |
| Vendor-level abuse and actions | AB2, AB5 | P1 in the PRD. First stretch goal |
| Vendor reviewer | DC8 | P1 in the PRD |
| The rest of the integrity layers | AB11 and AB12 beyond paste and time on task; AB14's extra overlap and spot checks; AB15, AB16; DC11's aggregate view for vendors | Audit sampling and shadow mode are the next integrity work; they feed the labels the detection service already receives |
| Vendor-private rules, cross-vendor match | AB9, AB10 | P2 in the PRD |
| Onboarding, API tokens, registry | VM2, VM3, N14 | Not needed to tell the story; vendors are seeded |
| Nice-to-haves | NH1–NH5 | Beyond the posting by definition |

### Detecting AI-pasted answers: an honest note

Telling AI-written text from human text is unreliable. The build doesn't claim to do it. It flags the **behavior** around a submission instead: answers submitted faster than the task can be read, text pasted rather than typed, and near-duplicate answers across tasks. These are A27's "starting point" signals. In the simulator, the AI-pasting worker behaves that way, so the rules catch it for the same reasons they would catch a real one.

## 3. The must-haves

From the [decision register](DECISIONS.md#what-this-means-for-the-first-build): the decisions that can't be added later for data already collected.

| # | Must-have | How the build covers it | Milestone |
|---|---|---|---|
| 1 | Immutable environment versions (D3) | Not applicable: this build stores no environments. The same rule is applied to what it does store: tasks and gold answers are immutable, and a changed task design is a new schema version | M2 |
| 2 | Lineage on every submission (D14) | Every submission stores task, worker ID, vendor, project, schema version and review; every dataset row references its submission | M2, M7 |
| 3 | Append-only audit log (VM4) | Every state change writes an audit event; the table has no update or delete path | M1 |
| 4 | Gold tasks secret and rotatable (D8, PD2) | Gold answers in their own table, never read by worker or vendor endpoints; rotation by gold-set version | M2, M3 |
| 5 | Raw cost inputs (PD3, DC9) | Price and unit on each contract (VM7); time on task on each submission | M2 |
| 6 | Opaque, vendor-scoped worker IDs (D12) | Worker IDs are random and per vendor; names exist only inside the vendor's workspace | M1 |
| 7 | Metric and rule versions recorded (N19) | Every scorecard row and flag stores the version that produced it | M5, M6 |
| 8 | Client and API signals recorded (AB11, AB12) | Partly. Every submission stores server-measured time on task and the client's paste signal. Request rates, sessions and the other client signals aren't recorded | M2, M6 |

## 4. Milestones

Each milestone ends with something that runs and a check that proves it. Hours are estimates.

| # | Milestone | Delivers | Done when | Hours | Status |
|---|---|---|---|---|---|
| M0 | Skeleton | Docker Compose with Postgres, the FastAPI app, migrations, the React app, a test runner and seeded demo logins | `docker compose up` serves a login page and a health endpoint; tests run in one command | 2 | Done |
| M1 | Workspaces, roles and audit | Orgs with capabilities, users and roles, vendor-scoped worker IDs, workspace scoping, append-only audit log | A test shows Vendor A's admin can't read Vendor B's data; every write creates an audit event | 3 | Done |
| M2 | Projects, contracts and work | Projects with schema versions, tasks, hidden gold answers, contracts with price and unit, allocations pointing to contracts, assignment queue, submissions with lineage and time on task | A worker can claim and answer tasks through the API; each submission carries full lineage | 4 | Done |
| M3 | Quality | Gold scoring, overlap and agreement, researcher spot checks with reasons, acceptance, worker quality scores, gold-set rotation, the worker's feedback view | Seeded answers produce the expected accuracy and agreement; workers see spot-check rejections with reasons; no worker or vendor endpoint returns gold data (tested) | 5 | Done |
| M4 | Simulator | Synthetic workers with profiles (careful, average, sloppy, AI-paster) driving the public API; the demo scenario script | One command runs the full demo scenario against a fresh database | 3 | Done |
| M5 | Scorecards | Roll-up jobs on the Postgres queue with metric versions; project comparison, vendor scorecard over time, vendor's own view with PD2's split | The project view ranks the three vendors; Vendor C's dip is visible; vendor views hide per-task gold results | 4 | Done |
| M6 | Abuse and cases | Versioned worker rules (speed, paste, near-duplicate), flags, cases with evidence, automatic holds, vendor-admin decisions and actions, data ops' read-only view | The AI-paster is flagged, held and suspended in the scenario; every action links to its case | 5 | Done |
| M7 | Clean-up and export | Lineage query, clean-up of a decided case, dataset export and re-export | Re-exported dataset contains none of the removed worker's answers; the audit log shows the chain | 3 | Done |
| M8 | Polish and walkthrough | UI pass across the four role views (researcher, data ops, vendor admin, worker), README with screenshots, recorded walkthrough | Someone new can run the demo from the README alone; the recording covers the nine-step story | 4 | Done |
| | | | **Total** | **33** | |

### Integrity milestones

These build the [integrity layers](DESIGN.md#integrity-layers) and the release stage (PRD PD7) around the case, hold and clean-up path from M6 and M7. They come before the stretch goals below.

| # | Milestone | Delivers | Done when | Hours | Status |
|---|---|---|---|---|---|
| M9 | Detection behind its contract | Submission events written in the submission's transaction (outbox) and read through a cursor feed; a separate detection service in Docker Compose that runs the versioned rules and returns findings with a severity and how far it has processed; a labels feed of case decisions; the platform's in-process scan removed (AB13, D23, D24, D32) | The demo's abusive worker is caught by the separate service; a single signal produces a watch finding, not a case; no event is skipped when transactions commit out of order (tested) | 4 | Done |
| M10 | Release stage | Release decided at export from evidence: detection progress, holds, minimum gap, overlap, later gold; provisional release at the cap; tier recorded on every dataset row; a release summary for the project (DC10, AB17, D25, D29, D31) | No unchecked or held answer is exported; an answer followed by failing gold isn't released; provisional rows appear only after the cap (tested); in the demo, none of the abusive worker's pasted answers reach a dataset | 4 | Done |
| M11 | Contributor grades | Quality grade with a 95% interval and integrity tier per worker; tiers set how much later gold release needs; grades snapshotted on dataset rows; a grades view for Anthropic and an aggregate for vendors (DC11, D30) | A turncoat's tier and release follow their later gold; vendors and workers can't read grades (tested) | 4 | Done |
| M12 | Discreet escalation | Watch findings raise the worker's share of gold; ten later gold answers clear the finding or open a case; a simulated worker who pastes from their own notes is watched and cleared without a case (AB14, D26, D32) | The escalated worker's tasks are indistinguishable from others' (tested); in the demo, the note-paster is cleared and the abusive worker is suspended | 3 | Done |
| | | | **Total** | **15** | |

### Environment milestones

The second half of the product: a researcher writes a brief for the environments wanted, data ops allocates it to an environment vendor, the vendor's builders submit environments against it, the platform checks them in a sandbox before anyone spends time on them, researchers review them, and approved versions are offered to training. This is core flow 1 in [`DESIGN.md`](DESIGN.md#4-core-flows) and the start of flow 3, and it covers PRD goal G6's first half and capabilities C2.3 and C3.3.

**What the slice covers.** Environment briefs, allocated like human-data projects (EC0, D33); immutable environment versions with a state machine (EC1, EC2, D3); automated checks in a sandbox: the reference solution passes, an empty solution fails, repeated runs agree (EC3, A11, A12); fair-share validation jobs (D4, N10); the builder's fix-and-resubmit loop (EC4); researcher review against the brief's rubric (EC5); a catalog that training pulls approved versions from (EC6); quarantine through an environment case (TO6, AB6, AB8, D13); environment work on the vendor scorecard (VP3).

**What it leaves out.** Difficulty estimates (the rest of EC3) need a model attempting the task. Generated programming tasks (EC7), training results and reward-hack detection (TO1–TO5, TO7) need a training stand-in. Vendor-built container images (A15) and registry credentials (VM2) are replaced by build decision B4 below.

**Build decisions for this slice**

| # | Decision | Why | Trade-off |
|---|---|---|---|
| B4 | An environment version is a bundle the builder uploads (task statement, files, tests, reference solution, and a manifest naming the test command), run on a base image the platform provides. Vendors don't push their own images | Keeps the slice to the checks that protect the reward signal, without running a registry. The checks, versioning and sandbox are the same either way | Environments can only use what the base image has. Arbitrary images (A15) and registry access (VM2) are the next step, and they make sandboxing harder, not different |
| B5 | Checks run in a separate sandbox runner with access to the Docker daemon. Each check gets a fresh container: no network, read-only root, a scratch directory, CPU, memory, process and time limits, a non-root user, and is killed on timeout | Vendor code is untrusted (N8). Plain Docker with these limits is enough to show the boundary and to test that it holds | Access to the Docker daemon is host-level power, acceptable on a laptop and nowhere else. A real deployment runs checks on dedicated hosts under gVisor or Firecracker (D4) |

| # | Milestone | Delivers | Done when | Hours | Status |
|---|---|---|---|---|---|
| M13 | Briefs and environment versions | Environment briefs as a project kind, with a review rubric and target count, allocated to vendors with the environments capability (EC0, D33); environments that each belong to a brief, and immutable versions (content-addressed bundles in object storage), the version state machine (draft → submitted → validating → failed or in review → approved → quarantined), a seeded environment builder at Kestrel (the vendor with the environments capability), per-environment contracts, the builder's upload and submit API (EC1, EC2, D3, VM6, VM7) | A brief can only be allocated to a vendor with the environments capability; a builder can only submit against a brief allocated to their vendor; a version can't be changed once stored, even by direct SQL; only legal transitions succeed, each with an audit event; a vendor without the environments capability can't submit; other vendors can't see the brief's environments (tested) | 4 | Planned |
| M14 | Sandbox validation | Validation jobs queued in the same transaction as submission and taken fairly across vendors; the sandbox runner service; three checks per version (reference passes, empty solution fails, three repeat runs agree) with logs in object storage; failed or in-review outcome (EC3, D4, N8, N10, A11, A12) | A sound environment passes; a grader that passes an empty solution fails the empty check; an unsolvable task fails the reference check; a flaky grader fails the repeat check; a check that reaches for the network or runs past its limit is stopped and failed; one vendor's burst of submissions doesn't hold back another's (tested) | 5 | Planned |
| M15 | Builder loop, review and catalog | Builder screens: versions, check results with logs, resubmit as a new version with a diff; researcher review queue per brief, graded against the brief's rubric, with feedback; progress against the brief's target count; approval; `GET /catalog` returning approved versions by exact id, with every pull recorded (EC4, EC5, EC6, D10) | A builder sees why a check failed and fixes it in a new version; only approved versions appear in the catalog; training pulls an exact version, and the pull is recorded (tested) | 3 | Planned |
| M16 | Quarantine, scorecard and demo | Environment cases owned by data ops; quarantine removes a version from the catalog and sends it back to review; lineage lists who pulled it; environment work on the vendor scorecard (validation pass rate, review grades); the demo adds a researcher's brief, allocated by data ops to Kestrel, whose builder submits a sound environment, one whose grader passes anything, one that can't be solved and one that's flaky; README and walkthrough (TO6, AB6, AB8, VP3, D13) | In `make demo`, the three broken environments are stopped by the checks that target them, the sound one is approved and pulled, then quarantined through a case and dropped from the catalog with its pulls listed; Kestrel's scorecard shows its environment pass rate | 3 | Planned |
| | | | **Total** | **15** | |

### Stretch, in order

1. Vendor-level abuse rule (quality falling while volume rises) and vendor actions with the vendor's response (AB2, AB5)
2. Vendor reviewer step (DC8)

## 5. Where the code lives

```
anthropic-jobs-5186067008/
  README.md           what this is and how to run it
  docker-compose.yml
  Makefile            up, down, test, seed, logs
  backend/            FastAPI app, migrations, background worker, tests
  detection/          the detection service: rules, severity, its own tests
  frontend/           React + TypeScript (Vite)
  simulator/          synthetic workers and the demo scenario
  docs/               posting, glossary, PRD, design, decisions, this plan, diagrams
```

## 6. Risks

| Risk | Mitigation |
|---|---|
| Polishing the UI eats the timebox | UI is plain until M8; every milestone is checked through the API and tests first |
| The demo depends on the simulator behaving believably | Worker profiles are tuned against the scenario in M4, before scorecards and rules are built on them |
| Abuse rules look naive | Rules are versioned and pluggable (D15, N19); the README says which signals are behavioral stand-ins and why |
| Scope creep from the stretch list | Stretch items start only after M8 is done |
| The sandbox is weaker than it looks | B5 says plainly that Docker with limits is a stand-in; M14 tests the limits that matter (no network, time, memory) rather than claiming isolation it doesn't have |
| Running containers per check makes the demo slow | Checks use one small base image and short tests; the demo submits four environments, not hundreds |
