import { useEffect, useState } from "react";
import { api, savedToken, type CheckRun, type EnvReview, type EnvVersion, type User, type VersionDiff } from "./api";

export const STATUS: Record<string, string> = {
  draft: "Draft",
  submitted: "Submitted",
  validating: "Validating",
  failed: "Failed",
  in_review: "In review",
  approved: "Approved",
  quarantined: "Quarantined",
};

const CHECKS: Record<string, string> = {
  reference: "Reference solution passes",
  empty: "Empty solution fails",
  repeat: "Repeated runs agree",
};

const PART: Record<string, string> = { task: "Task files", grader: "Grader", reference: "Reference solution" };

/**
 * One environment's versions (EC2, EC4, EC5): check results with logs, reviews, the diff from
 * the previous version, and the files. A researcher reviewing a version in review also gets
 * the brief's rubric to grade against.
 */
export default function EnvironmentDetail({
  environmentId, me, rubric, initialVersion, onChange,
}: {
  environmentId: string; me: User; rubric: string[]; initialVersion?: string; onChange?: () => void;
}) {
  const [versions, setVersions] = useState<EnvVersion[]>([]);
  const [selected, setSelected] = useState<string | null>(initialVersion ?? null);

  async function load() {
    const list = await api<EnvVersion[]>(`/environments/${environmentId}/versions`);
    setVersions(list);
    setSelected((s) => s ?? list[list.length - 1]?.id ?? null);
  }

  useEffect(() => {
    load();
  }, [environmentId]); // eslint-disable-line react-hooks/exhaustive-deps

  const version = versions.find((v) => v.id === selected);
  return (
    <div className="env-detail">
      <div className="version-tabs">
        {versions.map((v) => (
          <button key={v.id} className={v.id === selected ? "tab active" : "tab"} onClick={() => setSelected(v.id)}>
            v{v.number} · {STATUS[v.status]}
          </button>
        ))}
      </div>
      {version && (
        <VersionView
          key={version.id}
          version={version}
          me={me}
          rubric={rubric}
          onReviewed={() => {
            load();
            onChange?.();
          }}
        />
      )}
    </div>
  );
}

function VersionView({ version, me, rubric, onReviewed }: { version: EnvVersion; me: User; rubric: string[]; onReviewed: () => void }) {
  const [checks, setChecks] = useState<CheckRun[]>([]);
  const [reviews, setReviews] = useState<EnvReview[]>([]);
  const [diff, setDiff] = useState<VersionDiff | null>(null);
  const [file, setFile] = useState<{ path: string; text: string } | null>(null);

  useEffect(() => {
    api<CheckRun[]>(`/versions/${version.id}/checks`).then(setChecks);
    api<EnvReview[]>(`/versions/${version.id}/reviews`).then(setReviews);
    api<VersionDiff>(`/versions/${version.id}/diff`).then(setDiff);
  }, [version.id]);

  async function open(path: string) {
    const token = savedToken();
    const response = await fetch(`/api/versions/${version.id}/files/${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    setFile({ path, text: await response.text() });
  }

  const changed = diff?.files.filter((f) => f.change !== "unchanged") ?? [];
  return (
    <div className="version">
      <p className="muted">
        Test command <code>{version.manifest.test_command}</code> on <code>{version.manifest.base_image}</code>
      </p>

      <h4>Checks</h4>
      {checks.length === 0 ? (
        <p className="muted">{["submitted", "validating"].includes(version.status) ? "Running in the sandbox…" : "Not checked: the version hasn't been submitted."}</p>
      ) : (
        <ul className="checks">
          {checks.map((c) => (
            <li key={c.check_name}>
              <span className={c.passed ? "ok" : "bad"}>{c.passed ? "✓" : "✗"}</span> <strong>{CHECKS[c.check_name]}</strong>
              <span className="muted"> · {(c.duration_ms / 1000).toFixed(1)} s</span>
              <div>{c.detail}</div>
              <details>
                <summary className="muted">Logs</summary>
                <pre>{c.logs}</pre>
              </details>
            </li>
          ))}
        </ul>
      )}

      {reviews.length > 0 && (
        <>
          <h4>Review</h4>
          {reviews.map((r) => (
            <div key={r.created_at} className="review">
              <strong>{r.decision === "approve" ? "Approved" : "Sent back"}</strong> by {r.reviewer}
              <ul className="rubric">
                {Object.entries(r.rubric).map(([criterion, met]) => (
                  <li key={criterion}><span className={met ? "ok" : "bad"}>{met ? "✓" : "✗"}</span> {criterion}</li>
                ))}
              </ul>
              <p>{r.feedback}</p>
            </div>
          ))}
        </>
      )}

      {me.role === "researcher" && version.status === "in_review" && <ReviewForm versionId={version.id} rubric={rubric} onDone={onReviewed} />}

      {diff?.against_number && (
        <>
          <h4>Changes from v{diff.against_number}</h4>
          {changed.length === 0 ? (
            <p className="muted">No files changed.</p>
          ) : (
            changed.map((f) => (
              <details key={f.path} open={changed.length <= 2}>
                <summary>
                  {f.path} <span className="muted">· {f.change}</span>
                </summary>
                {f.diff && <pre className="diff">{f.diff.join("\n")}</pre>}
              </details>
            ))
          )}
        </>
      )}

      <h4>Files</h4>
      <ul className="files">
        {version.manifest.files.map((f) => (
          <li key={f.path}>
            <a href="#" onClick={(e) => (e.preventDefault(), open(f.path))}>{f.path}</a>
            <span className="muted"> · {PART[f.part]} · {f.size} bytes</span>
          </li>
        ))}
      </ul>
      {file && (
        <>
          <p className="muted">{file.path}</p>
          <pre>{file.text}</pre>
        </>
      )}
    </div>
  );
}

function ReviewForm({ versionId, rubric, onDone }: { versionId: string; rubric: string[]; onDone: () => void }) {
  const [met, setMet] = useState<Record<string, boolean>>(Object.fromEntries(rubric.map((c) => [c, false])));
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState<string | null>(null);
  const allMet = rubric.every((c) => met[c]);

  async function decide(decision: "approve" | "send_back") {
    try {
      await api(`/versions/${versionId}/reviews`, { method: "POST", body: JSON.stringify({ decision, rubric: met, feedback }) });
      onDone();
    } catch (e) {
      setError(String((e as Error).message));
    }
  }

  return (
    <div className="review-form">
      <h4>Grade against the brief's rubric</h4>
      {rubric.map((c) => (
        <label key={c} className="criterion">
          <input type="checkbox" checked={met[c]} onChange={(e) => setMet({ ...met, [c]: e.target.checked })} /> {c}
        </label>
      ))}
      <textarea placeholder="Feedback for the builder (required)" value={feedback} onChange={(e) => setFeedback(e.target.value)} />
      <div className="actions">
        <button className="primary" disabled={!allMet || feedback.length < 3} onClick={() => decide("approve")}>Approve</button>
        <button disabled={feedback.length < 3} onClick={() => decide("send_back")}>Send back</button>
      </div>
      {error && <p className="bad">{error}</p>}
    </div>
  );
}
