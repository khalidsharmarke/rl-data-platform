import { useEffect, useState } from "react";
import { api, type CaseDetail, type CaseSummary, type Lineage, type User } from "./api";

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

const OUTCOME: Record<string, string> = { suspend: "Suspended", remove: "Removed", warn: "Warned", clear: "Cleared" };
const ENVIRONMENT_OUTCOME: Record<string, string> = { remove: "Retired", clear: "Back in review" };
const isEnvironment = (c: CaseSummary) => c.subject_type === "environment_version";

const when = (t: string) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export default function Cases({ me }: { me: User }) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [open, setOpen] = useState<CaseDetail | null>(null);
  const [decision, setDecision] = useState("suspend");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lineage, setLineage] = useState<Lineage | null>(null);
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
    setDecision(isEnvironment(detail) ? "remove" : "suspend");
    setLineage(null);
    if (me.role === "data_ops" && !isEnvironment(detail) && detail.subject_id && detail.project_id) {
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
    <section>
      <h2>Cases</h2>
      <p className="muted">
        {me.role === "data_ops"
          ? "Every case across vendors and how it was resolved. Worker cases are decided by the worker's vendor; environment cases by data ops."
          : "Workers the platform's signals flagged. Their answers are held from training until you decide."}
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
                {isEnvironment(c) ? c.subject_handle : <>{c.subject_name ?? ""} <span className="mono muted">{c.subject_handle}</span></>}
              </td>
              <td>{isEnvironment(c) ? "Flagged by a person" : `${c.observations.length} signal${c.observations.length === 1 ? "" : "s"}`}</td>
              <td>
                {c.status === "open" ? (
                  <span className="badge warn">Open{c.held ? (isEnvironment(c) ? " · quarantined" : " · data held") : ""}</span>
                ) : (
                  <span className="badge">
                    {(isEnvironment(c) ? ENVIRONMENT_OUTCOME : OUTCOME)[c.decision ?? ""] ?? c.decision}
                    {c.held && !isEnvironment(c) ? " · data held" : ""}
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
            {isEnvironment(open) ? <>Environment: {open.subject_handle}</> : <>{open.subject_name ?? "Worker"} <span className="mono muted">{open.subject_handle}</span></>} ·{" "}
            {open.vendor_name}
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
          {open.samples.length > 0 && <h4>Examples</h4>}
          {open.samples.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Question</th>
                <th>Answer and reason</th>
                <th>Time</th>
                <th>Pasted</th>
              </tr>
            </thead>
            <tbody>
              {open.samples.map((s) => (
                <tr key={s.submission_id}>
                  <td>{s.prompt}</td>
                  <td>
                    {s.answer.choice}
                    {s.answer.rationale && <span className="muted"> · “{s.answer.rationale}”</span>}
                  </td>
                  <td>{s.time_on_task_s} s</td>
                  <td>{s.pasted ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
          {open.status === "open" && isOwner(open) && (
            <div className="decide">
              <select value={decision} onChange={(e) => setDecision(e.target.value)}>
                {(isEnvironment(open) ? ENVIRONMENT_DECISIONS : DECISIONS).map((d) => (
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
  );
}
