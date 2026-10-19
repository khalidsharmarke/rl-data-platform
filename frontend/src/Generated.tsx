import { useEffect, useState } from "react";
import { api, pct, type User } from "./api";

type Summary = {
  batches: { batch: string; status: string; versions: number; sampled: number; sample_reviewed: number }[];
  stopped: { id: number; family: string; reason: string; stopped_at: string }[];
  families: { family: string; intended: string; versions: number; approved: number; failed: number; episodes: number; solve_rate: number | null; measured: string | null }[];
};

const BATCH: Record<string, string> = {
  in_review: "Sample in review",
  approved: "Approved with its sample",
  sent_back: "Sent back",
  nothing_passed: "Nothing passed the checks",
};

/** Generated tasks on a brief (EC7, D49): batches reviewed by sample, stopped families, and calibration. */
export default function Generated({ projectId, me }: { projectId: string; me: User }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const load = () => api<Summary>(`/projects/${projectId}/generated`).then(setSummary);
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!summary || summary.families.length === 0) return null;
  return (
    <>
      <h4>Generated tasks</h4>
      <p className="muted">
        Researchers review a random sample of each batch (the square root of what passed the checks, at least three). Approving the
        whole sample approves the batch; sending one back sends the batch back and stops its family.
      </p>
      {summary.stopped.map((s) => (
        <p key={s.id} className="alert">
          <strong>{s.family}</strong> is stopped: {s.reason.replace(/\.$/, "")}.{" "}
          {me.role === "researcher" && (
            <button onClick={() => api(`/family-stops/${s.id}/resume`, { method: "POST" }).then(load)}>Template fixed: resume</button>
          )}
        </p>
      ))}
      {summary.batches.length > 0 && (
        <table>
          <thead>
            <tr><th>Batch</th><th>Versions</th><th>Sample reviewed</th><th>Status</th></tr>
          </thead>
          <tbody>
            {summary.batches.map((b) => (
              <tr key={b.batch}>
                <td className="mono">{b.batch}</td>
                <td>{b.versions}</td>
                <td>{b.sample_reviewed} of {b.sampled}</td>
                <td>{BATCH[b.status] ?? b.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h4>Intended against measured difficulty</h4>
      <p className="muted">Measured from training's solve rates once training has used a family's tasks (EC3).</p>
      <table>
        <thead>
          <tr><th>Family</th><th>Intended</th><th>Versions</th><th>Approved</th><th>Episodes</th><th>Solve rate</th><th>Measured</th></tr>
        </thead>
        <tbody>
          {summary.families.map((f) => (
            <tr key={`${f.family}-${f.intended}`}>
              <td className="mono">{f.family}</td>
              <td>{f.intended}</td>
              <td>{f.versions}</td>
              <td>{f.approved}</td>
              <td>{f.episodes.toLocaleString()}</td>
              <td>{pct(f.solve_rate)}</td>
              <td className={f.measured && f.measured !== f.intended ? "bad" : undefined}>{f.measured ?? <span className="muted">not yet</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
