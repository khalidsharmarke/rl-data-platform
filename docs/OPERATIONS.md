# Operations runbook

For Anthropic's data ops and researchers: how to run the platform day to day. Each section says what to look at, what to do and who decides. Vendors have their own guide, [`VENDOR-GUIDE.md`](VENDOR-GUIDE.md); why things work this way is in [`DESIGN.md`](DESIGN.md).

## 1. Onboarding a vendor

**Who:** data ops.

1. **Onboarding → Create and invite.** Give the vendor's name, its admin's name, and what it supplies (human work, environments, or both). You get a link to pass on: it signs the admin in once and expires after seven days. The platform doesn't email it. If it expires, invite again (`POST /orgs/{id}/invites`).
2. **Wait for the checklist.** The Onboarding table shows each new vendor's progress, its next item, and the time from invite to first accepted work and to ready. The vendor works through the samples itself, from the app or its own CI; see the vendor guide.
3. **Set up a contract** for the vendor: `POST /orgs/{vendor}/contracts` with the kind of work, price, unit and effective dates.
4. **Allocate real work** once the checklist is complete: `POST /projects/{project}/allocations` with the vendor, contract and share. Allocation to a vendor that hasn't finished is refused with "hasn't finished onboarding yet". (Contracts and allocations have no screens in this build; they're API calls, and both show on Projects.)

**If a vendor is stuck,** its checklist line says why: no worker added, sample answers below the bar (another worker can try), a sample environment failing a named check, or no image in its registry namespace. The samples count nowhere, so a vendor can retry as often as it needs.

Vendors that predate self-serve onboarding are marked onboarded and don't appear in the table.

## 2. API tokens

**Who:** vendor admins create and revoke their own; data ops can revoke any.

**A token has leaked:**

1. **API tokens**: find it by vendor, name and the characters it starts with. **Revoke.** It stops working at once for the API; registry tokens already issued from it last at most five minutes.
2. **What it did** lists every action recorded under it (or `GET /audit?token_id=`). Environment versions it created are attributed to it (`created_by_token`).
3. Quarantine anything it submitted that you don't trust, through an environment case (section 4).
4. Ask the vendor to create a replacement and move its tools over.

**Rotation:** tokens expire after 90 days. Vendors rotate by creating a new token before revoking the old one. The code task generator's token comes from the `GENERATOR_TOKEN` setting; changing it and restarting the API revokes the old one.

## 3. The registry

Each vendor has a namespace, `vendors/<vendor>/`, signed in to with its own API tokens (registry scope). The sandbox publishes the base image as `platform/base:1` when it starts. When a version names a vendor image, the platform copies it into `platform/images` by digest, and only that copy is run, so a vendor can't change what was checked.

Not done: image scanning and size quotas. Pushes and pulls are in the registry's own log, not the audit log.

## 4. Environments

**Who:** researchers write briefs and review; data ops allocates and runs cases.

- **Review queue (Review):** versions that passed the sandbox's checks. Grade each against the brief's rubric; approve, or send it back with feedback the builder sees. Approved versions go to the catalog training pulls from.
- **Quarantine:** if something is wrong with an approved version (a copied benchmark, a hole in the grader), data ops opens an environment case from the version. It leaves the catalog at once, and the case lists the runs that pulled it. Decide: clear (back to review) or remove.
- **Hack detection:** the reward-hack detector reads training's episodes. A watch finding queues transcripts for a researcher to read (Review); a case finding quarantines the version without waiting for anyone. Either way the case issues a recall naming each run, the step it first used the version and where flagged attempts began; training reports its rollback on the case. The platform never touches a model.

## 5. The code task generator

**Who:** researchers write the brief and review samples; data ops allocates.

1. Write an environment brief and allocate it to **Code task generator**, under its unpaid environments contract (section 1, step 4). The generator fills it with tasks from its families until the brief's target is reached, a batch at a time.
2. **Review the sample.** When a batch is complete, the platform draws a random sample (the square root of the versions that passed the checks, at least three) into the review queue, marked with its batch. Approving every sampled version approves the batch. Sending one back sends the batch back and stops its family.
3. **Stopped families** show on the brief, with why. The generator also stops a family itself after two versions in a row fail the checks; its probes usually say why. Fix the template in `generator/families.py` (its tests are `generator/test_families.py`), then **Template fixed: resume** on the brief.
4. **Calibrate.** Once training has used a family's tasks, the brief shows each family's intended difficulty beside the difficulty measured from training's solve rates. A family whose "hard" tasks come out easy needs a harder template.

Adding a family: write a function in `families.py` that returns the task, grader, reference and probes, add it to `FAMILIES`, and make its tests pass. Every task needs probes for the shortcuts the family invites.

## 6. Abuse and data integrity

| Situation | Look at | Who decides | What to do |
|---|---|---|---|
| A worker case opens | Cases | The vendor's admin | Nothing, unless asked. The worker's answers are already held back from training. The admin clears, warns, suspends or removes |
| A worker was removed | Cases → the case: lineage | Data ops | Clean up: one action removes the worker's answers, reopens their tasks for others, and lists the datasets that held them |
| A new detection rule | Detection | Data ops | New rule versions run in shadow: they flag but nothing happens. Promote to live once at least 20 of its flags have been reviewed and its precision is at the bar (80% unless you set one) |
| Workers flag a broken task | Projects → Task health | A researcher | Two flags pause the task. Restore it, or retire it so its answers count nowhere |
| An agreement alert | Projects → Task health | A researcher | A batch where the same workers agree much less than elsewhere. Look for broken tasks in it |
| A vendor case opens | Cases | Data ops | Read the evidence, then ask the vendor to respond. Act only after its response or three days without one: lower its allocation, pause it, or offboard it. Or clear the case |
| A dataset is needed | Projects → Release, then export | A researcher | Exports hold only released answers, each tagged verified or provisional, with lineage |

Spot checks (Review) ask two questions of every answer: accept or reject, and does it look like abuse. Answer both: the second is how detection's precision and misses are measured, and a random audit sample is mixed in so reviewers can't tell audits apart.
