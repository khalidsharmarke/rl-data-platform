import { useEffect, useState } from "react";
import { api, type FeedbackItem } from "./api";

export default function Feedback() {
  const [items, setItems] = useState<FeedbackItem[] | null>(null);
  useEffect(() => {
    api<FeedbackItem[]>("/me/feedback").then(setItems);
  }, []);
  return (
    <section>
      <h2>Feedback</h2>
      <p className="muted">Answers a reviewer rejected, and why.</p>
      {items?.length === 0 && <p className="muted">Nothing rejected so far.</p>}
      {items?.map((f) => (
        <div key={f.submission_id} className="card">
          <p className="muted">{new Date(f.reviewed_at).toLocaleString()} · {f.project_name}</p>
          <h3>{f.prompt}</h3>
          <p>You chose answer {f.your_answer.choice}.</p>
          <p><strong>Reviewer:</strong> {f.reason}</p>
        </div>
      ))}
    </section>
  );
}
