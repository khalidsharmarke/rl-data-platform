import { useEffect, useState } from "react";
import { api, pct, type TaskHealth as Health } from "./api";

/** Broken tasks workers flagged, and disagreement alerts (DC5, D43). Anthropic only. */
export default function TaskHealth({ projectId, canDecide }: { projectId: string; canDecide: boolean }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const load = () => api<Health>(`/projects/${projectId}/task-health`).then(setHealth);
  useEffect(() => {
    load();
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!health) return null;
  const paused = health.broken.filter((t) => t.broken_status === "paused");
  const retired = health.broken.filter((t) => t.broken_status === "retired");
  const alert = health.agreement_alert;
  if (!alert && health.broken.length === 0 && health.flaggers.length === 0) return null;

  async function decide(taskId: string, decision: "restore" | "retire") {
    await api(`/tasks/${taskId}/broken-decision`, {
      method: "POST",
      body: JSON.stringify({ decision, reason: reasons[taskId] }),
    });
    load();
  }

  return (
    <div className="card">
      <h3>Task health</h3>
      {alert && (
        <div className="alert">
          <strong>Low agreement in one batch:</strong> {pct(alert.agreement)} on the {alert.batch_tasks} tasks added{" "}
          {new Date(alert.batch_added_at).toLocaleString()}, where the same workers reach {pct(alert.others)} on the project's other tasks. Its
          tasks with the most disagreement:
          <ul>
            {alert.tasks.map((t) => (
              <li key={t.task_id}>
                {t.payload.prompt} <span className="muted">· {t.disagreements} disagreeing{t.broken_status ? ` · ${t.broken_status}` : ""}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="muted">
        A task two workers flag as broken stops being routed until a researcher restores or retires it. A retired task counts
        nowhere: not in volume, accuracy, agreement or datasets.
      </p>
      {paused.length === 0 && <p className="muted">No tasks waiting for a decision.</p>}
      {paused.map((t) => (
        <div key={t.task_id} className="env-detail">
          <p>
            <strong>{t.payload.prompt}</strong> {t.is_gold && <span className="badge">gold</span>}{" "}
            <span className="muted">· {t.answers} answers so far</span>
          </p>
          <div className="choices">
            <div className="choice"><strong>Answer A</strong><span>{t.payload.response_a}</span></div>
            <div className="choice"><strong>Answer B</strong><span>{t.payload.response_b}</span></div>
          </div>
          <ul>
            {t.flags.map((f, i) => (
              <li key={i}>
                <span className="mono muted">{f.handle}</span>: {f.reason}
              </li>
            ))}
          </ul>
          {canDecide && (
            <div className="row">
              <input placeholder="Reason (required, kept on record)" value={reasons[t.task_id] ?? ""}
                     onChange={(e) => setReasons((r) => ({ ...r, [t.task_id]: e.target.value }))} />
              <button disabled={!reasons[t.task_id]?.trim()} onClick={() => decide(t.task_id, "restore")}>Restore</button>
              <button className="primary" disabled={!reasons[t.task_id]?.trim()} onClick={() => decide(t.task_id, "retire")}>
                Retire
              </button>
            </div>
          )}
        </div>
      ))}
      {retired.length > 0 && <p className="muted">{retired.length} task{retired.length === 1 ? "" : "s"} retired.</p>}
      {health.flaggers.length > 0 && (
        <>
          <h4>Who flags</h4>
          <table>
            <thead>
              <tr><th>Worker</th><th>Vendor</th><th>Flags</th><th>On tasks later retired</th></tr>
            </thead>
            <tbody>
              {health.flaggers.map((f) => (
                <tr key={f.handle}>
                  <td className="mono">{f.handle}</td><td>{f.vendor_name}</td><td>{f.flags}</td><td>{f.retired}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
