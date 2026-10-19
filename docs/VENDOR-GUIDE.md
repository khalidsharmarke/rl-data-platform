# Vendor guide

How to work with the platform as a vendor: getting set up, tokens and the registry, submitting environments, what the checks mean, and how cases and scorecards look from your side.

<!-- repo -->
The same guide is in the app, under Guide, with your own values filled in: wherever this page says `<vendor>`, `<namespace>`, `<registry>` or `<api>`, the app shows yours. With the local build, `<registry>` is `localhost:5000` and `<api>` is `http://localhost:8000`.
<!-- /repo -->

## 1. Getting started

Anthropic's data ops creates your workspace and sends your admin a link. The link signs your admin in once and expires after seven days; if it has expired, ask data ops for a new one.

Your admin then sees the **Onboarding** checklist. Every item ticks itself when the platform sees it happen; nothing is self-reported. What's on it depends on what you were set up to supply:

| You supply | The checklist asks for |
|---|---|
| Human work | Your admin signed in; a worker added; one worker answers the 5 sample tasks, at least 4 of them correctly |
| Environments | Your admin signed in; an API token created and used; an image pushed to `<registry>/<namespace>/`; a sample environment, submitted with a token and naming your image, that passes the sandbox's checks |

Samples run on the real path but count nowhere: not on your scorecard, in a dataset, in review or in training. Real work can be allocated to you only once the checklist is complete. If an item is stuck, its line says why, for example which check your sample failed.

Your CI can wait for the checklist instead of anyone watching the page:

```sh
curl -sO <api>/tools/rlp.py
export RLP_TOKEN=<your token>
python3 rlp.py onboarding --wait
```

## 2. People

Your admin adds people under **Workspaces**, each with one role:

| Role | Does |
|---|---|
| Admin | Manages people and API tokens, decides cases about your workers, responds to cases about your vendor |
| Reviewer | When Anthropic switches vendor review on for a project, checks each of your workers' answers before it reaches Anthropic, accepting or correcting it |
| Worker | Answers tasks |
| Environment builder | Builds and submits environments against your briefs |

Anthropic sees your workers by handle only. You keep their identities.

**The cross-vendor match.** So that a person suspended at one vendor can't simply rejoin through another, every vendor sends, for each worker, a keyed hash of their email address: HMAC-SHA256 with the key from `GET /match-key`, of the address trimmed and lowercased, as 64 hex digits, to `PUT /orgs/<your org>/members/<worker>/identity`. The platform stores only the hash, so it can't recover who anyone is. A match is evidence for a person at Anthropic to weigh, never an action on its own, and you're never told which vendor a match involves.

## 3. API tokens

Tokens are for your organization's tools: a CI that submits environments, a script that reads your scorecard. A token acts for your vendor, never for a person, and everything it does is recorded under its name in your audit log.

Your admin creates tokens under **API tokens**, with one or more scopes:

| Scope | Lets the token |
|---|---|
| `environments` | Create environments, submit versions to your briefs, and read your briefs, versions and their checks |
| `read` | Read your scorecards, cases, briefs, versions and onboarding checklist |
| `registry` | `docker login` to `<registry>`, then push and pull your own images |

No token can answer tasks, review answers, decide cases, add people or create tokens: those are for people.

- **Shown once.** The token is displayed when it's created and never again; the platform stores only a hash. The list shows its first characters, so you can tell which one leaked.
- **Expires after 90 days.** To rotate one, create a new token, move your tools to it, then revoke the old one. Both work in between.
- **Revoke at once if it leaks.** Revoking stops it everywhere; registry access ends within five minutes. "What it did" lists every action it took.

## 4. The registry

Bring your own runtime when your environments need packages the platform's base image doesn't have.

```sh
echo "$RLP_TOKEN" | docker login <registry> -u ci --password-stdin
docker build -t <registry>/<namespace>/runtime:1 .
docker push <registry>/<namespace>/runtime:1
```

Your token can push only under `<namespace>/`, and can pull the platform's base image, `<registry>/platform/base:1` (Python 3.12 with pytest). Start your Dockerfile `FROM <registry>/platform/base:1`, or from any image that meets the rules below.

**An image is the runtime only.** Task files, grader and reference solution are never in the image; they're uploaded with each version and kept apart, so the model can never read its own answer key. Your image must:

- have `sh` and `tar`, and whatever your test command runs;
- work as an unprivileged user (uid 1000), with a read-only root filesystem, the task in a writable `/work`, and no network.

**A version is pinned to exact image content.** It names your image by digest; when you submit it, the platform copies the image into its own namespace, and only that copy is ever run. Retagging or deleting your image later changes nothing for versions already submitted. `rlp.py` turns a tag into its digest for you.

## 5. Submitting an environment

A version is a directory:

```
my-environment/
  manifest.json        {"test_command": "...", "prepare_command": "..."}   (prepare_command is optional)
  task/                what the model works in: the task statement and the code to change
  grader/              the tests that score an attempt; never shown to the model
  reference/           your known-good solution: files laid over task/ at the same paths
  probe/<name>/        optional known-wrong answers, laid over task/ the same way
```

Limits: 256 KB per file, 2 MB and 200 files per version. Every change is a new version; versions never change once submitted.

```sh
python3 rlp.py submit ./my-environment --brief "<brief name>" --name my-environment \
    --image <registry>/<namespace>/runtime:1
```

It creates the environment if it's new, uploads a version, submits it, waits for the checks and prints them. Leave out `--image` to run on the base image. Without `rlp.py`, the same calls are `POST /environments`, `POST /environments/{id}/versions` and `POST /versions/{id}/submit`, then `GET /versions/{id}/checks`.

## 6. The checks

Every submitted version is checked in fresh containers with no network, 1 CPU, 256 MB of memory and at most 128 processes. Each check has two steps: the attempt (only the task files, with your `prepare_command` if any, 30 s) and grading (the attempt's task files plus the grader, your `test_command`, 30 s).

| Check | Passes when | If it fails |
|---|---|---|
| Reference | Your reference solution, laid over the task, passes the grader | The task can't be solved as written, or the grader expects something the reference doesn't do. Read the grading log |
| Empty | The untouched task files fail the grader | The grader rewards doing nothing. Test the behavior the task asks for |
| Repeat | The grader gives the reference the same result ten times running (120 s in all) | A test depends on randomness, time or order. Make it deterministic |
| Probes | Every probe fails the grader (only run when the version has probes) | The grader rewards that shortcut. Test the cases the probe gets wrong |

Probes are how you prove a grader isn't easy to game: a probe that returns its input unchanged, or hard-codes the example in the README, must fail. A grader that only checks the README's example passes the other three checks and is gamed in training; a probe finds it first.

A version that passes goes to an Anthropic researcher, who reviews it against the brief's rubric and approves it or sends it back with feedback. A version sent back or failing a check is final: fix it in a new version. You see the diff from your previous version.

## 7. After approval

Approved versions go to a catalog that training pulls from. Training gets the task files and the grader, never your reference solution or probes.

If a problem is found later, by a researcher or by Anthropic's reward-hack detection reading training's results, the version is quarantined: it leaves the catalog at once, and an environment case is opened. Your scorecard shows how your versions did in training: episodes, solve rate, attempts flagged as possible hacks, and recalls.

## 8. Cases and scorecards

**Your scorecard** is computed by the platform, the same way for every vendor: volume, acceptance, gold accuracy, agreement, cost per accepted answer and turnaround for human work, and checks, review, quarantines and training outcomes for environments. Gold-based numbers are shown up to the last daily refresh, and you never see which tasks were gold.

**Cases about your workers.** When detection finds abuse signals that agree, the worker's answers are held back from training and a case opens for your admin. It shows what was observed, with examples, but never the rules' thresholds. Your admin decides: clear, warn, suspend or remove, with a reason on record.

**Your own rules.** Under Cases, your admin can add rules of your own: a threshold on a signal the platform already records (median time on task, share of answers pasted, focus changes per answer, time to first input) over a worker's latest answers. A worker who crosses one gets a case only you see. It holds nothing, and it never counts toward your scorecard or Anthropic's rules.

**Cases about your vendor.** Some patterns are only visible across a vendor, for example quality falling while volume rises, or several environment versions quarantined in a month. Anthropic's data ops may ask you to respond to such a case. Nothing is done to your allocation before your response, or before three days pass without one.
