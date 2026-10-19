#!/bin/bash
# Records docs/media/walkthrough.mp4 by driving the web app with agent-browser and captioning each
# scene. Run it right after `DEMO_AUTOPILOT=false make demo` has finished, with agent-browser and an
# ffmpeg that has libx264 on PATH. Long waits are cut out, and still stretches are trimmed after.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT=$REPO/docs/media/walkthrough.mp4
export AGENT_BROWSER_SESSION=rl-rec
ab() { agent-browser "$@" >/dev/null 2>&1; }
ref() { agent-browser snapshot -i | grep "$1" | head -1 | grep -o 'ref=e[0-9]*' | cut -d= -f2; }
click() { ab click "@$(ref "$1")"; }
pause() { ab wait "$1"; }
caption() {
  local js
  js=$(python3 -c 'import json,sys; t=json.dumps(sys.argv[1]); print("(()=>{let c=document.getElementById(\"cap\");if(!c){c=document.createElement(\"div\");c.id=\"cap\";c.style.cssText=\"position:fixed;left:50%;bottom:24px;transform:translateX(-50%);max-width:880px;background:rgba(20,20,19,.92);color:#fff;padding:12px 18px;border-radius:10px;font:15px/1.45 system-ui,sans-serif;z-index:99999;pointer-events:none;box-shadow:0 4px 16px rgba(0,0,0,.25)\";document.body.appendChild(c)}c.textContent="+t+";return 1})()")' "$1")
  agent-browser eval "$js" >/dev/null 2>&1
}
signin() {
  ab eval "sessionStorage.clear()"; ab open http://localhost:5173; ab wait --text "$1"; click "\"$1"; ab wait --text "Sign out"
}
scroll_to() { ab eval "(()=>{document.querySelectorAll('details.grades').forEach(d=>d.open=true);const e=[...document.querySelectorAll('h2,h3,h4,figcaption,summary')].find(x=>x.textContent.includes('$1'));if(e)e.scrollIntoView({behavior:'smooth',block:'start'});return 1})()"; }
# Polls a JS condition for up to $2 seconds (agent-browser's own wait stops at 25).
until_js() { for _ in $(seq "$2"); do [ "$(agent-browser eval "$1" 2>/dev/null)" = "true" ] && return 0; sleep 1; done; echo "gave up: $1" >&2; }
# Long waits happen off camera: stop recording, wait, then start the next part. The parts are joined at the end.
PARTS=$(mktemp -d); SEG=0
rec_on() { SEG=$((SEG+1)); agent-browser record start "$PARTS/part$(printf %02d $SEG).mp4" >/dev/null 2>&1; }
rec_off() { agent-browser record stop >/dev/null 2>&1; }
wait_off() { if [ "$(agent-browser eval "$1" 2>/dev/null)" != "true" ]; then rec_off; until_js "$1" "$2"; rec_on; fi; }
row() { ab eval "(()=>{const r=[...document.querySelectorAll('tr.clickable')].find(t=>t.textContent.includes('$1'));r.click();return 1})()"; }
case_row() { ab eval "(()=>{const r=[...document.querySelectorAll('tr.clickable')].find(t=>[...t.cells].some(c=>c.textContent.trim()==='$1'));r.click();return 1})()"; }
approve() {
  row "$1"; ab wait --text "Grade against"; pause 1000
  ab eval "(()=>{document.querySelector('.review-form').scrollIntoView({behavior:'smooth',block:'center'});document.querySelectorAll('.review-form input[type=checkbox]').forEach(c=>c.click());return 1})()"; pause 1000
  ab fill "@$(ref 'Feedback for the builder')" "$2"; pause 600
  click '"Approve"'; pause 2500
}
choose() { ab eval "(()=>{const s=[...document.querySelectorAll('select')].find(x=>[...x.options].some(o=>o.value==='$1'));s.value='$1';s.dispatchEvent(new Event('change',{bubbles:true}));return 1})()"; }

ab close
ab set viewport 1280 800
ab open http://localhost:5173
ab wait --text "Sam Lindqvist"
rec_on

caption "An Anthropic-run platform where vendors supply training data. Three vendors share one project; 30 simulated workers gave about 6,900 answers. One Brightline worker began pasting AI-written answers part-way through."
pause 7000

signin "Sam Lindqvist"
click '"Scorecards"'; ab wait --text "Cost per accepted"; pause 1500
caption "Data ops compares vendors on identical, platform-computed measures. Brightline is cheapest per task but the most expensive per accepted answer."
pause 6500
caption "Northwind reviews its own workers' answers before they reach Anthropic. It's scored on what it submitted; its workers on what they answered, so a vendor's review can't hide their mistakes."
pause 7000
scroll_to "Gold accuracy (rolling hour)"; pause 1200
ab mouse move 660 420; pause 400; ab mouse move 760 430
caption "Brightline's gold accuracy (test questions hidden among real tasks) falls after its worker starts pasting."
pause 5500

signin "Hana Kowalski"
ab wait --text "Review before submitting"; pause 800
caption "Northwind's reviewer accepts each answer or corrects it. A correction is kept beside the worker's own answer; nothing marks which tasks are tests."
pause 6000
click '"Accept"'; pause 2500

signin "Sam Lindqvist"
click '"Projects"'; ab wait --text "Worker grades"; pause 800
scroll_to "Worker grades"; pause 1200
caption "Every worker is graded twice: quality (accuracy, with how sure we are) and integrity. An honest but sloppy worker is trusted, with low quality: not treated as abusive."
pause 6500
scroll_to "Escalations"; pause 1200
caption "A careful Northwind worker pastes reasons from their own notes: one signal alone. The platform quietly gave them more test questions, and their answers cleared them. Northwind never saw a case."
pause 7500
click '"Detection"'; ab wait --text "What the live rules miss"; pause 1000
caption "Detection is measured too. Every spot check also asks whether an answer looks like abuse, and a random share of answers is mixed in so reviewers can't tell audits apart."
pause 7000
caption "A new rule that flags very short reasons runs in shadow: it flagged honest workers, reviewers judged none of its flags abusive, and it never reached a vendor."
pause 7000

signin "Rae Okafor"
ab wait --text "Task health"; scroll_to "Task health"; pause 1200
caption "A bug made both answers identical in the last batch of tasks. Workers flagged them instead of answering; a task two workers flag stops being routed until a researcher decides."
pause 7000
ab fill "@$(ref 'Reason (required, kept on record)')" "Both answers are identical."
pause 600
click '"Retire"'; pause 2000
caption "Retired, the task counts nowhere: not in volume, accuracy, agreement or datasets."
pause 4000

signin "Grace Adeyemi"
click '"Cases"'; ab wait --text "Open"; pause 1000
caption "Brightline's worker raised three agreeing signals, so the platform opened a case and held her answers. Acting on the worker is the vendor admin's call."
pause 6000
ab eval "document.querySelector('tr.clickable').click(); 1"; ab wait --text "What the platform observed"; pause 800
caption "She sees what was observed and examples: never the rules' thresholds, never which tasks were tests."
pause 5500
scroll_to "History"; pause 1500
choose suspend
click 'Reason (required'; ab type "@$(ref 'Reason (required')" "Pasted the same generic reason on every task, faster than anyone could read it."
pause 800
caption "She suspends the worker, with a reason that stays on record."
pause 2500
click '"Decide"'; ab wait --text "by You"; pause 3000

signin "Sam Lindqvist"
click '"Cases"'; ab wait --text "Brightline"; pause 600
ab eval "(()=>{const r=[...document.querySelectorAll('tr.clickable')].find(t=>t.textContent.includes('Brightline')&&t.textContent.includes('signals'));r.click();return 1})()"
ab wait --text "Lineage"; pause 600
scroll_to "Lineage"; pause 1200
caption "Lineage shows where her answers went. None of her pasted answers reached a dataset: the release stage held them back, because the test questions she answered after them were failing."
pause 7000
click '"Remove answers and re-label'; ab wait --text "removed,"; pause 1200
caption "One click removes every answer she gave and reopens those tasks for other workers. Exports contain only released answers, each tagged verified or provisional."
pause 6000
click '"Workspaces"'; ab wait --text "Recent audit events"; pause 600
scroll_to "Recent audit events"; pause 800
caption "The audit log holds the whole chain: case opened, data held, suspension, clean-up, export. It can't be edited, even by direct SQL."
pause 6000

caption "Act two: environments. A researcher wrote a brief for Python bug-fix environments, and data ops gave it to Kestrel, which builds environments."
pause 6000
signin "Sofia Lindgren"
ab wait --text "Python bug fixes"; pause 800
scroll_to "Environments"; pause 800
row "slugify exists"; pause 1500
ab eval "(()=>{const d=document.querySelectorAll('.checks details')[1]; if(d) d.open=true; document.querySelector('.env-detail').scrollIntoView({behavior:'smooth'}); return 1})()"; pause 1200
caption "Every version is checked in a sandbox: fresh containers, no network, the grader kept out of the attempt. This grader passed the untouched task files: it would reward an empty answer."
pause 7500

signin "Rae Okafor"
click '"Review"'; ab wait --text "Environments to review"; pause 800
caption "A sound environment passed all three checks. The researcher grades it against the brief's rubric; approved, training pulls it, never the reference solution."
pause 5000
approve "empty strings" "Clear task; the tests check exactly the empty-string fix."

signin "Sam Lindqvist"
ab wait --text "Python bug fixes"; pause 600
scroll_to "Environments"; pause 600
row "empty strings"; pause 1500
ab eval "(()=>{const f=document.querySelector('.review-form');if(f)f.scrollIntoView({behavior:'smooth',block:'center'});return 1})()"; pause 1200
caption "Later, a researcher spots that the task copies a public benchmark. Data ops flags the version: quarantined, out of the catalog at once, with the training run that pulled it on record."
pause 5000
ab fill "@$(ref 'wrong with it')" "Copies a public benchmark problem word for word, against the brief's constraints."
pause 800
click '"Open a case and quarantine"'; pause 2500

caption "Act three: training. Kestrel submitted two more, and both passed all three checks. But one grader tests only the empty string: a function that returns \"\" scores full marks."
pause 7000
signin "Rae Okafor"
click '"Review"'; ab wait --text "Environments to review"; pause 800
approve "drops punctuation" "Clear task, and the tests check the fix the README asks for."
approve "edge cases" "Clear task, and the tests check the fix the README asks for."
caption "The researcher approves both. Nothing in a review shows the hole; it takes a model attempting the task."
pause 4500
click '"Training"'; ab wait --text "Training"
wait_off "document.body.textContent.includes('slugify edge cases')" 300
ab eval "document.querySelector('.version-tabs button').click(); 1"; pause 1500
caption "Training pulls both and starts a run. Every episode comes back with its run, step, reward, length and transcript; this dashboard refreshes every few seconds. Training here is a stand-in with a modeled policy."
pause 9000
wait_off "(document.querySelector('section p.muted')?.textContent.match(/up to step ([\\d,]+)/)?.[1]?.replace(/,/g,'') ?? 0) >= 2600" 90
caption "Partway through, the policy finds the shortcut. On 'slugify edge cases' the solve rate climbs while attempts shrink from about 40 turns to 4. The sound environment climbs slowly."
pause 7000
scroll_to "Attempt length"; pause 4000
row "edge cases"; pause 1500
ab eval "(()=>{const e=document.querySelector('.env-detail');e.scrollIntoView({behavior:'smooth',block:'start'});const r=[...e.querySelectorAll('tr.clickable')].find(t=>t.cells[1].textContent==='1'&&+t.cells[2].textContent<=6);(r||e.querySelector('tr.clickable')).click();return 1})()"; pause 1500
ab eval "(()=>{const v=document.querySelector('.env-detail .version');if(v)v.scrollIntoView({behavior:'smooth',block:'center'});return 1})()"
caption "One of those episodes: the model edits the task to return an empty string, and the grader passes it."
pause 6000
wait_off "[...document.querySelectorAll('tr.clickable')].some(t=>t.textContent.includes('edge cases')&&t.textContent.includes('uarantined'))" 90
ab eval "window.scrollTo({top:0,behavior:'smooth'}); 1"; pause 1500
caption "A separate hack detector reads the same episodes. Half the latest attempts scored full marks in a fraction of the usual length, so it opened a case: the version is quarantined, with no person involved."
pause 8000

signin "Sam Lindqvist"
click '"Cases"'; ab wait --text "hack detector"; pause 800
row "edge cases"; ab wait --text "Recall to training"; pause 800
scroll_to "Recall to training"; pause 1000
wait_off "document.body.textContent.includes('Rolled back')" 60
caption "The case issues a recall to training: the run, the step it first used the version, and where flagged attempts began. Training rolled back to the checkpoint before them and reported it."
pause 8000
choose remove
ab fill "@$(ref 'Reason (required, kept on record)')" "The flagged transcripts return an empty string and score full marks."
pause 600
click '"Decide"'; pause 2000
# Act six's transcripts come from this quarantined environment: start that act now, off camera.
(cd "$REPO" && docker compose -f docker-compose.yml -f docker-compose.demo.yml run -d --rm simulator python scenario.py transcripts >/dev/null 2>&1)

wait_off "[...document.querySelectorAll('tr.clickable')].some(t=>t.cells[2]&&t.cells[2].textContent.trim()==='Kestrel Labs')" 60  # data ops: Subject column
case_row "Kestrel Labs"; ab wait --text "Vendor: Kestrel"; pause 1000
caption "A vendor rule notices: two of Kestrel's environments quarantined in 30 days. The case stays inside Anthropic until data ops has read it and asks Kestrel to respond."
pause 7000
click '"Ask Kestrel Labs'; pause 2000

signin "Jonah Weiss"
click '"Cases"'; ab wait --text "Kestrel Labs"; pause 600
case_row "Kestrel Labs"; ab wait --text "Vendor: Kestrel"; pause 800
caption "Kestrel sees what was observed, never the rule, and answers."
pause 3500
ab fill "@$(ref 'Your response to Anthropic')" "We've retired both and now have a second builder review every grader before we submit."
pause 600
click '"Respond"'; pause 2000

signin "Sam Lindqvist"
click '"Cases"'; ab wait --text "Kestrel Labs"; pause 600
case_row "Kestrel Labs"; ab wait --text "You can act on the vendor now"; pause 800
choose pause
ab fill "@$(ref 'Reason (required, the vendor sees it)')" "Paused on this brief until the second grader review shows in a new batch."
pause 600
caption "Only after Kestrel's response, or three days without one, can data ops act: here, pausing Kestrel on the brief."
pause 3500
click '"Act"'; pause 2000
choose resolve
ab fill "@$(ref 'Reason (required, kept on record)')" "Paused on the brief."
click '"Decide"'; pause 2000

click '"Scorecards"'; ab wait --text "Environment work"; pause 800
scroll_to "Environment work"; pause 1200
caption "Kestrel's scorecard shows its environment work, from submissions to quarantines, and what happened in training: episodes, solve rate, attempts flagged as hacks, and recalls."
pause 7500
scroll_to "How vendors handle"; pause 1200
caption "And how each vendor handles its own workers' cases: most cases cleared, workers reinstated or cases left open are signals about the vendor."
pause 6500


caption "Act four: a vendor onboards itself. Data ops created Harbor Labs and passed its admin a single-use invite link; Harbor did the rest."
pause 5500
click '"Onboarding"'; ab wait --text "To ready"; pause 1000
caption "Harbor's checklist completed itself from what the platform saw happen, never from what Harbor reported. Data ops sees how long it took, and only now can allocate it real work."
pause 7000

signin "Ines Duarte"
click '"Onboarding"'; ab wait --text "From your own terminal"; pause 1000
caption "Harbor's admin made one API token for its CI. With it, the CI pushed a runtime image to Harbor's own registry namespace and submitted a sample environment. The first sample failed the empty check; the fix passed."
pause 8000
scroll_to "From your own terminal"; pause 1200
caption "Every step is shown with Harbor's own values, so a vendor can do all of it from its own terminal or CI, which can also wait on this checklist."
pause 6500
click '"API tokens"'; ab wait --text "harbor-ci"; pause 800
click '"What it did"'; ab wait --text "Everything harbor-ci did"; pause 600
scroll_to "Everything harbor-ci did"; pause 1200
caption "Everything the token did is recorded under its name. It acts for Harbor, never for a person, and data ops can revoke it if it leaks."
pause 6500
click '"Guide"'; ab wait --text "Vendor guide"; pause 800
scroll_to "4. The registry"; pause 1200
caption "The vendor guide, with Harbor's values filled in. It's the same document as docs/VENDOR-GUIDE.md in the repo."
pause 6000

signin "Sam Lindqvist"
click '"Cases"'; ab wait --text "Harbor Labs"; pause 600
wait_off "[...document.querySelectorAll('tr.clickable')].some(t=>[...t.cells].some(c=>c.textContent.trim()==='Harbor Labs'))" 60
case_row "Harbor Labs"; ab wait --text "The same person at another vendor"; pause 800
scroll_to "The same person at another vendor"; pause 1200
caption "Harbor's admin also added a worker who, by the keyed hash every vendor submits, is the person Brightline suspended. The platform never learns who she is, and Harbor isn't told which vendor matched. A vendor rule tells data ops; the match itself changes nothing until a person decides."
pause 9000

caption "Act five: generated tasks. A researcher's brief went to the code task generator, a separate service that signs in with its own token, like any vendor's tooling."
pause 6500
signin "Rae Okafor"
ab wait --text "Generated Python bug fixes"; scroll_to "Generated tasks"; pause 1500
caption "Every generated task carries probes: known-wrong answers that must fail its grader. One family's tests only checked the README's example, so a probe that hard-codes it passed. The sandbox failed those versions and the family stopped."
pause 9000
click '"Review"'; ab wait --text "Sample from"; pause 600
scroll_to "Environments to review"; pause 1200
caption "Researchers review a random sample of each batch, not every task: three of the seven that passed the checks."
pause 5000
for _ in 1 2 3; do approve "Sample from" "Statement and tests agree; the probes cover the obvious shortcuts."; done
caption "Approving the whole sample approved the batch into the catalog. Sending one back would have sent the batch back and stopped its family."
pause 6000
click '"Projects"'; ab wait --text "Intended against measured"; scroll_to "Intended against measured"; pause 1000
caption "Training pulls the approved batch and starts a run. This table fills in as its episodes arrive."
wait_off "[...document.querySelectorAll('tr')].some(r=>r.cells[0]?.textContent==='dedupe'&&r.cells[1]?.textContent==='hard'&&r.cells[6]?.textContent!=='not yet')" 300
scroll_to "Intended against measured"; pause 1200
caption "Once training used them, each family shows the difficulty it intended beside the one training measured. clamp matches its labels; dedupe is easier than intended at every level, so its template is the one to make harder."
pause 9000

caption "Act six: people check what training rewarded. A researcher turned the gamed environment's episodes into transcripts for two of Northwind's workers to review."
pause 6500
signin "Rae Okafor"
rec_off
for _ in $(seq 60); do  # the page lists projects when it opens, so reopen it until the transcripts project exists
  click '"Review"'; pause 1500
  [ "$(agent-browser eval "[...document.querySelectorAll('select option')].some(o=>o.textContent.startsWith('Agent transcripts'))" 2>/dev/null)" = "true" ] && break
  click '"Projects"'; sleep 4
done
rec_on
ab select "@$(agent-browser snapshot -i | grep 'combobox \"Project\"' | head -1 | grep -o 'ref=e[0-9]*' | cut -d= -f2)" "Agent transcripts: slugify edge cases"; pause 2000
until_js "document.querySelector('select')?.selectedOptions[0]?.textContent.startsWith('Agent transcripts') && !!document.querySelector('.steps')" 30
ab eval "(()=>{const c=[...document.querySelectorAll('.card.task')].find(x=>x.querySelector('li.wrong')&&x.textContent.includes('return \"\"'))||document.querySelector('.card.task');window.scrollTo({top:c.getBoundingClientRect().top+window.scrollY-90,behavior:'smooth'});return 1})()"; pause 1500
caption "Each task is one attempt, step by step, with what the grader said. The reviewer marks the first step that goes wrong and judges the outcome: here the edit that makes the function return an empty string, which the grader passed."
pause 9000
click '"Scorecards"'; ab wait --text "Cost per accepted"; pause 800
ab select "@$(agent-browser snapshot -i | grep 'combobox \"Project\"' | head -1 | grep -o 'ref=e[0-9]*' | cut -d= -f2)" "Agent transcripts: slugify edge cases"; pause 2500
until_js "document.querySelector('select')?.selectedOptions[0]?.textContent.startsWith('Agent transcripts')" 20
caption "One reviewer reads every step; the other trusts the grader. Gold scored on the step and the outcome tells them apart, and their agreement drops exactly where the grader passed a hack."
pause 8000

rec_off
# Join the parts into the walkthrough.
for f in "$PARTS"/part*.mp4; do echo "file '$f'"; done > "$PARTS/list.txt"
ffmpeg -v error -y -f concat -safe 0 -i "$PARTS/list.txt" -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p -an "$PARTS/joined.mp4"
# The browser tool's own overhead adds still frames after captions: keep at most 8.5 s of any still stretch.
python3 "$(dirname "$0")/trim_freezes.py" "$PARTS/joined.mp4" "$OUT" 8.5
ab close
