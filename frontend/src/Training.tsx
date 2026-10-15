import { useEffect, useState } from "react";
import { api, pct, type EpisodeDetail, type EpisodeRow, type RunDetail, type RunRow } from "./api";
import { QuarantineForm, STATUS } from "./EnvironmentDetail";
import LineChart, { type Series } from "./LineChart";

const MAX_SERIES = 3; // the validated palette's categorical slots; never cycled

/** Training runs as they happen (TO2, TO4, TO5, D39): the platform's own statistics, not a detector's. */
export default function Training() {
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [versionId, setVersionId] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      api<RunRow[]>("/runs").then((list) => {
        setRuns(list);
        setRunId((id) => id ?? list[0]?.id ?? null);
      });
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!runId) return;
    const load = () => api<RunDetail>(`/runs/${runId}`).then(setDetail);
    load();
    const timer = setInterval(load, 3000); // polling stands in for a live stream (B7)
    return () => clearInterval(timer);
  }, [runId]);

  if (runs.length === 0) return <section><h2>Training</h2><p className="muted">No training runs have reported yet.</p></section>;

  const charted = [...(detail?.versions ?? [])].sort((a, b) => b.episodes - a.episodes).slice(0, MAX_SERIES);
  const series = (key: "solve_rate" | "mean_length"): Series[] =>
    charted.map((v, i) => ({ name: v.environment_name, slot: i + 1, points: v.series.map((p) => ({ t: p.step, v: p[key] })) }));
  const maxLength = Math.max(10, ...charted.flatMap((v) => v.series.map((p) => p.mean_length ?? 0)));
  const selected = detail?.versions.find((v) => v.version_id === versionId);

  return (
    <section>
      <h2>Training</h2>
      <div className="version-tabs">
        {runs.map((r) => (
          <button key={r.id} className={r.id === runId ? "tab active" : "tab"} onClick={() => (setRunId(r.id), setVersionId(null))}>
            {r.label}
          </button>
        ))}
      </div>
      {detail && (
        <>
          <p className="muted">
            {detail.run.episodes.toLocaleString()} episodes across {detail.run.environments} environment
            {detail.run.environments === 1 ? "" : "s"}, up to step {detail.run.last_step?.toLocaleString() ?? "—"}.{" "}
            {detail.unprocessed > 0 ? `${detail.unprocessed} episodes not yet in the statistics.` : "Statistics are up to date."}
          </p>
          <LineChart title="Solve rate over training" series={series("solve_rate")} format={(v) => `${Math.round(v * 100)}%`}
                     xFormat={(t) => `step ${t.toLocaleString()}`} />
          <LineChart title="Attempt length over training (turns)" series={series("mean_length")} format={(v) => v.toFixed(0)}
                     domain={[0, Math.ceil(maxLength / 20) * 20]} xFormat={(t) => `step ${t.toLocaleString()}`} />
          {detail.versions.length > MAX_SERIES && (
            <p className="muted">The charts show the {MAX_SERIES} environments with the most episodes; the table lists all of them.</p>
          )}
          <table>
            <thead>
              <tr>
                <th>Environment</th><th>Vendor</th><th>Version</th><th>Episodes</th><th>Solve rate</th>
                <th>Reward variance</th><th>Mean length</th><th>Difficulty</th>
              </tr>
            </thead>
            <tbody>
              {detail.versions.map((v) => (
                <tr key={v.version_id} className="clickable" onClick={() => setVersionId(versionId === v.version_id ? null : v.version_id)}>
                  <td>{v.environment_name}</td>
                  <td>{v.vendor_name}</td>
                  <td>v{v.number} · {STATUS[v.status]}</td>
                  <td>{v.episodes.toLocaleString()}</td>
                  <td>{pct(v.solve_rate)}</td>
                  <td>{v.reward_variance?.toFixed(2) ?? "—"}</td>
                  <td>{v.mean_length?.toFixed(1) ?? "—"}</td>
                  <td>{v.difficulty ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {selected && <Episodes runId={detail.run.id} version={selected} />}
        </>
      )}
    </section>
  );
}

function Episodes({ runId, version }: { runId: string; version: RunDetail["versions"][number] }) {
  const [rows, setRows] = useState<EpisodeRow[]>([]);
  const [open, setOpen] = useState<EpisodeDetail | null>(null);
  useEffect(() => {
    api<EpisodeRow[]>(`/runs/${runId}/episodes?version_id=${version.version_id}`).then(setRows);
    setOpen(null);
  }, [runId, version.version_id]);
  return (
    <div className="env-detail">
      <h4>Latest episodes · {version.environment_name}</h4>
      <table>
        <thead>
          <tr><th>Step</th><th>Reward</th><th>Length</th><th>Touched</th></tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.episode_id} className="clickable" onClick={() => api<EpisodeDetail>(`/episodes/${encodeURIComponent(e.episode_id)}`).then(setOpen)}>
              <td>{e.step.toLocaleString()}</td>
              <td>{e.reward}</td>
              <td>{e.length}</td>
              <td className="mono">{e.touched.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {open && (
        <div className="version">
          <p className="muted">Episode {open.episode_id} · step {open.step.toLocaleString()} · reward {open.reward}</p>
          <pre>{open.transcript ?? "No transcript."}</pre>
        </div>
      )}
      {version.status === "approved" && <QuarantineForm versionId={version.version_id} onDone={() => undefined} />}
    </div>
  );
}
