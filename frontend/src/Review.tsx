import { useEffect, useState } from "react";
import { api, type Project, type ReviewItem } from "./api";

export default function Review() {
  const [project, setProject] = useState<Project | null>(null);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [done, setDone] = useState<Record<string, string>>({});

  useEffect(() => {
    api<Project[]>("/projects").then((list) => setProject(list[0] ?? null));
  }, []);

  async function load() {
    if (!project) return;
    setItems(await api<ReviewItem[]>(`/projects/${project.id}/review-queue?n=5`));
    setDone({});
    setReasons({});
  }

  useEffect(() => {
    load();
  }, [project]); // eslint-disable-line react-hooks/exhaustive-deps

  async function decide(item: ReviewItem, verdict: "accepted" | "rejected") {
    await api(`/submissions/${item.submission_id}/review`, {
      method: "POST",
      body: JSON.stringify({ verdict, reason: reasons[item.submission_id] ?? null }),
    });
    setDone((d) => ({ ...d, [item.submission_id]: verdict }));
  }

  return (
    <section>
      <h2>Spot checks</h2>
      <p className="muted">A random sample of answers on regular tasks. Rejections need a reason; the worker sees it.</p>
      <button onClick={load}>New sample</button>
      {items.map((item) => (
        <div key={item.submission_id} className="card task">
          <p className="muted">
            {item.vendor_name} · <span className="mono">{item.worker_handle}</span>
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
          {done[item.submission_id] ? (
            <p className="muted">Marked {done[item.submission_id]}.</p>
          ) : (
            <div className="row">
              <input
                placeholder="Reason (required to reject)"
                value={reasons[item.submission_id] ?? ""}
                onChange={(e) => setReasons((r) => ({ ...r, [item.submission_id]: e.target.value }))}
              />
              <button onClick={() => decide(item, "accepted")}>Accept</button>
              <button disabled={!reasons[item.submission_id]?.trim()} onClick={() => decide(item, "rejected")}>
                Reject
              </button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
