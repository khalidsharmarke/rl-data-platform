# Build plan

| | |
|---|---|
| Status | Planned |
| Timebox | About a week: ~32 hours across nine milestones |
| Stack | Python (FastAPI, async) · Postgres · React + TypeScript (Vite) · Docker Compose |
| Delivered as | This repo, runnable with `docker compose up`, plus a 2–3 minute recorded walkthrough embedded in the README |
| Related | [`PRD.md`](PRD.md) · [`DESIGN.md`](DESIGN.md) · [`DECISIONS.md`](DECISIONS.md) · [`GLOSSARY.md`](GLOSSARY.md) |

## 1. What the build shows

The build covers the PRD's core: **several vendors working on the same project, measured the same way, with abuse caught and cleaned up.** These are core flows 2, 4 and 5 in [`DESIGN.md`](DESIGN.md#4-core-flows). The environment pipeline (flows 1 and 3) isn't part of this build.

### The demo story

The seeded scenario runs on its own, so a reviewer can watch it from start to finish:

1. A researcher creates a project: *"Which of these two answers is more helpful?"*, 3,000 comparisons, 5% gold tasks.
2. Data ops allocates it across three vendors with different contracts: Vendor A per task, Vendor B hourly, Vendor C per task at a lower price.
3. Simulated workers at each vendor start answering. Each vendor has a mix of careful, average and sloppy workers.
4. The project view compares the three vendors on quality, volume, cost per accepted unit and turnaround, using one measure for all.
5. Partway through, one worker at Vendor C starts pasting answers far faster than anyone could read the task.
6. Vendor C's gold accuracy and agreement drop. The speed and paste rules flag the worker, a case opens, and the worker's pending answers are **held** automatically.
7. Vendor C's admin sees the case with its evidence and suspends the worker. Data ops can see the case and its outcome.
8. Data ops runs the clean-up: lineage finds every answer the worker submitted, they're removed, and the dataset is re-exported without them.
9. The audit log shows every step, and Vendor C's scorecard shows the dip and the recovery.

## 2. Scope

### In the build

| Area | Requirements | Notes |
|---|---|---|
| Workspaces and roles | VM1, VM4, VM6, N9 | Seeded demo logins per role instead of real SSO. Workspace scoping is enforced in API middleware, which stands in for the gateway |
| Projects and allocation | DC1, VP1 | Allocation records share, price and price unit (PD3) |
| Doing the work | DC2, DC3, DC9 | Assignment uses the Postgres job-queue pattern (`SKIP LOCKED`, D2). Time on task is recorded on every submission |
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
| API gateway | Middleware in the FastAPI app |
| Analytics store | Summary tables in Postgres |
| Object storage | A local directory mounted into the container |
| Background jobs | An async worker process claiming jobs from the Postgres queue table |
| SSO | Seeded users with a role picker on the login page |

### Not in the build

| Left out | Requirements | Why |
|---|---|---|
| Environment pipeline and sandboxing | EC1–EC7, N8, D3, D4 | The costliest part to do safely, and a separate story. It would be the next slice |
| Training ingestion and observability | TO1–TO7, D5, D6 | Depends on environments |
| Vendor-level abuse and actions | AB2, AB5 | P1 in the PRD. First stretch goal |
| Vendor reviewer | DC8 | P1 in the PRD |
| Vendor-private rules, cross-vendor match | AB9, AB10 | P2 in the PRD |
| Onboarding, API tokens, registry | VM2, VM3 | Not needed to tell the story |
| Nice-to-haves | NH1–NH5 | Beyond the posting by definition |

### Detecting AI-pasted answers: an honest note

Telling AI-written text from human text is unreliable. The build doesn't claim to do it. It flags the **behavior** around a submission instead: answers submitted faster than the task can be read, text pasted rather than typed, and near-duplicate answers across tasks. These are A27's "starting point" signals. In the simulator, the AI-pasting worker behaves that way, so the rules catch it for the same reasons they would catch a real one.

## 3. The seven must-haves

From the [decision register](DECISIONS.md#what-this-means-for-the-first-build): the decisions that can't be added later for data already collected.

| # | Must-have | How the build covers it | Milestone |
|---|---|---|---|
| 1 | Immutable environment versions (D3) | Not applicable: this build stores no environments. The same rule is applied to what it does store: tasks and gold answers are immutable, and a changed task design is a new schema version | M2 |
| 2 | Lineage on every submission (D14) | Every submission stores task, worker ID, vendor, project, schema version and review; every dataset row references its submission | M2, M7 |
| 3 | Append-only audit log (VM4) | Every state change writes an audit event; the table has no update or delete path | M1 |
| 4 | Gold tasks secret and rotatable (D8, PD2) | Gold answers in their own table, never read by worker or vendor endpoints; rotation by gold-set version | M2, M3 |
| 5 | Raw cost inputs (PD3, DC9) | Price and unit on each allocation; time on task on each submission | M2 |
| 6 | Opaque, vendor-scoped worker IDs (D12) | Worker IDs are random and per vendor; names exist only inside the vendor's workspace | M1 |
| 7 | Metric and rule versions recorded (N19) | Every scorecard row and flag stores the version that produced it | M5, M6 |

## 4. Milestones

Each milestone ends with something that runs and a check that proves it. Hours are estimates.

| # | Milestone | Delivers | Done when | Hours |
|---|---|---|---|---|
| M0 | Skeleton | Docker Compose with Postgres, the FastAPI app, migrations, the React app, a test runner and seeded demo logins | `docker compose up` serves a login page and a health endpoint; tests run in one command | 2 |
| M1 | Workspaces, roles and audit | Orgs with capabilities, users and roles, vendor-scoped worker IDs, workspace-scoping middleware, append-only audit log | A test shows Vendor A's admin can't read Vendor B's data; every write creates an audit event | 3 |
| M2 | Projects, allocation and work | Projects with schema versions, tasks, hidden gold answers, allocations with price and unit, assignment queue, submissions with lineage and time on task | A worker can claim and answer tasks through the API; each submission carries full lineage | 4 |
| M3 | Quality | Gold scoring, overlap and agreement, acceptance, worker quality scores, gold-set rotation | Seeded answers produce the expected accuracy and agreement; no worker or vendor endpoint returns gold data (tested) | 4 |
| M4 | Simulator | Synthetic workers with profiles (careful, average, sloppy, AI-paster) driving the public API; the demo scenario script | One command runs the full demo scenario against a fresh database | 3 |
| M5 | Scorecards | Roll-up jobs on the Postgres queue with metric versions; project comparison, vendor scorecard over time, vendor's own view with PD2's split | The project view ranks the three vendors; Vendor C's dip is visible; vendor views hide per-task gold results | 4 |
| M6 | Abuse and cases | Versioned worker rules (speed, paste, near-duplicate), flags, cases with evidence, automatic holds, vendor-admin decisions and actions, data ops' read-only view | The AI-paster is flagged, held and suspended in the scenario; every action links to its case | 5 |
| M7 | Clean-up and export | Lineage query, clean-up of a decided case, dataset export and re-export | Re-exported dataset contains none of the removed worker's answers; the audit log shows the chain | 3 |
| M8 | Polish and walkthrough | UI pass across the four role views (researcher, data ops, vendor admin, worker), README with screenshots, recorded walkthrough | Someone new can run the demo from the README alone; the recording covers the nine-step story | 4 |
| | | | **Total** | **32** |

### Stretch, in order

1. Vendor-level abuse rule (quality falling while volume rises) and vendor actions with the vendor's response (AB2, AB5)
2. Vendor reviewer step (DC8)
3. A thin environment slice: immutable environment versions with reference and no-op checks in a locked-down container (EC1–EC3, D3)

## 5. Where the code lives

```
anthropic-jobs-5186067008/
  app/
    backend/      FastAPI app, migrations, background worker, tests
    frontend/     React + TypeScript (Vite)
    simulator/    synthetic workers and the demo scenario
    docker-compose.yml
```

## 6. Risks

| Risk | Mitigation |
|---|---|
| Polishing the UI eats the timebox | UI is plain until M8; every milestone is checked through the API and tests first |
| The demo depends on the simulator behaving believably | Worker profiles are tuned against the scenario in M4, before scorecards and rules are built on them |
| Abuse rules look naive | Rules are versioned and pluggable (D15, N19); the README says which signals are behavioral stand-ins and why |
| Scope creep from the stretch list | Stretch items start only after M8 is done |
