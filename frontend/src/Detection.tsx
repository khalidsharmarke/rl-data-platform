import { useEffect, useState } from "react";
import { api, pct, type DetectionQuality, type RuleMeasure } from "./api";

const DETECTORS: Record<string, string> = { "rules-v1": "Worker abuse", "hacks-v1": "Reward hacks" };

/** How well detection is doing, and which rule versions may act (AB15, AB16, D40, D41). Anthropic only. */
export default function Detection({ canChange }: { canChange: boolean }) {
  const [data, setData] = useState<DetectionQuality | null>(null);
  const load = () => api<DetectionQuality>("/detection-quality").then(setData);
  useEffect(() => {
    load();
  }, []);
  if (!data) return null;
  return (
    <section>
      <h2>Detection</h2>
      <p className="muted">
        Every rule version starts in shadow: its findings are recorded and a few of its flagged answers go to spot checks, but
        it opens no case and nobody outside Anthropic sees it. Precision is the share of its reviewed flags that reviewers
        judged abusive; data ops can make a version live once {data.min_reviewed} of its flags have been reviewed and its
        precision meets their bar.
      </p>
      <table>
        <thead>
          <tr>
            <th>Detector</th><th>Rule version</th><th>Mode</th><th>Findings</th><th>Reviewed</th><th>Precision</th><th></th>
          </tr>
        </thead>
        <tbody>
          {data.rules.map((r) => (
            <RuleRow key={r.rule_version} rule={r} canChange={canChange} onChange={load} />
          ))}
        </tbody>
      </table>
      <h3>What the live rules miss</h3>
      <p className="muted">
        A random share of answers goes to spot checks, mixed in so reviewers can't tell them apart. Among audited answers from
        workers no live rule has flagged, the share reviewers judged abusive estimates what detection misses.
      </p>
      <p>
        {data.audited === 0 ? (
          <span className="muted">No audited answers reviewed yet.</span>
        ) : (
          <>
            <strong>{pct(data.miss_rate)}</strong> missed: {data.audited_abusive} of {data.audited} audited answers judged abusive.
          </>
        )}
      </p>
    </section>
  );
}

function RuleRow({ rule: r, canChange, onChange }: { rule: RuleMeasure; canChange: boolean; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [bar, setBar] = useState(String(Math.round((r.bar ?? 0.8) * 100)));
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const next = r.mode === "live" ? "shadow" : "live";
  async function change() {
    setError(null);
    try {
      await api(`/detectors/${encodeURIComponent(r.rule_version)}/mode`, {
        method: "PUT",
        body: JSON.stringify({ mode: next, bar: next === "live" ? Number(bar) / 100 : null, reason }),
      });
      setOpen(false);
      onChange();
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }
  return (
    <>
      <tr>
        <td>{DETECTORS[r.detector] ?? r.detector}</td>
        <td className="mono">{r.rule_version}</td>
        <td>
          <span className={r.mode === "shadow" ? "badge warn" : "badge"}>{r.mode === "shadow" ? "Shadow" : "Live"}</span>
        </td>
        <td>{r.findings} on {r.subjects} {r.detector === "hacks-v1" ? "versions" : "workers"}</td>
        <td>{r.reviewed}</td>
        <td>
          {r.reviewed ? `${pct(r.precision)} (${r.abusive} of ${r.reviewed})` : "—"}
          {r.bar != null && <span className="muted"> · bar {pct(r.bar)}</span>}
        </td>
        <td>
          {canChange && (r.mode === "live" || r.can_promote) && (
            <button className="nowrap" onClick={() => setOpen(!open)}>{r.mode === "live" ? "To shadow" : "Make live"}</button>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7}>
            <div className="row">
              {next === "live" && (
                <label>
                  Precision bar (%){" "}
                  <input type="number" min={1} max={100} value={bar} onChange={(e) => setBar(e.target.value)} style={{ width: 70 }} />
                </label>
              )}
              <input placeholder="Reason (required, kept on record)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <button className="primary" disabled={reason.trim().length < 3} onClick={change}>
                {next === "live" ? "Make live" : "Send back"}
              </button>
            </div>
            {error && <p className="error">{error}</p>}
          </td>
        </tr>
      )}
    </>
  );
}
