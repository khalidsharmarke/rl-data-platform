import { useState } from "react";
import { api, type Assignment } from "./api";

export default function Work() {
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [choice, setChoice] = useState<"A" | "B" | null>(null);
  const [rationale, setRationale] = useState("");
  const [pasted, setPasted] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [done, setDone] = useState(0);

  async function next() {
    setMessage(null);
    setChoice(null);
    setRationale("");
    setPasted(false);
    const claimed = await api<Assignment | null>("/assignments/next", { method: "POST", body: "{}" });
    setAssignment(claimed);
    if (!claimed) setMessage("No tasks available right now.");
  }

  async function submit() {
    if (!assignment || !choice) return;
    try {
      const answer = rationale.trim() ? { choice, rationale: rationale.trim() } : { choice };
      await api(`/assignments/${assignment.id}/submit`, {
        method: "POST",
        body: JSON.stringify({ answer, client_signals: { pasted } }),
      });
      setDone((n) => n + 1);
      await next();
    } catch (e) {
      setMessage(String(e instanceof Error ? e.message : e));
    }
  }

  return (
    <section>
      <h2>Work</h2>
      <p className="muted">Submitted this session: {done}</p>
      {!assignment && (
        <button className="primary" onClick={next}>
          Get a task
        </button>
      )}
      {message && <p className="muted">{message}</p>}
      {assignment && (
        <div className="card task">
          <p className="muted">{assignment.task.project_name}</p>
          <p>{assignment.task.instructions}</p>
          <h3>{assignment.task.payload.prompt}</h3>
          <div className="choices">
            {(["A", "B"] as const).map((key) => (
              <button key={key} className={choice === key ? "choice selected" : "choice"} onClick={() => setChoice(key)}>
                <strong>Answer {key}</strong>
                <span>{key === "A" ? assignment.task.payload.response_a : assignment.task.payload.response_b}</span>
              </button>
            ))}
          </div>
          <label className="field">
            Reason (optional)
            <input value={rationale} onChange={(e) => setRationale(e.target.value)} onPaste={() => setPasted(true)} />
          </label>
          <button className="primary" disabled={!choice} onClick={submit}>
            Submit
          </button>
        </div>
      )}
    </section>
  );
}
