import { useEffect, useRef, useState } from "react";
import { api, type Assignment } from "./api";

export default function Work() {
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [choice, setChoice] = useState<"A" | "B" | null>(null);
  const [rationale, setRationale] = useState("");
  const [pasted, setPasted] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [done, setDone] = useState(0);
  const [flagging, setFlagging] = useState(false);
  const [flagReason, setFlagReason] = useState("");
  // How the answer was produced (AB11): reported to the platform as untrusted evidence, never as proof.
  const shownAt = useRef(0);
  const firstInput = useRef<number | null>(null);
  const focusChanges = useRef(0);
  const viewed = useRef(false);
  const choices = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!assignment) return;
    shownAt.current = performance.now();
    firstInput.current = null;
    focusChanges.current = 0;
    viewed.current = false;
    const blur = () => (focusChanges.current += 1);
    window.addEventListener("blur", blur);
    const seen = new IntersectionObserver((e) => e.some((x) => x.isIntersecting) && (viewed.current = true), { threshold: 1 });
    if (choices.current) seen.observe(choices.current);
    return () => (window.removeEventListener("blur", blur), seen.disconnect());
  }, [assignment]);

  const touched = () => {
    if (firstInput.current === null) firstInput.current = Math.round(performance.now() - shownAt.current);
  };

  async function next() {
    setMessage(null);
    setChoice(null);
    setRationale("");
    setPasted(false);
    setFlagging(false);
    setFlagReason("");
    try {
      const claimed = await api<Assignment | null>("/assignments/next", { method: "POST", body: "{}" });
      setAssignment(claimed);
      if (!claimed) setMessage("No tasks available right now.");
    } catch (e) {
      setAssignment(null);
      setMessage(e instanceof Error ? e.message : String(e));
    }
  }

  async function submit() {
    if (!assignment || !choice) return;
    try {
      const answer = rationale.trim() ? { choice, rationale: rationale.trim() } : { choice };
      await api(`/assignments/${assignment.id}/submit`, {
        method: "POST",
        body: JSON.stringify({
          answer,
          client_signals: { pasted, first_input_ms: firstInput.current, focus_changes: focusChanges.current, content_viewed: viewed.current },
        }),
      });
      setDone((n) => n + 1);
      await next();
    } catch (e) {
      setMessage(String(e instanceof Error ? e.message : e));
    }
  }

  /** A broken task is flagged instead of answered (DC5); the worker moves straight on. */
  async function flag() {
    if (!assignment) return;
    try {
      await api(`/tasks/${assignment.task.id}/flag`, { method: "POST", body: JSON.stringify({ reason: flagReason.trim() }) });
      await next();
      setMessage("Thanks: the task was flagged and a researcher will look at it.");
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
          <div className="choices" ref={choices}>
            {(["A", "B"] as const).map((key) => (
              <button key={key} className={choice === key ? "choice selected" : "choice"} onClick={() => (touched(), setChoice(key))}>
                <strong>Answer {key}</strong>
                <span>{key === "A" ? assignment.task.payload.response_a : assignment.task.payload.response_b}</span>
              </button>
            ))}
          </div>
          <label className="field">
            Reason (optional)
            <input value={rationale} onChange={(e) => (touched(), setRationale(e.target.value))} onPaste={() => (touched(), setPasted(true))} />
          </label>
          <div className="row">
            <button className="primary" disabled={!choice} onClick={submit}>
              Submit
            </button>
            <button onClick={() => setFlagging(!flagging)}>This task is broken</button>
          </div>
          {flagging && (
            <div className="row">
              <input placeholder="What's wrong with it? (required)" value={flagReason} onChange={(e) => setFlagReason(e.target.value)} />
              <button disabled={flagReason.trim().length < 3} onClick={flag}>
                Flag and skip
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
