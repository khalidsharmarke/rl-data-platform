# Decision register

Every product and design decision made so far, sorted by how hard it would be to undo. Product decisions (`PD#`) are explained in [`PRD.md`](PRD.md#7-product-decisions); design decisions (`D#`) and assumptions (`A#`) in [`DESIGN.md`](DESIGN.md). Framing decisions (`F#`) shape both documents and are recorded only here; build decisions (`B#`) are explained in [`BUILD.md`](BUILD.md).

## How decisions are classified

| Class | Meaning |
|---|---|
| **One-way** | Can't be undone for data already recorded or for things already shown to vendors or workers. Has to be right from the first release |
| **Hardens** | Cheap to change now, but becomes one-way at a known point, usually the first vendor onboarded, the first data recorded, or the first external integration |
| **Two-way** | Can be changed later by changing code. The cost grows with time but no information is lost |

**The pattern.** The one-way decisions are almost all about two things: **data the platform records, or fails to record, from day one**, and **what vendors and workers have already been shown**. Decisions about code structure and infrastructure are two-way. So the first build has to get the one-way decisions right, even in minimal form, while the two-way ones can start simple.

## One-way

| ID | Decision | Why it can't be undone | Keeps the damage contained |
|---|---|---|---|
| D3 | Environment versions are immutable; status changes only through the state machine | If versions could change before the rule existed, nobody can tell which content training actually used. Immutability can't be applied to the past | Enforce it from the first environment stored |
| D14 | Lineage is recorded when data is created | A submission stored without its worker, vendor and review can never be traced, so abuse clean-up (AB6) can't reach it | Record lineage from the first submission, even before any clean-up tooling exists |
| VM4 / N12 | The audit log is append-only | Events that were never recorded, or were overwritten, can't be recovered | Write audit events from the first state change |
| PD2 | Gold-task accuracy is shown to vendors only as a daily aggregate | Tightening this later is easy. **Loosening** it is one-way: once per-task gold results are shown, those gold tasks are known and have to be retired | Rotate gold tasks (DC4), so any exposed set is replaced over time |
| D8, D15 | Gold answers and detector rules are never exposed to vendors or workers | Same as PD2: a leaked gold set or a published rule can't be made secret again | Rotate gold tasks; keep rules pluggable (A27) so a known rule can be replaced |
| D26 / PD6 | Automatic responses to a finding can't be perceived by its subject | Same as PD2: once workers have seen how and when the platform reacts, they know what triggers it, and that can't be taken back | Keep evidence shown to vendors to what was observed (N18), never thresholds |
| AB11, AB12 | Client and API signals are recorded with every submission | A signal not captured at submission can't be reconstructed, so a new detector can't be tried in shadow mode (D27) against past work | Record raw signals, including ones no rule uses yet |
| PD3 | Cost is compared per accepted unit, using platform-measured time on task | The comparison itself is two-way (it's a calculation). What's one-way is **recording the inputs**: each contract's price and unit (VM7), and each submission's time on task (DC9). Data not captured can't be recomputed under a different definition | Store raw inputs, never only the computed cost |

## Hardens

| ID | Decision | Cheap to change until | Keeps it reversible |
|---|---|---|---|
| F1 | One platform, run by Anthropic, with vendors as tenants (not a separate deployment per vendor) | The first vendor is onboarded. After that, every vendor's users, data and integrations assume the shared platform | Keep workspace scoping in one layer (the gateway and API, N9), not spread through the code |
| PD1 / D12 | Workers are pseudonymous to Anthropic, with IDs scoped to their vendor | The first submissions are stored. Adding the cross-vendor match later (AB10) stays possible because vendors still know their workers and can submit hashes for existing ones. **Holding identities and then dropping them** would mean deleting data | Keep the worker ID opaque and separate from any vendor-supplied identifier |
| D11 | Every performance number is computed by the platform, one code path for all vendors | Vendors are first shown a scorecard. After that, changing a metric's definition changes numbers vendors have already seen, which damages trust and breaks comparisons over time | Record which metric definition produced each score, so a new definition can run alongside the old one |
| PD5 | Only shared rules feed scorecards | Same moment as D11: changing the rules changes past and future scores | Record which rule version raised each flag |
| D18 | What counts as an accepted answer (metric quality-v1) | Vendors are first shown acceptance numbers (M5 scorecards) | Record the metric version with every score (N19), so a new definition can run alongside the old one |
| D5, D6 | Training results go straight to an intake that deduplicates by episode id | Training infrastructure integrates. The endpoint and the stable episode id become a contract with another team | Version the ingest API; keep the producer contract small (episode id, version, reward, transcript reference) |
| D10 | Training pulls approved environments from a catalog API and datasets via signed URLs | Training integrates, as above | Version the catalog API |
| D23 | Worker-abuse detection is a separate service behind a contract (events in, findings out) | The detection service integrates. The event and finding schemas become a contract with another team | Version both schemas; put raw signals in events, not derived ones |
| D9 | Task types and environment configs are JSON schemas stored as data | Many projects exist. Every stored task follows the schema it was created under | Store the schema version with each task and config |

## Two-way

| ID | Decision | What changing it would cost |
|---|---|---|
| F2 / A24 | Vendors have capabilities (human work, environments, or both), set by data ops | A setting and its admin screen |
| F3 / A23 | Training is a separate system; a script stands in for it until a real integration exists | Replacing the stand-in with a real integration (see D5, D10 for the contract) |
| PD4 / D16 | Low quality opens no case; abuse opens a case; data is held automatically, people are acted on only by human decision | Policy and workflow code. The holds themselves are reversible by design. Individual actions against people aren't, which is exactly why they need a human |
| D1 | The control plane is one modular service | Splitting a module into its own service. Cheap while module boundaries stay clean, expensive if they blur |
| D2 | The job queue is a Postgres table | Moving to a dedicated broker plus an outbox. The jobs themselves are idempotent, so no data is lost |
| D4 | Validation runs in fair-share sandboxes | Swapping the sandbox technology or the fairness rule |
| D7 | Detectors raise flags through the API | Rewiring one path |
| D13 | One flag → case → action model for both levels | Splitting the case model if the two levels diverge |
| D17 | Gold tasks routed to every vendor; regular tasks to one vendor by share | Changing the routing rule; routes for started work stay as they are |
| D19 | Scorecard drop alerts need size (8 points), significance (two-proportion z ≥ 2) and enough gold on both sides (60) | Tuning the thresholds |
| D20 | Abuse rules look only at answers after the last decision on a worker | Changing the window rule |
| D21 | Clean-up removes all of a worker's answers | Adding a narrower scope (for example, since the first flagged answer) |
| D22 | Removed answers stay in scorecard history as delivered, never accepted | Changing the measure, with a new metric version (N19) |
| D24 | Every submission publishes an event through an outbox; the detection service samples its costly checks by risk | Changing what's sampled, inside the service |
| D25 | Exports include only submissions detection has processed and that aren't held | Dropping or loosening the gate, at the cost of exporting unchecked data |
| D27 | New rules start in shadow mode | Changing the bar for going live |
| D28 | A random share of unflagged work is reviewed, and outcomes return to detection as labels | Changing the sample rate |
| | Three planes (control, execution, ingestion); Postgres, an analytics store and object storage | Replacing a store means a data migration, which is costly but loses nothing |
| | Web app in React and TypeScript | A rewrite of the front end; the API (VM5) is unaffected |
| B1 | The first build covers multi-vendor human data (flows 2, 4, 5), not the environment pipeline ([`BUILD.md`](BUILD.md)) | The environment slice can follow; nothing in slice A blocks it |
| B2 | Build stack: FastAPI (async Python), Postgres, React + TypeScript, Docker Compose | Rewriting the backend in another language; the API contract and data model carry over |
| B3 | Delivered as a runnable repo plus a recorded walkthrough, not a hosted demo | Hosting can be added later without code changes |

## What this means for the first build

Whatever the build's scope, it should include these from the start, even in a minimal form, because they can't be added later for data already collected:

1. Immutable environment versions (D3)
2. Lineage on every submission and episode (D14)
3. An append-only audit log (VM4)
4. Gold tasks kept secret and rotatable (D8, PD2)
5. Raw cost inputs: contract price and unit, time on task (PD3, DC9)
6. Opaque, vendor-scoped worker IDs (D12)
7. Metric and rule versions recorded with every score and flag (D11, PD5)
8. Client and API signals recorded with every submission (AB11, AB12)
