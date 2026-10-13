# RL Data Platform: Product Requirements

| | |
|---|---|
| Status | Draft |
| Date | 2026-10-13 |
| Related | [`POSTING.md`](POSTING.md) (source) · [`GLOSSARY.md`](GLOSSARY.md) (terms) · [`DESIGN.md`](DESIGN.md) (engineering design) |

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
- Holding workers' real identities. Vendors keep those (see open question Q1).

## 5. Capabilities

Priority: **P0** is needed for the product to make sense, **P1** makes it good, **P2** comes later. The "Design" column maps to requirement IDs in [`DESIGN.md`](DESIGN.md).

### C1. Projects and allocation
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C1.1 | Create a project: task type, instructions, quality bar, volume, deadline | Researcher | P0 | DC1 |
| C1.2 | Allocate a project across vendors by **vendor capability**; change allocation while it runs | Data ops | P0 | (new) |
| C1.3 | See project progress: volume, acceptance, turnaround, per vendor | Researcher, data ops | P0 | DC7 |

### C2. Doing the work
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C2.1 | Receive tasks, answer them, see feedback on rejected work | Worker | P0 | DC2 DC3 |
| C2.2 | Review workers' output before it goes to Anthropic | Vendor reviewer | P1 | (new) |
| C2.3 | Submit an environment, see validation results and reviewer feedback, resubmit | Environment builder | P0 | EC1–EC4 |

### C3. Measuring quality
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C3.1 | Hidden **gold tasks** and overlap between workers, applied the same way across vendors | Platform | P0 | DC4 |
| C3.2 | Researcher review and grading of human data and environments | Researcher | P0 | EC5 |
| C3.3 | Automated environment **validation** | Platform | P0 | EC3 |
| C3.4 | Training outcomes (reward hacking, solve rates) attributed back to the environment and vendor | Platform | P1 | TO1–TO3 |

### C4. Performance views
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C4.1 | **Project view:** vendors side by side on one project (quality, volume, cost, turnaround) | Data ops, researcher | P0 | (new) |
| C4.2 | **Vendor scorecard:** one vendor's performance over time, across projects, with alerts on significant drops | Data ops | P0 | (new) |
| C4.3 | **Vendor's own view:** the vendor sees its scorecard as Anthropic sees it, broken down by worker | Vendor admin | P0 | (new) |
| C4.4 | Worker quality history within a vendor | Vendor admin, vendor reviewer | P1 | DC4 |

### C5. Abuse: detect, investigate, act
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C5.1 | Detect **worker abuse**: answers faster than reading time, near-duplicate answers, AI-generated text where human judgment is required, colluding answer patterns, gold-task farming | Platform → vendor admin | P0 | (new) |
| C5.2 | Detect **vendor abuse**: quality falling while volume rises, banned workers returning, environments with gameable graders or copied benchmark content | Platform → data ops | P1 | TO3 (partly) |
| C5.3 | Case handling: a flag opens a case with evidence; the owner records a decision | Vendor admin, data ops | P0 | (new) |
| C5.4 | Actions on workers: warn, retrain, suspend, remove | Vendor admin | P0 | (new) |
| C5.5 | Actions on vendors: lower allocation, pause, offboard | Data ops | P1 | (new) |
| C5.6 | Clean up data: find everything a flagged worker, vendor or environment produced (lineage), then discard it, re-label it or **quarantine** it | Data ops | P0 | TO6 N12 |
| C5.7 | Anthropic can see vendor-level abuse cases and how each vendor resolved them | Data ops | P1 | (new) |

### C6. Vendor workspace
| # | Capability | Who | Priority | Design |
|---|---|---|---|---|
| C6.1 | Invite users and assign roles (admin, reviewer, worker, environment builder) | Vendor admin | P0 | VM1 |
| C6.2 | Self-serve onboarding with setup checks | Vendor admin | P1 | VM3 |
| C6.3 | API keys and registry credentials for the vendor's own tooling | Vendor admin | P1 | VM2 |

### C7. Platform basics
Sign-in, permissions, tenant isolation, audit trail and API access. These are standard for running any multi-tenant platform. They're specified in [`DESIGN.md`](DESIGN.md) (VM1, VM4, VM5, N9, N11, N12) and not repeated here.

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

## 7. Open questions

| # | Question | Why it matters |
|---|---|---|
| Q1 | How much worker detail can Anthropic see? A pseudonymous worker ID is enough to trace data and spot patterns, but not to stop a banned worker from joining another vendor | Privacy and vendor trust versus catching repeat offenders across vendors |
| Q2 | Do vendors see their own quality numbers in real time, or after a delay? | Real time helps them fix problems; it also helps them reverse-engineer gold tasks |
| Q3 | How is cost measured: per task, per accepted task, or per hour? | Determines how fairly vendors can be compared |
| Q4 | Which signals count as abuse versus low quality, and who decides? | A false abuse finding against a worker or vendor has real consequences |
| Q5 | Is the abuse detector shared across vendors, or can vendors add their own rules? | Shared rules make comparisons fair; vendor rules catch vendor-specific patterns |

## Sources

- [TechCrunch: Silicon Valley bets big on 'environments' to train AI agents](https://techcrunch.com/2025/09/21/silicon-valley-bets-big-on-environments-to-train-ai-agents/) (environment vendors, Mechanize and Anthropic, the $1B figure, workforce vendors moving into environments)
- [Surge AI, Wikipedia](https://en.wikipedia.org/wiki/Surge_AI) and [Sacra: Surge AI](https://sacra.com/research/surge-ai) (Surge's customers and revenue)
- [Second Talent](https://www.secondtalent.com/resources/is-dataannotation-legit/) and [Revenue Memo](https://www.revenuememo.com/p/who-owns-dataannotation) (DataAnnotation operated by Surge; secondary sources)
- [Beri: run two vendors and keep the gold set](https://www.beri.net/article/scale-ai-alternatives-data-labeling-vendors-second-source-gold-set-2026) (multi-vendor sourcing with shared gold sets)
