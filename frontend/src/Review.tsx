import { useEffect, useState } from "react";
import { api, type HackCheck, type Project, type ReviewItem, type User, type VersionRow } from "./api";
import EnvironmentDetail, { QuarantineForm } from "./EnvironmentDetail";

export default function Review({ me }: { me: User }) {
  const [project, setProject] = useState<Project | null>(null);
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [done, setDone] = useState<Record<string, string>>({});
  const [abusive, setAbusive] = useState<Record<string, boolean>>({});

  useEffect(() => {
    api<Project[]>("/projects").then((list) => setProject(list.find((p) => p.kind === "human_data") ?? null));
  }, []);

  async function load() {
    if (!project) return;
    setItems(await api<ReviewItem[]>(`/projects/${project.id}/review-queue?n=5`));
    setDone({});
    setReasons({});
    setAbusive({});
  }

  useEffect(() => {
    load();
  }, [project]); // eslint-disable-line react-hooks/exhaustive-deps

  async function decide(item: ReviewItem, verdict: "accepted" | "rejected") {
    await api(`/submissions/${item.submission_id}/review`, {
      method: "POST",
      body: JSON.stringify({ verdict, reason: reasons[item.submission_id] ?? null, looks_abusive: abusive[item.submission_id] }),
    });
    setDone((d) => ({ ...d, [item.submission_id]: verdict }));
  }

  return (
    <section>
      <HackChecks />
      <EnvironmentQueue me={me} />
      <h2>Spot checks</h2>
      <p className="muted">
        Answers on regular tasks. Rejections need a reason; the worker sees it. Whether an answer looks like abuse is a separate
        question, never shown to the worker or vendor: an honest but poor answer is rejected, not abusive.
      </p>
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
            <>
            <div className="row judgement">
              <span>Looks like abuse?</span>
              {([false, true] as const).map((v) => (
                <label key={String(v)}>
                  <input type="radio" name={`abuse-${item.submission_id}`} checked={abusive[item.submission_id] === v}
                         onChange={() => setAbusive((a) => ({ ...a, [item.submission_id]: v }))} />{" "}
                  {v ? "Yes: pasted, generic or not engaging with the task" : "No"}
                </label>
              ))}
            </div>
            <div className="row">
              <input
                placeholder="Reason (required to reject)"
                value={reasons[item.submission_id] ?? ""}
                onChange={(e) => setReasons((r) => ({ ...r, [item.submission_id]: e.target.value }))}
              />
              <button disabled={abusive[item.submission_id] === undefined} onClick={() => decide(item, "accepted")}>Accept</button>
              <button disabled={abusive[item.submission_id] === undefined || !reasons[item.submission_id]?.trim()}
                      onClick={() => decide(item, "rejected")}>
                Reject
              </button>
            </div>
            </>
          )}
        </div>
      ))}
    </section>
  );
}

/** Environment versions that passed the sandbox's checks and wait for a researcher (EC5). */
function EnvironmentQueue({ me }: { me: User }) {
  const [queue, setQueue] = useState<VersionRow[]>([]);
  const [briefs, setBriefs] = useState<Record<string, Project>>({});
  const [open, setOpen] = useState<VersionRow | null>(null);

  async function load() {
    setQueue(await api<VersionRow[]>("/versions?status=in_review"));
    const list = await api<Project[]>("/projects");
    setBriefs(Object.fromEntries(list.filter((p) => p.kind === "environment").map((p) => [p.id, p])));
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="card">
      <h2>Environments to review</h2>
      {queue.length === 0 ? (
        <p className="muted">Nothing waiting. Versions appear here once they pass the sandbox's checks.</p>
      ) : (
        <table>
          <thead>
            <tr><th>Environment</th><th>Brief</th><th>Vendor</th><th>Version</th></tr>
          </thead>
          <tbody>
            {queue.map((v) => (
              <tr key={v.id} className="clickable" onClick={() => setOpen(open?.id === v.id ? null : v)}>
                <td>
                  {v.environment_name}
                  {v.batch && <div className="muted small">Sample from {v.batch}: approving the whole sample approves the batch</div>}
                </td>
                <td>{briefs[v.project_id]?.name ?? "—"}</td>
                <td>{v.vendor_name}</td>
                <td>v{v.number}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && briefs[open.project_id] && (
        <EnvironmentDetail
          key={open.id}
          environmentId={open.environment_id}
          initialVersion={open.id}
          me={me}
          rubric={briefs[open.project_id].brief!.review_rubric}
          onChange={() => {
            setOpen(null);
            load();
          }}
        />
      )}
    </div>
  );
}

/** Weak reward-hack findings (D38): read a few transcripts, then say it looks fine or flag the version. */
function HackChecks() {
  const [checks, setChecks] = useState<HackCheck[]>([]);
  const [flagging, setFlagging] = useState<string | null>(null);
  const load = () => api<HackCheck[]>("/hack-checks").then(setChecks);
  useEffect(() => {
    load();
  }, []);
  if (checks.length === 0) return null;
  async function answer(id: string, outcome: "looks_fine" | "flagged") {
    await api(`/hack-checks/${id}`, { method: "POST", body: JSON.stringify({ outcome }) });
    setFlagging(null);
    load();
  }
  return (
    <div className="card">
      <h2>Training transcripts to check</h2>
      <p className="muted">The hack detector saw something worth a look, but not enough to quarantine on its own.</p>
      {checks.map((c) => (
        <div key={c.id} className="env-detail">
          <p>
            <strong>{c.environment_name}</strong> in <span className="mono">{c.run_label}</span>: {c.observation}
          </p>
          {c.episodes.map((e) => (
            <details key={e.episode_id}>
              <summary className="muted">Step {e.step.toLocaleString()} · reward {e.reward} · {e.length} turns</summary>
              <pre>{e.transcript ?? "No transcript."}</pre>
            </details>
          ))}
          <div className="actions row">
            <button onClick={() => answer(c.id, "looks_fine")}>Looks fine</button>
            <button className="primary" onClick={() => setFlagging(c.id)}>Flag the version</button>
          </div>
          {flagging === c.id && <QuarantineForm versionId={c.version_id} onDone={() => answer(c.id, "flagged")} />}
        </div>
      ))}
    </div>
  );
}
