import type { ReactNode, RefObject } from "react";

/** How each task type (backend/app/task_types.py, D53) looks: the task, and an answer to it. */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Payload = Record<string, any>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Answer = Record<string, any>;

/** One line for an answer of any type, for tables and lists. */
export function summary(answer: Answer): string {
  if (answer.outcome) return `${answer.first_wrong_step ? `step ${answer.first_wrong_step} goes wrong` : "no step goes wrong"}; ${answer.outcome}`;
  if (answer.choice) return `Answer ${answer.choice}`;
  if (answer.score != null) return `${answer.score} of 5`;
  if (answer.text) return answer.text.length > 120 ? `${answer.text.slice(0, 117)}…` : answer.text;
  return JSON.stringify(answer);
}

const OUTCOMES = { succeeded: "Succeeded", partly: "Partly", failed: "Failed" } as const;

/** An agent's transcript as numbered steps; `pick` makes each step choosable as the first that goes wrong. */
function Steps({ payload, marked, pick }: { payload: Payload; marked?: number; pick?: (n: number) => void }) {
  return (
    <>
      <p className="muted">
        {payload.environment} · the grader {payload.graded === "passed" ? "passed" : "failed"} this attempt · episode{" "}
        <span className="mono">{payload.episode_id}</span>
      </p>
      <ol className="steps">
        {(payload.steps as string[]).map((step, i) => (
          <li key={i} className={marked === i + 1 ? "wrong" : undefined}>
            {pick ? (
              <button className={marked === i + 1 ? "choice selected" : "choice"} onClick={() => pick(i + 1)}>
                <span className="mono">{step}</span>
              </button>
            ) : (
              <span className="mono">{step}</span>
            )}
            {marked === i + 1 && <strong> ← first wrong step</strong>}
          </li>
        ))}
      </ol>
    </>
  );
}

/** The task, with an answer shown if there is one (spot checks, vendor review). */
export function TaskShown({ type, payload, answer }: { type: string; payload: Payload; answer?: Answer }) {
  return (
    <>
      <h3>{payload.prompt}</h3>
      {type === "pairwise_preference" && (
        <div className="choices">
          {(["A", "B"] as const).map((key) => (
            <div key={key} className={answer?.choice === key ? "choice selected" : "choice"}>
              <strong>Answer {key}{answer?.choice === key ? " · chosen" : ""}</strong>
              <span>{key === "A" ? payload.response_a : payload.response_b}</span>
            </div>
          ))}
        </div>
      )}
      {type === "rating" && (
        <>
          <div className="choice">{payload.response}</div>
          {answer && <p><strong>Rated {answer.score} of 5</strong></p>}
        </>
      )}
      {type === "writing" && answer && <div className="choice written">{answer.text}</div>}
      {type === "trajectory_review" && (
        <>
          <Steps payload={payload} marked={answer?.first_wrong_step} />
          {answer && <p><strong>{summary(answer)}</strong></p>}
        </>
      )}
      {answer?.rationale && <p className="muted">Reason given: {answer.rationale}</p>}
    </>
  );
}

/** The work screen's input for each type. `answer` is null until the worker has given the required part. */
export function AnswerInput({ type, payload, answer, set, touched, pasted, area }: {
  type: string;
  payload: Payload;
  answer: Answer;
  set: (a: Answer) => void;
  touched: () => void;
  pasted: () => void;
  area: RefObject<HTMLDivElement>;
}): ReactNode {
  const reason = (
    <label className="field">
      Reason (optional)
      <input value={answer.rationale ?? ""} onPaste={() => (touched(), pasted())}
             onChange={(e) => (touched(), set({ ...answer, rationale: e.target.value }))} />
    </label>
  );
  if (type === "rating")
    return (
      <>
        <div className="choice" ref={area}>{payload.response}</div>
        <div className="row" role="radiogroup" aria-label="How helpful is this answer, from 1 to 5?">
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} className={answer.score === n ? "choice selected" : "choice"} onClick={() => (touched(), set({ ...answer, score: n }))}>
              {n}
            </button>
          ))}
        </div>
        {reason}
      </>
    );
  if (type === "trajectory_review")
    return (
      <div ref={area}>
        <p>Click the first step that goes wrong, or say none does.</p>
        <Steps payload={payload} marked={answer.first_wrong_step} pick={(n) => (touched(), set({ ...answer, first_wrong_step: n }))} />
        <button className={answer.first_wrong_step === 0 ? "choice selected" : "choice"} onClick={() => (touched(), set({ ...answer, first_wrong_step: 0 }))}>
          No step goes wrong
        </button>
        <p>Did the agent do what the task asked?</p>
        <div className="row" role="radiogroup" aria-label="Outcome">
          {Object.entries(OUTCOMES).map(([value, label]) => (
            <button key={value} className={answer.outcome === value ? "choice selected" : "choice"} onClick={() => (touched(), set({ ...answer, outcome: value }))}>
              {label}
            </button>
          ))}
        </div>
        {reason}
      </div>
    );
  if (type === "writing")
    return (
      <div ref={area}>
        <label className="field">
          Your answer
          <textarea rows={8} value={answer.text ?? ""} onPaste={() => (touched(), pasted())}
                    onChange={(e) => (touched(), set({ ...answer, text: e.target.value }))} />
        </label>
      </div>
    );
  return (
    <>
      <div className="choices" ref={area}>
        {(["A", "B"] as const).map((key) => (
          <button key={key} className={answer.choice === key ? "choice selected" : "choice"} onClick={() => (touched(), set({ ...answer, choice: key }))}>
            <strong>Answer {key}</strong>
            <span>{key === "A" ? payload.response_a : payload.response_b}</span>
          </button>
        ))}
      </div>
      {reason}
    </>
  );
}

/** Whether an answer has what its type requires, and the answer as submitted (empty reasons dropped). */
export function ready(type: string, answer: Answer): Answer | null {
  const out = { ...answer };
  if (!out.rationale?.trim()) delete out.rationale;
  else out.rationale = out.rationale.trim();
  if (type === "rating") return out.score ? out : null;
  if (type === "trajectory_review") return out.first_wrong_step != null && out.outcome ? out : null;
  if (type === "writing") return out.text?.trim() ? { text: out.text.trim() } : null;
  return out.choice ? out : null;
}
