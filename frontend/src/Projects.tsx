import { useEffect, useState } from "react";
import BriefCard from "./BriefCard";
import TaskHealth from "./TaskHealth";
import { api, pct, savedToken, type Allocation, type Contract, type Dataset, type Escalation, type Grades, type Project, type Quality, type Release, type User } from "./api";

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
  const [grades, setGrades] = useState<Record<string, Grades>>({});
  const [escalations, setEscalations] = useState<Record<string, Escalation[]>>({});
  const internal = me.org_kind === "internal";

  useEffect(() => {
    api<Project[]>("/projects").then(async (list) => {
      setProjects(list);
      if (!internal) return;
      list = list.filter((p) => p.kind === "human_data");
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
        const graded = await api<Grades>(`/projects/${p.id}/grades`);
        setGrades((g) => ({ ...g, [p.id]: graded }));
        const escalated = await api<Escalation[]>(`/projects/${p.id}/escalations`);
        setEscalations((x) => ({ ...x, [p.id]: escalated }));
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
      {projects.filter((p) => p.kind === "environment").map((p) => <BriefCard key={p.id} project={p} me={me} />)}
      {projects.filter((p) => p.kind === "human_data" && me.role !== "environment_builder").map((p) => (
        <div key={p.id} className="card">
          <h3>{p.name}{p.onboarding && <span className="chip">Onboarding sample: feeds nothing</span>}</h3>
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
                      <td>{a.status}{a.vendor_review ? " · vendor reviews" : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {me.role === "vendor_admin" && <ReviewSwitch projectId={p.id} />}
          {internal && (grades[p.id]?.workers.length ?? 0) > 0 && <GradesTable g={grades[p.id]} escalations={escalations[p.id] ?? []} />}
          {internal && p.kind === "human_data" && <TaskHealth projectId={p.id} canDecide={me.role === "researcher"} />}
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

const INTEGRITY: Record<string, string> = {
  trusted: "Trusted",
  unproven: "Unproven",
  watch: "Watched",
  held: "Held (open case)",
  suspended: "Suspended",
};

const OUTCOME: Record<string, string> = {
  cleared: "cleared by later gold",
  confirmed: "confirmed: case opened",
  case: "ended by a case",
};

function GradesTable({ g, escalations }: { g: Grades; escalations: Escalation[] }) {
  const counts = g.workers.reduce<Record<string, number>>((c, w) => ({ ...c, [w.integrity]: (c[w.integrity] ?? 0) + 1 }), {});
  return (
    <details className="grades">
      <summary>
        Worker grades: {Object.entries(INTEGRITY).filter(([k]) => counts[k]).map(([k, label]) => `${counts[k]} ${label.toLowerCase()}`).join(" · ")}
      </summary>
      <p className="muted">
        Quality is gold accuracy with a 95% interval, against the {Math.round(g.quality_bar * 100)}% bar. Integrity sets how
        many gold answers the worker must get right after an answer before it is released as verified. Workers and vendors
        never see these.
      </p>
      <table>
        <thead>
          <tr>
            <th>Worker</th>
            <th>Vendor</th>
            <th>Gold answers</th>
            <th>Accuracy (95% interval)</th>
            <th>Quality</th>
            <th>Integrity</th>
            <th>Later gold needed</th>
          </tr>
        </thead>
        <tbody>
          {g.workers.map((w) => (
            <tr key={w.worker_id}>
              <td>{w.handle}</td>
              <td>{w.vendor_name}</td>
              <td>{w.gold_n}</td>
              <td>
                {pct(w.gold_accuracy)}
                {w.interval && ` (${pct(w.interval[0])}–${pct(w.interval[1])})`}
              </td>
              <td>{w.quality === "meets" ? "Meets the bar" : w.quality === "below" ? "Below the bar" : "Uncertain"}</td>
              <td>
                {INTEGRITY[w.integrity]}
                {w.watch_progress && ` · ${w.watch_progress[1]}/${w.watch_progress[0]} gold right since`}
              </td>
              <td>{w.later_gold_needed ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {escalations.length > 0 && (
        <>
          <h4>Escalations</h4>
          <p className="muted">
            After a single signal, the platform gives the worker more gold tasks, unannounced, until ten later gold answers
            clear them or confirm a case.
          </p>
          <ul>
            {escalations.map((x) => (
              <li key={x.id}>
                {x.handle} ({x.vendor_name}) after a {x.rule} signal, {new Date(x.started_at).toLocaleString()}:{" "}
                {x.outcome ? OUTCOME[x.outcome] : "open"} · {x.gold_right_since} of {x.gold_since} later gold answers right
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  );
}

/** A vendor admin's switch for reviewing workers' answers before they reach Anthropic (DC8, D44). */
function ReviewSwitch({ projectId }: { projectId: string }) {
  const [mine, setMine] = useState<{ id: string; vendor_review: boolean } | null>(null);
  useEffect(() => {
    api<{ id: string; vendor_review: boolean }>(`/projects/${projectId}/my-allocation`).then(setMine).catch(() => setMine(null));
  }, [projectId]);
  if (!mine) return null;
  async function toggle(on: boolean) {
    await api(`/allocations/${mine!.id}/vendor-review`, { method: "PUT", body: JSON.stringify({ on }) });
    setMine({ ...mine!, vendor_review: on });
  }
  return (
    <label className="row">
      <input type="checkbox" style={{ flex: "none" }} checked={mine.vendor_review} onChange={(e) => toggle(e.target.checked)} />
      Our reviewers check each answer before it reaches Anthropic
    </label>
  );
}
