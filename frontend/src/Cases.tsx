import { useEffect, useState } from "react";
import { summary } from "./TaskView";
import { api, type CaseDetail, type CaseSummary, type VendorRule, type Lineage, type Recall, type User } from "./api";

const DECISIONS = [
  { value: "suspend", label: "Suspend worker" },
  { value: "warn", label: "Warn and release hold" },
  { value: "clear", label: "Clear: no abuse" },
  { value: "remove", label: "Remove worker" },
];

// An environment case (TO6): data ops keeps the version quarantined, or sends it back to review.
const ENVIRONMENT_DECISIONS = [
  { value: "remove", label: "Keep quarantined: retire this version" },
  { value: "clear", label: "Clear: send it back to review" },
];

// A vendor case (AB5, D42): resolved after an action on the vendor, or cleared.
const VENDOR_DECISIONS = [
  { value: "resolve", label: "Resolve: action taken" },
  { value: "clear", label: "Clear: nothing to act on" },
];
const VENDOR_ACTIONS = [
  { value: "lower_allocation", label: "Lower its share on the project" },
  { value: "pause", label: "Pause it on every project" },
  { value: "offboard", label: "Offboard it" },
];

const OUTCOME: Record<string, string> = { suspend: "Suspended", remove: "Removed", warn: "Warned", clear: "Cleared" };
const ENVIRONMENT_OUTCOME: Record<string, string> = { remove: "Retired", clear: "Back in review" };
const VENDOR_OUTCOME: Record<string, string> = { resolve: "Resolved", clear: "Cleared" };
const isEnvironment = (c: CaseSummary) => c.subject_type === "environment_version";
const isVendor = (c: CaseSummary) => c.subject_type === "vendor";
const outcomes = (c: CaseSummary) => (isEnvironment(c) ? ENVIRONMENT_OUTCOME : isVendor(c) ? VENDOR_OUTCOME : OUTCOME);
const decisionsFor = (c: CaseSummary) => (isEnvironment(c) ? ENVIRONMENT_DECISIONS : isVendor(c) ? VENDOR_DECISIONS : DECISIONS);

const when = (t: string) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

/** How a decided case reads at a glance; the label always says it too. */
const DECISION_TONE: Record<string, string> = { clear: "good", warn: "warn", suspend: "bad", remove: "bad" };

export default function Cases({ me }: { me: User }) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [open, setOpen] = useState<CaseDetail | null>(null);
  const [decision, setDecision] = useState("suspend");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [recall, setRecall] = useState<Recall | null>(null);
  const isOwner = (c: CaseSummary) => (c.owner === "vendor_admin" ? me.role === "vendor_admin" : me.role === "data_ops");

  const load = () => api<CaseSummary[]>("/cases").then(setCases);
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  async function show(id: string) {
    setError(null);
    setReason("");
    const detail = await api<CaseDetail>(`/cases/${id}`);
    setOpen(detail);
    setDecision(decisionsFor(detail)[0].value);
    setLineage(null);
    setRecall(isEnvironment(detail) && me.org_kind === "internal" ? await api<Recall | null>(`/cases/${id}/recall`) : null);
    if (me.role === "data_ops" && detail.subject_type === "worker" && detail.subject_id && detail.project_id) {
      setLineage(await api<Lineage>(`/lineage?worker_id=${detail.subject_id}&project_id=${detail.project_id}`));
    }
  }

  async function cleanup(mode: "relabel" | "discard") {
    if (!open) return;
    try {
      await api(`/cleanups`, { method: "POST", body: JSON.stringify({ case_id: open.id, mode }) });
      await show(open.id);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  const cleaned = open?.actions.some((a) => a.action === "cleanup");

  async function decide() {
    if (!open) return;
    try {
      setOpen(await api<CaseDetail>(`/cases/${open.id}/decision`, { method: "POST", body: JSON.stringify({ decision, reason }) }));
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <>
    <section>
      <h2>Cases</h2>
      <p className="muted">
        {me.role === "data_ops"
          ? "Every case across vendors and how it was resolved. Worker cases are decided by the worker's vendor; environment and vendor cases by data ops."
          : "Workers the platform's signals flagged, whose answers are held from training until you decide, and anything Anthropic has asked your company to respond to."}
      </p>
      <table>
        <thead>
          <tr>
            <th>Opened</th>
            {me.role === "data_ops" && <th>Vendor</th>}
            <th>Subject</th>
            <th>What was observed</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {cases.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">No cases.</td>
            </tr>
          )}
          {cases.map((c) => (
            <tr key={c.id} className="clickable" onClick={() => show(c.id)}>
              <td>{when(c.opened_at)}</td>
              {me.role === "data_ops" && <td>{c.vendor_name}</td>}
              <td>
                {isEnvironment(c) || isVendor(c) ? c.subject_handle : <>{c.subject_name ?? ""} <span className="mono muted">{c.subject_handle}</span></>}
              </td>
              <td>
                {isEnvironment(c) ? (c.by_person ? "Flagged by a person" : "Flagged by the hack detector") : `${c.observations.length} signal${c.observations.length === 1 ? "" : "s"}`}
                {c.private && <span className="chip">Your own rule: only you see this</span>}
              </td>
              <td>
                {c.status === "open" ? (
                  <span className="badge warn">
                    Open{isVendor(c) ? (c.shared_at ? " · with the vendor" : " · not yet shared") : c.held ? (isEnvironment(c) ? " · quarantined" : " · data held") : ""}
                  </span>
                ) : (
                  <span className={`badge ${DECISION_TONE[c.decision ?? ""] ?? ""}`}>
                    {outcomes(c)[c.decision ?? ""] ?? c.decision}
                    {c.held && c.subject_type === "worker" ? " · data held" : ""}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {open && (
        <div className="card case">
          <h3>
            {isVendor(open) ? <>Vendor: {open.vendor_name}</> : <>
              {isEnvironment(open) ? <>Environment: {open.subject_handle}</> : <>{open.subject_name ?? "Worker"} <span className="mono muted">{open.subject_handle}</span></>} ·{" "}
              {open.vendor_name}
            </>}
          </h3>
          <h4>{isEnvironment(open) ? "Why it was flagged" : "What the platform observed"}</h4>
          <ul>
            {open.flags.map((f) => (
              <li key={f.observation}>
                {f.observation}
                {f.rule && f.rule !== "person" && <span className="mono muted"> ({f.rule_version})</span>}
              </li>
            ))}
          </ul>
          {open.environment && (
            <div className="lineage">
              <h4>Lineage</h4>
              <p>
                Version {open.environment.number} is <strong>{open.environment.status.replace("_", " ")}</strong>
                {open.environment.status === "quarantined" ? ": out of the catalog, so training can't pull it." : "."}
              </p>
              {open.environment.pulls.length > 0 ? (
                <ul>
                  {open.environment.pulls.map((p, i) => (
                    <li key={i}>
                      Pulled by {p.puller}
                      {p.run_label && <> for <span className="mono">{p.run_label}</span></>} · {when(p.pulled_at)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Training never pulled this version.</p>
              )}
            </div>
          )}
          {recall && (
            <div className="lineage">
              <h4>Recall to training</h4>
              <table>
                <thead>
                  <tr><th>Run</th><th>First used at</th><th>Episodes</th><th>Flagged attempts from</th><th>Training's report</th></tr>
                </thead>
                <tbody>
                  {recall.runs.map((r) => {
                    const report = recall.reports.find((p) => p.run_label === r.run_label);
                    return (
                      <tr key={r.run_label}>
                        <td className="mono">{r.run_label}</td>
                        <td>{r.first_step == null ? "pulled, not used" : `step ${r.first_step.toLocaleString()}`}</td>
                        <td>{r.episodes.toLocaleString()}</td>
                        <td>{r.first_flagged_step == null ? "—" : `step ${r.first_flagged_step.toLocaleString()} (${r.flagged} flagged)`}</td>
                        <td>
                          {report
                            ? report.action === "rolled_back"
                              ? `Rolled back to step ${report.checkpoint_step?.toLocaleString()}`
                              : "Kept going"
                            : <span className="muted">Waiting for training</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {open.samples.length > 0 && <h4>Examples</h4>}
          {open.samples.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>Answer and reason</th>
                <th>Time</th>
                <th>Pasted</th>
                <th title="Reported by the worker's browser: untrusted">First input · focus changes</th>
              </tr>
            </thead>
            <tbody>
              {open.samples.map((s) => (
                <tr key={s.submission_id}>
                  <td>{s.prompt}</td>
                  <td>
                    {summary(s.answer)}
                    {s.answer.rationale && <span className="muted"> · “{s.answer.rationale}”</span>}
                  </td>
                  <td>{s.time_on_task_s} s</td>
                  <td>{s.pasted ? "yes" : "no"}</td>
                  <td>
                    {s.client_signals.first_input_ms == null ? "—" : `${(s.client_signals.first_input_ms / 1000).toFixed(1)} s`} ·{" "}
                    {s.client_signals.focus_changes ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          )}
          {open.api_signals && (
            <>
              <h4>What the platform measured</h4>
              <p className="muted small">
                {open.api_signals.claims_last_hour} tasks taken in the last hour, at most {open.api_signals.most_claims_in_a_minute} in one minute;{" "}
                {open.api_signals.sessions_last_day} sign-ins and {open.api_signals.devices_last_day} browsers in the last day
                {open.api_signals.other_workers_on_those_devices > 0 && `, shared with ${open.api_signals.other_workers_on_those_devices} other workers`}.
                Browser-reported columns above are the browser's own word.
              </p>
            </>
          )}
          {open.matches.length > 0 && (
            <>
              <h4>The same person at another vendor</h4>
              <p className="muted small">
                From the keyed hashes vendors submit; the platform never sees who the person is. Evidence for a person to weigh,
                never an action, and the vendor is never told which vendor matched.
              </p>
              <ul>
                {open.matches.map((m, i) => (
                  <li key={i}>
                    <span className="mono">{m.handle}</span> at {m.vendor_name} is <span className="mono">{m.matched_handle}</span> at{" "}
                    {m.matched_vendor}{m.matched_suspended && ", who was suspended or removed there"}
                  </li>
                ))}
              </ul>
            </>
          )}
          <h4>History</h4>
          <ul className="history">
            {open.actions.map((a, i) => (
              <li key={i}>
                <span className="muted">{when(a.at)}</span> · <strong>{a.action}</strong> by {a.by}
                {a.note && <> · {a.note}</>}
              </li>
            ))}
          </ul>
          {isVendor(open) && open.status === "open" && (
            <VendorCase c={open} me={me} onChange={(c) => (setOpen(c), load())} onError={setError} />
          )}
          {open.status === "open" && isOwner(open) && (
            <div className="decide">
              <select value={decision} onChange={(e) => setDecision(e.target.value)}>
                {decisionsFor(open).map((d) => (
                  <option key={d.value} value={d.value}>
                    {d.label}
                  </option>
                ))}
              </select>
              <input placeholder="Reason (required, kept on record)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <button className="primary" disabled={reason.trim().length < 3} onClick={decide}>
                Decide
              </button>
            </div>
          )}
          {lineage && (
            <div className="lineage">
              <h4>Lineage</h4>
              <p>
                {lineage.submissions} answers from <span className="mono">{lineage.handle}</span>: {lineage.held} held, {lineage.removed} removed,{" "}
                {lineage.accepted_now} accepted now.
              </p>
              {lineage.datasets.length > 0 ? (
                <ul>
                  {lineage.datasets.map((d) => (
                    <li key={d.dataset_id}>
                      {d.rows_from_subject} rows in the dataset exported {when(d.created_at)}
                      {d.current ? " (current: re-export after clean-up)" : " (superseded)"}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">None of these answers is in an exported dataset.</p>
              )}
              {open.status === "decided" && (open.decision === "suspend" || open.decision === "remove") && !cleaned && (
                <div className="row">
                  <button className="primary" onClick={() => cleanup("relabel")}>Remove answers and re-label tasks</button>
                  <button onClick={() => cleanup("discard")}>Remove answers only</button>
                </div>
              )}
            </div>
          )}
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </section>
    {me.role === "vendor_admin" && <OwnRules />}
    </>

  );
}

/** A vendor case before it's decided (AB5, D42): data ops shares it, the vendor responds, and
 *  data ops may act once the vendor has responded or the window has passed. */
function VendorCase({ c, me, onChange, onError }: {
  c: CaseDetail; me: User; onChange: (c: CaseDetail) => void; onError: (e: string) => void;
}) {
  const [text, setText] = useState("");
  const [action, setAction] = useState("lower_allocation");
  const [share, setShare] = useState("15");
  const [reason, setReason] = useState("");
  const post = async (path: string, body?: object) => {
    try {
      onChange(await api<CaseDetail>(`/cases/${c.id}/${path}`, { method: "POST", body: body ? JSON.stringify(body) : undefined }));
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };
  if (me.role === "vendor_admin") {
    return (
      <div className="decide">
        <input placeholder="Your response to Anthropic (kept on the case)" value={text} onChange={(e) => setText(e.target.value)} />
        <button className="primary" disabled={text.trim().length < 3} onClick={() => (post("responses", { text }), setText(""))}>
          Respond
        </button>
      </div>
    );
  }
  if (me.role !== "data_ops") return null;
  if (!c.shared_at) {
    return (
      <p>
        <button className="primary" onClick={() => post("share")}>Ask {c.vendor_name} to respond</button>{" "}
        <span className="muted">Until then, the vendor doesn't see this case.</span>
      </p>
    );
  }
  return (
    <>
      <p className="muted">
        Shared with {c.vendor_name}. {c.can_act ? "You can act on the vendor now." : `Actions wait for its response, or until ${c.respond_by ? new Date(c.respond_by).toLocaleString() : "the window passes"}.`}
      </p>
      {c.can_act && (
        <div className="decide">
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            {VENDOR_ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
          </select>
          {action === "lower_allocation" && (
            <label className="nowrap">
              to <input type="number" min={1} max={99} value={share} onChange={(e) => setShare(e.target.value)} style={{ width: 60 }} />%
            </label>
          )}
          <input placeholder="Reason (required, the vendor sees it)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="primary" disabled={reason.trim().length < 3}
                  onClick={() => (post("actions", { action, reason, project_id: c.project_id, share: action === "lower_allocation" ? Number(share) / 100 : null }), setReason(""))}>
            Act
          </button>
        </div>
      )}
    </>
  );
}

const SIGNALS: Record<string, string> = {
  fast: "Median time on task (seconds) is below",
  pasted: "Share of answers pasted (0 to 1) is above",
  focus_changes: "Focus changes per answer are above",
  quick_start: "Median seconds before the first input are below",
};

/** A vendor's own rules (AB9, D56): its thresholds on signals the platform records, opening cases only it sees. */
function OwnRules() {
  const [rules, setRules] = useState<VendorRule[]>([]);
  const [name, setName] = useState("");
  const [signal, setSignal] = useState("fast");
  const [threshold, setThreshold] = useState("");
  const [windowSize, setWindowSize] = useState("20");
  const load = () => api<VendorRule[]>("/vendor-rules").then(setRules);
  useEffect(() => {
    load();
  }, []);

  async function create() {
    await api("/vendor-rules", { method: "POST", body: JSON.stringify({ name, signal, threshold: Number(threshold), window: Number(windowSize) }) });
    setName("");
    setThreshold("");
    load();
  }

  return (
    <section>
      <h2>Your own rules</h2>
      <p className="muted">
        Look at a worker sooner than the platform's shared rules would. Your rules run on your workers' latest answers; a worker
        who crosses one gets a case only you see. It holds nothing, and never counts toward your scorecard or Anthropic's rules.
      </p>
      {rules.length > 0 && (
        <table>
          <thead><tr><th>Rule</th><th>Fires when</th><th>Over the last</th><th /></tr></thead>
          <tbody>
            {rules.map((r) => (
              <tr key={r.id} className={r.retired_at ? "muted" : undefined}>
                <td>{r.name}</td>
                <td>{SIGNALS[r.signal]} {r.threshold}</td>
                <td>{r.window} answers</td>
                <td>{r.retired_at ? "Retired" : <button onClick={() => api(`/vendor-rules/${r.id}/retire`, { method: "POST" }).then(load)}>Retire</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="review-form">
        <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Too quick" /></label>
        <label className="field">
          Signal
          <select value={signal} onChange={(e) => setSignal(e.target.value)}>
            {Object.entries(SIGNALS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="field">Threshold<input inputMode="decimal" value={threshold} onChange={(e) => setThreshold(e.target.value)} /></label>
        <label className="field">Answers to look at<input inputMode="numeric" value={windowSize} onChange={(e) => setWindowSize(e.target.value)} /></label>
        <div className="row">
          <button className="primary" disabled={!name || threshold === "" || Number.isNaN(Number(threshold))} onClick={create}>Add rule</button>
        </div>
      </div>
    </section>
  );
}
