# RL Data Platform: Product Requirements

| | |
|---|---|
| Status | Draft |
| Date | 2026-10-13 |
| Related | [`POSTING.md`](POSTING.md) (source) · [`GLOSSARY.md`](GLOSSARY.md) (terms) · [`DESIGN.md`](DESIGN.md) (engineering design) · [`DECISIONS.md`](DECISIONS.md) (decision register) |

This document says *what* the product is and *why*: the problem, who it serves, what each of them needs, and how we'd know it works. [`DESIGN.md`](DESIGN.md) says *how* it's built. Glossary terms are in bold on first use and are defined in the [glossary](GLOSSARY.md).

## 1. Summary

**Anthropic** needs large volumes of high-quality training data: human judgments and RL **environments**. It sources most of that through **vendors**: middlemen who recruit and manage the **workers** or build the environments. Vendors differ in what they can supply and how well they do it.

The platform is where that supply chain meets. Anthropic uses it to give out work, compare vendors on the same work, follow each vendor's performance over time, and catch and clean up abuse. Each vendor uses its own **workspace** to manage its people and to catch abuse among its own workers before it reaches Anthropic.

## 2. Problem

### The need
*"The quality of Claude's next generation depends on the quality of the data we train it on"*. Training needs both quality (one bad **reward signal** teaches a wrong lesson at scale) and volume (*"human data collection at scale"*, *"scalable code data generation"*). The work must also turn around fast: *"a loop that closes in hours and days, not quarters or months"*.

### How it's met today: through vendors
Anthropic doesn't employ the thousands of workers involved. It contracts vendors, which come in two kinds:

- **Workforce vendors** recruit, vet, pay and manage workers, and sell their output. Surge AI is the best-known example, and its worker-facing brand is reported to be DataAnnotation.tech. Mercor, Scale AI and Turing are others.
- **Environment vendors** build RL environments as software. Mechanize is reported to have worked with Anthropic on environments.

Several of the largest workforce vendors now build environments as well. Anthropic leaders have reportedly discussed spending more than $1 billion on RL environments in a year ([TechCrunch](https://techcrunch.com/2025/09/21/silicon-valley-bets-big-on-environments-to-train-ai-agents/)), so this is a large and growing supply chain.

### What goes wrong without a platform
1. **Vendors can't be compared.** Each vendor reports its own numbers in its own format. Anthropic can't tell whether vendor A's 90% acceptance rate means the same as vendor B's.
2. **Performance is invisible over time.** A vendor that was strong last quarter can slip after taking on cheaper workers, and nobody sees it until training results look wrong.
3. **Incentives point in different directions.** Vendors are paid for volume and for accepted work. Anthropic needs quality. Unless quality is measured the same way for everyone and reported quickly, volume wins.
4. **Abuse spreads before it's caught.** A worker pasting AI output, or an environment with a gameable grader, can contaminate a lot of data before anyone notices. Without **lineage**, finding and removing the affected data is guesswork.
5. **Onboarding is slow.** Each new vendor or worker pool is a custom integration, where the posting asks for *"hours, not weeks"*.

### Problem statement
> Anthropic can't currently see, compare or correct the quality of the data its vendors supply quickly enough to protect training. Vendors can't see their own quality the way Anthropic sees it, so they can't fix problems before Anthropic does.

## 3. Who cares about what

### Anthropic side

| Persona | Needs | Pain today |
|---|---|---|
| **Researcher** | Define a project once, get trustworthy data back fast, and trust that what reaches training is clean | Bad data shows up only after training goes wrong; no way to see where it came from |
| **Data ops** (vendor management) | Pick the right vendors for each project, compare them fairly, follow their performance over time, and handle abuse cases | Vendor-reported numbers that can't be compared; manual onboarding; abuse found late |

### Vendor side

| Persona | Needs | Pain today |
|---|---|---|
| **Vendor admin** | Manage their own users and roles; see their quality the way Anthropic sees it; catch and remove bad workers before Anthropic does; keep their allocation | Rejections arrive late and unexplained; can't see which workers are causing them |
| **Vendor reviewer** | Check workers' output before it goes to Anthropic | Reviewing blind, without Anthropic's quality signals |
| **Worker** (labeler or expert) | Clear instructions, steady work, fast and fair feedback | Unclear tasks; opaque quality judgments; no idea why work was rejected |
| **Environment builder** | Fast, specific feedback on whether an environment is correct and useful | Waits days to learn an environment was broken |

### The shared goal
Both sides benefit when quality is **measured the same way, visible to both, and fast**. Vendors that are actually good can prove it and win more work. Vendors that aren't get caught early. The platform's main job is to make that true.

## 4. Goals and non-goals

### Goals
- **G1** Researchers can give a project to one or more vendors and get quality-checked data back.
- **G2** Data ops can compare vendors on the same project, using measures applied identically to all.
- **G3** Data ops can follow each vendor's performance over time and spot changes early.
- **G4** Abuse is detected, investigated and acted on at both levels: Anthropic over vendors, vendors over workers. Affected data can be traced and removed.
- **G5** Each vendor manages its own users, roles and quality in its own workspace.
- **G6** Environments are validated and reviewed before training uses them, and their behavior in training feeds back into vendor performance.
- **G7** A new vendor can go from invitation to first accepted work in under a day.

### Non-goals
- Paying vendors or workers. The platform supplies the numbers that billing would use but doesn't move money.
- Recruiting workers. That's the vendor's business.
- Running model training. Training is a separate system the platform connects to.
- Holding workers' real identities. Vendors keep those (PD1).

## 5. Capabilities

Priority: **P0** is needed for the product to make sense, **P1** makes it good, **P2** comes later. The "Design" column maps to requirement IDs in [`DESIGN.md`](DESIGN.md).

### C1. Projects and allocation
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C1.1 | Create a project: task type, instructions, quality bar, volume, deadline | Researcher | P0 | DC1 |
| C1.2 | Allocate a project across vendors by **vendor capability**; change allocation while it runs | Data ops | P0 | VP1 VM6 |
| C1.3 | See project progress: volume, acceptance, turnaround, per vendor | Researcher, data ops | P0 | DC7 |

### C2. Doing the work
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C2.1 | Receive tasks, answer them, see feedback on rejected work | Worker | P0 | DC2 DC3 |
| C2.2 | Review workers' output before it goes to Anthropic | Vendor reviewer | P1 | DC8 |
| C2.3 | Submit an environment, see validation results and reviewer feedback, resubmit | Environment builder | P0 | EC1–EC4 |

### C3. Measuring quality
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C3.1 | Hidden **gold tasks**, rotated regularly, and overlap between workers, applied the same way across vendors | Platform | P0 | DC4 |
| C3.2 | Researcher review and grading of human data and environments | Researcher | P0 | EC5 |
| C3.3 | Automated environment **validation** | Platform | P0 | EC3 |
| C3.4 | Training outcomes (reward hacking, solve rates) attributed back to the environment and vendor | Platform | P1 | TO7 |
| C3.5 | Time on task measured by the platform for every submission | Platform | P1 | DC9 |

### C4. Performance views
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C4.1 | **Project view:** vendors side by side on one project (quality, volume, cost per accepted unit, turnaround) | Data ops, researcher | P0 | VP2 |
| C4.2 | **Vendor scorecard:** one vendor's performance over time, across projects, with alerts on significant drops | Data ops | P0 | VP3 |
| C4.3 | **Vendor's own view:** the vendor sees its scorecard as Anthropic sees it, broken down by worker. Reviewer feedback and agreement are live; gold-task accuracy is a daily running score (PD2) | Vendor admin | P0 | VP4 |
| C4.4 | Worker quality history within a vendor | Vendor admin, vendor reviewer | P1 | VP4 DC4 |

### C5. Abuse: detect, investigate, act
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C5.1 | Detect **worker abuse**: answers faster than reading time, near-duplicate answers, AI-generated text where human judgment is required, colluding answer patterns, gold-task farming | Platform → vendor admin | P0 | AB1 |
| C5.2 | Detect **vendor abuse**: quality falling while volume rises, banned workers returning, environments with gameable graders or copied benchmark content | Platform → data ops | P1 | AB2 TO3 |
| C5.3 | Case handling: an abuse flag opens a case with evidence; the owner records a decision. Low-quality signals feed scorecards and feedback instead (PD4) | Vendor admin, data ops | P0 | AB3 |
| C5.4 | Actions on workers: warn, retrain, suspend, remove | Vendor admin | P0 | AB4 |
| C5.5 | Actions on vendors: lower allocation, pause, offboard, after the vendor has had a chance to respond | Data ops | P1 | AB5 |
| C5.6 | Clean up data: find everything a flagged worker, vendor or environment produced (lineage), then discard it, re-label it or **quarantine** it | Data ops | P0 | AB6 TO6 |
| C5.7 | Anthropic can see vendor-level abuse cases and how each vendor resolved them | Data ops | P1 | AB7 |
| C5.8 | While a case is open, affected data is protected automatically and reversibly: a flagged worker's pending answers are held out of exports, a flagged environment is quarantined (PD4) | Platform | P0 | AB8 |
| C5.9 | Vendor-private abuse rules that open cases only inside the vendor's workspace and never affect scorecards (PD5) | Vendor admin | P2 | AB9 |
| C5.10 | Cross-vendor worker match on a keyed hash, used as evidence in cases (PD1) | Data ops | P2 | AB10 |

### C6. Vendor workspace
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C6.1 | Invite users and assign roles (admin, reviewer, worker, environment builder) | Vendor admin | P0 | VM1 |
| C6.2 | Self-serve onboarding with setup checks | Vendor admin | P1 | VM3 |
| C6.3 | API keys and registry credentials for the vendor's own tooling | Vendor admin | P1 | VM2 |

### C7. Platform basics
Sign-in, permissions, tenant isolation, audit trail and API access. These are standard for running any multi-tenant platform. They're specified in [`DESIGN.md`](DESIGN.md) (VM1, VM4, VM5, N9, N11, N12, N16–N19) and not repeated here.

## 6. How we'd know it works

These are proposed targets, to be checked against real baselines.

| Metric | For | Target |
|---|---|---|
| Time from project creation to first accepted data | Researcher | < 1 day |
| Share of training data traceable to its vendor and pseudonymous worker | Researcher, data ops | 100% |
| Time from worker abuse starting to the worker being stopped | Vendor admin | < 24 hours |
| Time from vendor abuse being flagged to affected data being quarantined | Data ops | < 1 hour after the decision |
| Share of quality problems vendors catch before Anthropic does | Both | Rising over time |
| Vendor-reported and platform-measured acceptance rate | Data ops | Same number, because both read the same measure |
| Time for a new vendor to reach first accepted work | Data ops, vendor admin | < 1 day |

## 7. Product decisions

| # | Question | Decision | Why | Trade-off accepted |
|---|---|---|---|---|
| PD1 | How much worker detail can Anthropic see? | Workers are pseudonymous: Anthropic sees an ID scoped to the worker's vendor, never a name. The ID scheme leaves room to add a cross-vendor match on a keyed hash of a stable identifier later (C5.10); a match would be evidence in a case, not an automatic ban, since many contractors legitimately work for several vendors | Workers' privacy and vendors' rosters stay with the vendor; Anthropic doesn't hold personal data for thousands of contractors | Until the match exists, a worker removed by one vendor can join another unseen |
| PD2 | How quickly do vendors see their quality numbers? | Split by signal. Reviewer feedback and agreement are shown live. Gold-task accuracy is shown only as a running score per worker, once the worker has about 20 gold results, refreshed daily. Gold tasks are rotated | Per-task, instant right/wrong feedback would reveal which tasks are gold, and workers who can spot tests can game them | Vendors learn about accuracy problems up to a day later than they could |
| PD3 | How is cost compared? | Each contract is stored in its own unit (per hour, per task or per environment) and compared as cost per accepted unit. Hourly contracts are converted using time on task measured by the platform, not vendor timesheets | Vendors are priced differently in practice (experts hourly, labeling per task); one normalized measure keeps comparisons fair | Comparisons depend on the platform's time measurement being accurate |
| PD4 | What counts as abuse versus low quality, and who decides? | Low quality (honest but poor work) feeds scorecards and feedback and opens no case. Abuse signals open a case. The vendor admin decides worker cases; data ops decides vendor and environment cases, after the vendor can respond. The platform protects data automatically and reversibly while a case is open; any action against a person or vendor needs a human decision. Anthropic can always decline data, whatever a vendor decides about its worker | Intent can't be measured, only evidence; holds limit damage quickly without punishing anyone on a false positive | A burst of false flags can hold back data until cases are reviewed |
| PD5 | Who writes abuse-detection rules? | Shared platform rules are the only ones that affect scorecards and Anthropic's decisions. Later (C5.9), vendors can add private rules that open cases only inside their own workspace | Every vendor is measured by the same rules; vendors can still catch patterns specific to their workforce | Vendors can't influence the shared rules directly |

## 8. Nice-to-haves beyond the posting

Features the posting doesn't ask for that would make the product better. None of them is needed for the core loop, so they sit after every P0 and P1 capability. Each says which existing decision it would touch, if any.

| # | Feature | Who it helps | Why it's worth having | Why it isn't core |
|---|---|---|---|---|
| NH1 | **Migration from another platform.** Import a vendor's existing projects, gold sets, worker quality history and accepted data from the tools Anthropic uses today | Data ops, vendor admins | Anthropic already works with vendors, so a new platform starts with relationships but no history. Without import, scorecards (C4.2) begin blank, proven gold sets have to be rebuilt, and switching costs make vendors reluctant to move. It supports the posting's *"ramp up in hours, not weeks"* | It's a one-time cost per source system. Imported data can't carry lineage it never had (D14), so it has to be marked as imported, with whatever provenance the old system kept |
| NH2 | **Connecting a vendor's own work tools.** Large vendors already run their own worker platforms (Surge's DataAnnotation, for example) and may prefer to pull tasks into them over the API rather than send workers to ours | Vendor admins, workers | The biggest vendors are the least likely to move thousands of workers to a new interface, so this widens the supply Anthropic can reach | Several signals are measured inside our interface: time on task (PD3) and some abuse signals (AB1). Through an outside tool they become vendor-reported, which weakens consistent measurement (N16). Scorecards would need to show which numbers are platform-measured |
| NH3 | **Worker appeals.** A worker can contest a rejection or a quality result; upheld appeals correct the score | Workers, vendor admins, researchers | Gold answers and reviewers are sometimes wrong. Appeals are the cheapest way to find bad gold tasks before they distort every worker's score, and fairness keeps good workers on a vendor's roster | Needs care not to reveal gold tasks (PD2): an appeal against a gold result tells the worker that task was a test, so contested gold tasks should be retired |
| NH4 | **Instruction experiments.** Run two versions of a project's instructions or task design side by side, and compare agreement, accuracy and speed | Researchers | The posting's loop is *"iterate on data collection strategies"* fast. Measuring which instructions produce better data turns that iteration from guesswork into evidence, and reuses the quality measures the platform already has | Core value comes first from running projects at all. Experiments rely on versioned task schemas (N19), which the design already requires |
| NH5 | **Claude as first-pass reviewer.** A model screens submissions and environments for likely problems (unclear instructions, answers that ignore the task, environments whose instructions don't match the grader) and queues them for human review | Researchers, vendor reviewers | Reviewer time is the scarcest resource in the loop. Triage spends it where problems are likely. It also matches how the team works: *"Claude is your pair programmer"* | Grading with a model is out of scope (DESIGN §7). This stays triage only: the model never accepts or rejects work, and its flags are low-quality signals rather than abuse cases (PD4) |

## Sources

- [TechCrunch: Silicon Valley bets big on 'environments' to train AI agents](https://techcrunch.com/2025/09/21/silicon-valley-bets-big-on-environments-to-train-ai-agents/) (environment vendors, Mechanize and Anthropic, the $1B figure, workforce vendors moving into environments)
- [Surge AI, Wikipedia](https://en.wikipedia.org/wiki/Surge_AI) and [Sacra: Surge AI](https://sacra.com/research/surge-ai) (Surge's customers and revenue)
- [Second Talent](https://www.secondtalent.com/resources/is-dataannotation-legit/) and [Revenue Memo](https://www.revenuememo.com/p/who-owns-dataannotation) (DataAnnotation operated by Surge; secondary sources)
- [Beri: run two vendors and keep the gold set](https://www.beri.net/article/scale-ai-alternatives-data-labeling-vendors-second-source-gold-set-2026) (multi-vendor sourcing with shared gold sets)
