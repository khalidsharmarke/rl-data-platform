# RL Data Platform

A working slice of a platform for sourcing AI training data through many vendors. Work is split across vendors, every vendor is measured the same way by the platform, and abuse is caught, decided on by a person, and cleaned out of the data, with a trail back to every answer. Vendors also build training environments to a researcher's brief, and the platform checks each one in a sandbox before anyone reviews it or training uses it. When training uses them, the platform watches the results, catches an environment being gamed, and tells training which runs to roll back.

Built in response to Anthropic's [Full-Stack Software Engineer, Reinforcement Learning](docs/POSTING.md) posting. It builds the data-collection core of the design and thin slices of environment creation and training observability, with training itself played by a stand-in. [Limitations](docs/LIMITATIONS.md) says what it doesn't do and where it falls short.

**[Watch the five-minute walkthrough](docs/media/walkthrough.mp4)** · [Product requirements](docs/PRD.md) · [Design](docs/DESIGN.md) · [Decisions](docs/DECISIONS.md) · [Build plan](docs/BUILD.md) · [Limitations](docs/LIMITATIONS.md) · [What we chose not to solve](docs/NOT-SOLVED.md)

![Scorecards: three vendors compared on identical measures, with Brightline's dip](docs/media/scorecards.png)

## The story `make demo` tells

Three fictional vendors work on one project, *"Which of these two answers is more helpful?"*: 3,000 comparisons, two answers each, 5% hidden test questions (gold tasks). Thirty simulated workers answer through the public API. About five simulated hours run in under three minutes.

1. **Data ops splits the work.** Northwind (40%, $0.80 per task), Kestrel (35%, $38 per hour) and Brightline (25%, $0.55 per task).
2. **Every vendor is measured the same way.** The platform computes gold accuracy, agreement, acceptance, cost per accepted answer and turnaround. No vendor reports its own numbers. Brightline is the cheapest per task and the most expensive per accepted answer.
3. **Halfway through its work, one Brightline worker starts pasting the same generic reason on every task**, faster than anyone could read it. Brightline's gold accuracy and acceptance drop.
4. **The platform notices and opens a case.** A separate detection service reads every submission and reports three signals that agree: speed, pasting and duplicates. The worker's answers are held out of training automatically. Nothing is done *to the worker* yet.
5. **One signal on its own isn't a case.** A careful Northwind worker pastes reasons from their own notes. Pasting alone only puts them on watch, so the platform quietly gives them more test questions. Their good answers clear them, and their vendor never sees a case.
6. **Detection is measured, and new rules prove themselves first.** Every spot check also asks whether the answer looks like abuse, and a random share of answers is mixed into the queue so reviewers can't tell audits apart. A new rule that flags very short reasons runs in shadow: it flags more than a dozen honest workers, reviewers judge none of its flags abusive, and it never reaches a vendor. The audit sample finds no abuse the live rules missed.
7. **Workers flag broken tasks.** A bug made both answers identical in the project's last batch of tasks. Workers who reach them flag them instead of answering, and a task two workers flag stops being routed. The agreement drop raises an alert listing the worst tasks; the researcher retires them, and their answers count nowhere.
8. **Brightline's admin decides.** She sees what was observed, with examples, but never the rules' thresholds or which tasks were tests. She suspends the worker, with a reason on record.
9. **Data ops cleans up.** Lineage shows every answer the worker gave. None of her pasted answers reached a dataset: the release stage held them back, because the test questions she answered after them were failing. A few honest answers from before she switched may have gone out; nobody can say exactly when abuse begins, so one click removes all her answers and reopens those tasks for other workers.
10. **Every export contains only released answers**: checked by detection, past a minimum gap, and backed by good test answers from the same worker afterwards. How many depends on the worker's grade: a trusted worker needs one, a new one three. Grades rate quality and integrity separately, so an honest but sloppy worker is trusted with low quality, not treated as abusive. Each row is tagged verified or provisional and points back to its source.
11. **The audit log holds the whole chain** and can't be edited, even by direct SQL.

### The second act: environments

12. **A researcher writes a brief** for Python bug-fix environments, with constraints (nothing copied from public benchmarks) and a review rubric. Data ops gives it to Kestrel, the vendor set up to build environments.
13. **Kestrel's builder submits four environments.** Each version is stored by hash in three parts: the task files the model works in, the grader, and the reference solution.
14. **The sandbox checks each one** in fresh containers with no network and strict limits, keeping the grader out of the attempt: the reference solution must pass, the untouched task files must fail, and ten repeat runs must agree. Three are stopped: a grader that rewards an empty answer, a task even its own solution can't pass, and a test that passes at random. The builder sees why, with logs.
15. **The researcher grades the sound one against the brief's rubric** and approves it. Training pulls it from the catalog: the task files and grader, never the reference solution.
16. **Later, a researcher spots that the task copies a public benchmark.** Data ops opens a case: the version is quarantined and leaves the catalog at once, and the case lists which training run pulled it. Data ops retires it.

### The third act: training

17. **Kestrel's builder submits two more.** One is sound. The other, "slugify edge cases", has a grader that only checks the empty string, so a function that returns `""` scores full marks. Both pass the sandbox's checks: the reference passes, the untouched task fails, the repeats agree. The researcher approves both; nothing in a review shows the hole.
18. **Training pulls both and starts a run**, sending back every episode with its run, step, reward, length and transcript. Training here is a stand-in with a modeled policy ([B6](docs/BUILD.md#training-observability-milestones)), and the hack is one we planted; a sandbox test proves `return ""` really beats this grader.
19. **Researchers watch the run live:** solve rate, reward variance and attempt length per environment, with drill-down to each transcript. Partway through, the weak environment's solve rate leaps while its attempts shrink from about 40 turns to 4.
20. **A separate hack detector reads the episodes.** At 20% short full-mark attempts it asks a researcher to read transcripts; at 50% it opens a case, and the version is quarantined without a person. The sound environment raises nothing and keeps training.
21. **The case issues a recall** naming the run, the step it first used the version, and where the flagged attempts began. Training reads it, rolls back to the checkpoint before the first flagged step, and reports that; the report lands on the case. The platform never touches a model.
22. **Data ops retires the version.** Kestrel's scorecard shows its environment work, from versions submitted to quarantines, and what happened in training: episodes, solve rate, flagged attempts and recalls.

![A builder's view of a brief: one environment quarantined, three stopped by the sandbox's checks, with the reason and logs](docs/media/environment-checks.png)

| Brightline's admin: the case | Data ops: lineage and clean-up |
|---|---|
| ![Case as the vendor admin sees it](docs/media/case-vendor.png) | ![Case as data ops sees it, with lineage](docs/media/case-data-ops.png) |

![Worker grades and a cleared escalation, as Anthropic sees them](docs/media/grades.png)

## Run it

Needs Docker with Compose. The sandbox runner starts a container for every environment check through your Docker daemon, which is fine on a laptop and nowhere else ([B5](docs/BUILD.md#environment-milestones)).

```sh
make demo    # fresh database, then the scripted scenario above (about 4 minutes)
make up      # start Postgres, the API, the background worker and the web app on real time
make test    # 165 backend tests, 38 detection-service tests, and 13 sandbox tests on real containers
make down    # stop (make reset also deletes the database)
```

Open http://localhost:5173 and pick a demo user. The login page suggests a path through the story. The API is at http://localhost:8000 (interactive docs at `/docs`).

`make demo` plays the people in the story too: the admin's suspension and data ops' clean-up. To make those decisions yourself, run `DEMO_AUTOPILOT=false make demo`, then sign in as Grace Adeyemi (Brightline's admin) and then Sam Lindqvist (data ops).

## What's real and what stands in

| Built | Stand-in for |
|---|---|
| FastAPI service with workspace scoping on every route | The API gateway (SSO, tenant scoping) |
| Seeded demo logins | Real sign-in |
| Postgres job table claimed with `SKIP LOCKED` | A message queue (needless at this load; see [D2](docs/DESIGN.md)) |
| Summary tables in Postgres | An analytics store |
| A Docker volume | Object storage for exported datasets |
| A training stand-in that pulls approved environments, sends back episodes from a modeled policy, and rolls back when recalled | Training infrastructure and the model being trained ([A23](docs/DESIGN.md), [B6](docs/BUILD.md#training-observability-milestones)) |
| An outbox table the detection service reads through a cursor feed | A message broker between the platform and the detection service ([D24](docs/DESIGN.md)) |
| Docker containers with no network and strict limits, started per check | A hardened sandbox (gVisor or Firecracker) on dedicated hosts ([B5](docs/BUILD.md#environment-milestones)) |
| A demo clock running 180× real time, `make demo` only | Waiting five hours for the story |

Out of scope so far: vendor-built environment images, vendor-level abuse rules, vendor reviewers, and extra overlap during escalation. [BUILD.md](docs/BUILD.md#2-scope) lists everything left out and why, and [LIMITATIONS.md](docs/LIMITATIONS.md) covers where the built part falls short.

**On detecting AI-written answers.** The platform doesn't try to tell AI text from human text; that's unreliable. It flags the *behavior* around a submission: answering faster than the task can be read, pasting rather than typing, and giving the same reason to different questions. Honest but sloppy work isn't flagged: it shows up as low quality on the scorecard instead.

## What building it caught

Each of these was found by testing or by looking at the demo's output, fixed, and covered by a test or recorded as a decision:

- **A race in task claiming:** under 12 concurrent workers, a task got three answers when it allowed two. The claiming query's snapshot was older than its lock. Fixed with a recount after locking; a concurrency test with real commits reproduces it.
- **Three ways gold tasks could be recognized.**
  - Picking every 20th task landed on only 3 of 12 questions.
  - Routing gave each vendor a contiguous block, so every vendor's stream ended in a run of test questions.
  - An audit event visible to vendors included the gold count.

  All three are fixed, and D8 now says gold must be indistinguishable by content, position or routing.
- **A noisy alert:** a vendor alert compared against one lucky early period. It now uses a two-proportion test ([D19](docs/DESIGN.md)).

## Where things are

```
backend/     FastAPI app, migrations, background worker (app/jobs.py), the detection contract (app/routes/detection.py), rule modes, the audit sample and detection's measures (app/detection_quality.py, app/routes/detectors.py), broken tasks and agreement alerts (app/routes/task_health.py), the release stage (app/release.py), contributor grades (app/grades.py), discreet escalation (app/escalation.py), environments and versions (app/environments.py), the sandbox runner (app/sandbox.py), the catalog (app/routes/catalog.py), episode intake and training dashboards (app/routes/training.py), training statistics (app/training_stats.py), the hack-detection contract and recalls (app/routes/hacks.py, app/environment_cases.py), fixture environments (tests/fixtures/environments), 165 tests
sandbox/     the sandbox runner's image and the base image environment checks run on
detection/   the separate detection services: abuse rules and severity (rules.py), their state (detector.py), the reward-hack detector (hacks.py, hack_service.py), 38 tests
frontend/    React + TypeScript: one app, a view per role
simulator/   the scripted scenario, driving the public API, and the training stand-in (training.py)
docs/        posting, glossary, PRD, design, decisions, build plan, diagrams, media
```
