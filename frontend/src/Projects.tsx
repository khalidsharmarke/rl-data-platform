import { useEffect, useState } from "react";
import { api, pct, savedToken, type Allocation, type Contract, type Dataset, type Project, type Quality, type Release, type User } from "./api";

const UNIT: Record<string, string> = { task: "per task", hour: "per hour", environment: "per environment" };

async function download(id: string) {
  const token = savedToken();
  const response = await fetch(`/api/datasets/${id}/download`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const url = URL.createObjectURL(await response.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: `dataset-${id}.jsonl` });
  a.click();
  URL.revokeObjectURL(url);
}

export default function Projects({ me }: { me: User }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [allocations, setAllocations] = useState<Record<string, Allocation[]>>({});
  const [contracts, setContracts] = useState<Record<string, Contract>>({});
  const [quality, setQuality] = useState<Record<string, Quality>>({});
  const [datasets, setDatasets] = useState<Record<string, Dataset[]>>({});
  const [releases, setReleases] = useState<Record<string, Release>>({});
  const internal = me.org_kind === "internal";

  useEffect(() => {
    api<Project[]>("/projects").then(async (list) => {
      setProjects(list);
      if (!internal) return;
      const byProject: Record<string, Allocation[]> = {};
      const byContract: Record<string, Contract> = {};
      const byQuality: Record<string, Quality> = {};
      for (const p of list) {
        byProject[p.id] = await api<Allocation[]>(`/projects/${p.id}/allocations`);
        byQuality[p.id] = await api<Quality>(`/projects/${p.id}/quality`);
        const sets = await api<Dataset[]>(`/projects/${p.id}/datasets`);
        setDatasets((d) => ({ ...d, [p.id]: sets }));
        const release = await api<Release>(`/projects/${p.id}/release`);
        setReleases((r) => ({ ...r, [p.id]: release }));
        if (me.role === "data_ops") {
          for (const a of byProject[p.id]) {
            for (const c of await api<Contract[]>(`/orgs/${a.vendor_id}/contracts`)) byContract[c.id] = c;
          }
        }
      }
      setAllocations(byProject);
      setQuality(byQuality);
      setContracts(byContract);
    });
  }, [internal, me.role]);

  return (
    <section>
      <h2>Projects</h2>
      {projects.map((p) => (
        <div key={p.id} className="card">
          <h3>{p.name}</h3>
          <p className="muted">
            {p.task_type} · task design v{p.current_schema_version} · {p.overlap} answers per task · target{" "}
            {p.target_volume.toLocaleString()}
          </p>
          {internal && (
            <table>
              <thead>
                <tr>
                  <th>Vendor</th>
                  <th>Share</th>
                  {me.role === "data_ops" && <th>Contract</th>}
                  <th>Tasks routed</th>
                  <th>Answers submitted</th>
                  <th>Gold accuracy</th>
                  <th>Agreement</th>
                  <th>Accepted</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {(allocations[p.id] ?? []).map((a) => {
                  const c = contracts[a.contract_id];
                  const q = quality[p.id]?.vendors.find((v) => v.id === a.vendor_id);
                  return (
                    <tr key={a.id}>
                      <td>{a.vendor_name}</td>
                      <td>{Math.round(Number(a.share) * 100)}%</td>
                      {me.role === "data_ops" && <td>{c ? `$${Number(c.price).toFixed(2)} ${UNIT[c.price_unit]}` : "—"}</td>}
                      <td>{a.routed_tasks.toLocaleString()}</td>
                      <td>{a.submissions.toLocaleString()}</td>
                      <td>{pct(q?.gold_accuracy)}</td>
                      <td>{pct(q?.agreement)}</td>
                      <td>{(q?.accepted ?? 0).toLocaleString()}</td>
                      <td>{a.status}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {internal && (
            <div className="datasets">
              <h4>Datasets</h4>
              {releases[p.id] && <ReleaseLine r={releases[p.id]} />}
              <button
                onClick={async () => {
                  const d = await api<Dataset>(`/projects/${p.id}/exports`, { method: "POST" });
                  setDatasets((all) => ({ ...all, [p.id]: [d, ...(all[p.id] ?? [])] }));
                  const release = await api<Release>(`/projects/${p.id}/release`);
                  setReleases((r) => ({ ...r, [p.id]: release }));
                }}
              >
                Export released answers
              </button>
              <ul>
                {(datasets[p.id] ?? []).slice(0, 5).map((d, i) => (
                  <li key={d.id}>
                    {new Date(d.created_at).toLocaleString()} · {d.row_count.toLocaleString()} rows
                    {d.provisional > 0 && ` (${d.provisional.toLocaleString()} provisional)`} · {d.metric_version}
                    {i === 0 ? " · current" : " · superseded"} ·{" "}
                    <a href="#" onClick={(e) => (e.preventDefault(), download(d.id))}>download</a>
                  </li>
                ))}
              </ul>
              {(datasets[p.id]?.length ?? 0) > 5 && <p className="muted">and {datasets[p.id].length - 5} earlier exports</p>}
            </div>
          )}
        </div>
      ))}
    </section>
  );
}

function ReleaseLine({ r }: { r: Release }) {
  const waiting = r.waiting.detection + r.waiting.gap + r.waiting.evidence;
  const hours = (m: number) => (m % 60 === 0 ? `${m / 60} h` : `${m} min`);
  return (
    <p className="muted">
      Of {r.accepted.toLocaleString()} accepted answers, {r.verified.toLocaleString()} are released as verified and{" "}
      {r.provisional.toLocaleString()} as provisional. {waiting.toLocaleString()} are waiting: {r.waiting.detection} for
      detection, {r.waiting.gap} for the minimum gap ({hours(r.release_after_minutes)}), {r.waiting.evidence} for later
      evidence about their worker (provisional after {hours(r.release_cap_minutes)}).
    </p>
  );
}
