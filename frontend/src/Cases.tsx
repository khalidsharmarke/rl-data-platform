import { useEffect, useState } from "react";
import { api, type CaseDetail, type CaseSummary, type User } from "./api";

const DECISIONS = [
  { value: "suspend", label: "Suspend worker" },
  { value: "warn", label: "Warn and release hold" },
  { value: "clear", label: "Clear: no abuse" },
  { value: "remove", label: "Remove worker" },
];

const when = (t: string) => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export default function Cases({ me }: { me: User }) {
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [open, setOpen] = useState<CaseDetail | null>(null);
  const [decision, setDecision] = useState("suspend");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
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
    setOpen(await api<CaseDetail>(`/cases/${id}`));
  }

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
          ? "Every case across vendors and how it was resolved. Worker cases are decided by the worker's vendor."
          : "Workers the platform's signals flagged. Their answers are held from training until you decide."}
      </p>
      <table>
        <thead>
          <tr>
            <th>Opened</th>
            {me.role === "data_ops" && <th>Vendor</th>}
            <th>Worker</th>
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
                {c.subject_name ?? ""} <span className="mono muted">{c.subject_handle}</span>
              </td>
              <td>{c.observations.length} signal{c.observations.length === 1 ? "" : "s"}</td>
              <td>
                {c.status === "open" ? <span className="badge warn">Open{c.held ? " · data held" : ""}</span> : <span className="badge">{c.decision}</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {open && (
        <div className="card case">
          <h3>
            {open.subject_name ?? "Worker"} <span className="mono muted">{open.subject_handle}</span> · {open.vendor_name}
          </h3>
          <h4>What the platform observed</h4>
          <ul>
            {open.flags.map((f) => (
              <li key={f.observation}>
                {f.observation}
                {f.rule && <span className="mono muted"> ({f.rule_version})</span>}
              </li>
            ))}
          </ul>
          <h4>Examples</h4>
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
                {DECISIONS.map((d) => (
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
          {error && <p className="error">{error}</p>}
        </div>
      )}
    </section>
  );
}
