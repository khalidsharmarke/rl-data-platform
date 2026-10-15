import { useEffect, useState } from "react";
import { api, type VendorQueue } from "./api";

/** The vendor reviewer's queue (DC8, D44): accept each answer, or correct it before it reaches Anthropic.
 *  A correction is kept beside the worker's own answer, which Anthropic still measures the worker on. */
export default function VendorReview() {
  const [queue, setQueue] = useState<VendorQueue | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const load = () => api<VendorQueue>("/vendor-review/queue?n=5").then(setQueue);
  useEffect(() => {
    load();
  }, []);
  if (!queue) return null;

  async function decide(id: string, decision: "accepted" | "corrected", answer?: object) {
    await api(`/vendor-review/${id}`, { method: "POST", body: JSON.stringify({ decision, answer, note: notes[id] || null }) });
    load();
  }

  return (
    <section>
      <h2>Review before submitting</h2>
      <p className="muted">
        {queue.waiting.toLocaleString()} answers from your workers are waiting. Nothing reaches Anthropic until you accept it or correct
        it. Anthropic still measures each worker on their own answer; your correction is what your company submits.
      </p>
      {queue.items.length === 0 && <p className="muted">Nothing waiting.</p>}
      {queue.items.map((item) => {
        const other = item.answer.choice === "A" ? "B" : "A";
        return (
          <div key={item.submission_id} className="card task">
            <p className="muted">
              {item.project_name} · {item.worker_name} <span className="mono">{item.worker_handle}</span>
            </p>
            <h3>{item.payload.prompt}</h3>
            <div className="choices">
              {(["A", "B"] as const).map((key) => (
                <div key={key} className={item.answer.choice === key ? "choice selected" : "choice"}>
                  <strong>Answer {key}{item.answer.choice === key ? " · chosen" : ""}</strong>
                  <span>{key === "A" ? item.payload.response_a : item.payload.response_b}</span>
                </div>
              ))}
            </div>
            {item.answer.rationale && <p className="muted">Reason given: {item.answer.rationale}</p>}
            <div className="row">
              <input placeholder="Note for the worker (optional)" value={notes[item.submission_id] ?? ""}
                     onChange={(e) => setNotes((n) => ({ ...n, [item.submission_id]: e.target.value }))} />
              <button className="primary" onClick={() => decide(item.submission_id, "accepted")}>Accept</button>
              <button onClick={() => decide(item.submission_id, "corrected", { ...item.answer, choice: other })}>
                Correct to {other}
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
