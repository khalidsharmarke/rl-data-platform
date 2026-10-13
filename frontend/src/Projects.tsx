import { useEffect, useState } from "react";
import { api, pct, type Allocation, type Contract, type Project, type Quality, type User } from "./api";

const UNIT: Record<string, string> = { task: "per task", hour: "per hour", environment: "per environment" };

export default function Projects({ me }: { me: User }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [allocations, setAllocations] = useState<Record<string, Allocation[]>>({});
  const [contracts, setContracts] = useState<Record<string, Contract>>({});
  const [quality, setQuality] = useState<Record<string, Quality>>({});
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
                      <td>{a.routed_tasks}</td>
                      <td>{a.submissions}</td>
                      <td>{pct(q?.gold_accuracy)}</td>
                      <td>{pct(q?.agreement)}</td>
                      <td>{q?.accepted ?? 0}</td>
                      <td>{a.status}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      ))}
    </section>
  );
}
