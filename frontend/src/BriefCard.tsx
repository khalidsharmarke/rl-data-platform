import { useEffect, useState } from "react";
import { api, type Allocation, type EnvironmentRow, type EnvProgress, type Project, type User } from "./api";
import EnvironmentDetail, { STATUS } from "./EnvironmentDetail";

/** An environment brief (EC0): what the researcher asked for, who it's allocated to, and the environments built against it. */
export default function BriefCard({ project, me }: { project: Project; me: User }) {
  const [allocations, setAllocations] = useState<Allocation[]>([]);
  const [environments, setEnvironments] = useState<EnvironmentRow[]>([]);
  const [progress, setProgress] = useState<EnvProgress | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const internal = me.org_kind === "internal";
  const brief = project.brief!;

  useEffect(() => {
    if (internal) api<Allocation[]>(`/projects/${project.id}/allocations`).then(setAllocations);
    load();
  }, [project.id, internal]); // eslint-disable-line react-hooks/exhaustive-deps

  function load() {
    api<EnvironmentRow[]>(`/environments?project_id=${project.id}`).then(setEnvironments);
    api<EnvProgress>(`/projects/${project.id}/environment-progress`).then(setProgress);
  }

  const mix = Object.entries(brief.difficulty_mix).map(([level, share]) => `${Math.round(share * 100)}% ${level}`).join(", ");
  return (
    <div className="card">
      <h3>{project.name}{project.onboarding && <span className="chip">Onboarding sample: feeds nothing</span>}</h3>
      <p className="muted">
        Environment brief · {project.target_volume.toLocaleString()} environments wanted{mix && ` · ${mix}`}
        {brief.languages.length > 0 && ` · ${brief.languages.join(", ")}`}
      </p>
      <p>{brief.description}</p>
      <div className="brief-lists">
        {brief.constraints.length > 0 && (
          <div>
            <h4>Constraints</h4>
            <ul>{brief.constraints.map((c) => <li key={c}>{c}</li>)}</ul>
          </div>
        )}
        <div>
          <h4>Review rubric</h4>
          <ul>{brief.review_rubric.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      </div>
      {internal && allocations.length > 0 && (
        <p className="muted">Allocated to {allocations.map((a) => a.vendor_name).join(", ")}</p>
      )}
      <h4>Environments</h4>
      {progress && (
        <p className="muted">
          {progress.approved} of {progress.target} approved · {progress.in_review} in review · {progress.failed} sent back or failed ·{" "}
          {progress.in_progress} in progress
        </p>
      )}
      {environments.length === 0 ? (
        <p className="muted">None submitted yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Environment</th>
              {internal && <th>Vendor</th>}
              <th>Versions</th>
              <th>Latest version</th>
            </tr>
          </thead>
          <tbody>
            {environments.map((e) => (
              <tr key={e.id} className="clickable" onClick={() => setOpen(open === e.id ? null : e.id)}>
                <td>{e.name}</td>
                {internal && <td>{e.vendor_name}</td>}
                <td>{e.versions}</td>
                <td>{e.latest_status ? STATUS[e.latest_status] : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <EnvironmentDetail key={open} environmentId={open} me={me} rubric={brief.review_rubric} onChange={load} />}
    </div>
  );
}
