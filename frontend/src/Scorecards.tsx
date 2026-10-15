import { useEffect, useState } from "react";
import { api, pct, type Project, type Scorecard, type ScorePoint, type User, type VendorComparison, type WorkerQuality, type EnvironmentWork } from "./api";
import LineChart, { type Series } from "./LineChart";

const WINDOW = 4; // periods in the rolling window

/** Rolling gold accuracy over the last WINDOW periods; null where gold is held back or too thin. */
function rollingGold(points: ScorePoint[]) {
  return points.map((p, i) => {
    const win = points.slice(Math.max(0, i - WINDOW + 1), i + 1);
    if (win.some((q) => q.gold_n == null)) return { t: Date.parse(p.period_start), v: null };
    const n = win.reduce((a, q) => a + (q.gold_n ?? 0), 0);
    const c = win.reduce((a, q) => a + (q.gold_accuracy ?? 0) * (q.gold_n ?? 0), 0);
    return { t: Date.parse(p.period_start), v: n >= 10 ? c / n : null };
  });
}

function acceptedShare(points: ScorePoint[]) {
  return points.map((p, i) => {
    const win = points.slice(Math.max(0, i - WINDOW + 1), i + 1);
    const regular = win.reduce((a, q) => a + q.submissions - (q.gold_n ?? 0), 0);
    const acc = win.reduce((a, q) => a + q.accepted, 0);
    // Without gold counts (held back for vendors), fall back to all answers as the base.
    const base = win.some((q) => q.gold_n == null) ? win.reduce((a, q) => a + q.submissions, 0) : regular;
    // Too few answers in the window to say anything (for example, a run starting mid-period).
    return { t: Date.parse(p.period_start), v: base >= 20 ? acc / base : null };
  });
}

const money = (v: number | null) => (v == null ? "—" : `$${v.toFixed(2)}`);

export default function Scorecards({ me }: { me: User }) {
  const [project, setProject] = useState<Project | null>(null);
  const [comparison, setComparison] = useState<VendorComparison[]>([]);
  const [cards, setCards] = useState<Scorecard[]>([]);
  const [workers, setWorkers] = useState<WorkerQuality[]>([]);
  const [computedAt, setComputedAt] = useState<string | null>(null);
  const vendorView = me.role === "vendor_admin";

  useEffect(() => {
    api<Project[]>("/projects").then((list) => {
      const human = list.filter((p) => p.kind === "human_data");
      setProject(human.find((p) => !p.name.includes("(sample)")) ?? human[0] ?? null);
    });
  }, []);

  useEffect(() => {
    if (!project) return;
    const load = async () => {
      if (vendorView) {
        setCards([await api<Scorecard>(`/vendors/${me.org_id}/scorecard?project_id=${project.id}`)]);
        setWorkers(await api<WorkerQuality[]>(`/vendors/${me.org_id}/workers?project_id=${project.id}`));
      } else {
        const c = await api<{ computed_at: string | null; vendors: VendorComparison[] }>(`/projects/${project.id}/vendors`);
        setComparison(c.vendors);
        setComputedAt(c.computed_at);
        setCards(await Promise.all(c.vendors.map((v) => api<Scorecard>(`/vendors/${v.vendor_id}/scorecard?project_id=${project.id}`))));
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [project, vendorView, me.org_id]);

  // Color follows the vendor, in a fixed order, never its rank.
  const slot = (name: string) => ["Brightline Annotation", "Kestrel Labs", "Northwind Data"].indexOf(name) + 1 || 1;
  const gold: Series[] = cards.map((c) => ({ name: c.vendor_name, slot: slot(c.vendor_name), points: rollingGold(c.points) }));
  const accepted: Series[] = cards.map((c) => ({ name: c.vendor_name, slot: slot(c.vendor_name), points: acceptedShare(c.points) }));
  const alerts = cards.flatMap((c) => c.alerts.map((a) => ({ ...a, vendor: c.vendor_name })));
  const lastAlert = Object.values(Object.fromEntries(alerts.map((a) => [a.vendor, a])));

  if (!project) return <p className="muted">No projects yet.</p>;
  return (
    <section>
      <h2>Scorecards · {project.name}</h2>
      <p className="muted">
        Every number is computed by the platform the same way for every vendor (metric {cards[0]?.metric_version ?? "quality-v1"}).
        {computedAt && ` Updated ${new Date(computedAt).toLocaleTimeString()}.`}
        {vendorView && cards[0]?.gold_shown_until && ` Gold-based numbers are shown up to ${new Date(cards[0].gold_shown_until).toLocaleTimeString()}.`}
      </p>

      {lastAlert.length > 0 && (
        <div className="alerts">
          {lastAlert.map((a) => (
            <p key={a.vendor} className="alert">
              <span aria-hidden>▲</span> <strong>{a.vendor}</strong>: gold accuracy over the last hour is {pct(a.recent)}, down from{" "}
              {pct(a.baseline)} before it.
            </p>
          ))}
        </div>
      )}

      {!vendorView && (
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Answers</th>
              <th>Accepted</th>
              <th>Gold accuracy</th>
              <th>Agreement</th>
              <th>Cost</th>
              <th>Cost per accepted</th>
              <th>Avg turnaround</th>
            </tr>
          </thead>
          <tbody>
            {comparison.map((v) => (
              <tr key={v.vendor_id}>
                <td>
                  <i className="swatch" style={{ background: `var(--series-${slot(v.vendor_name)})` }} /> {v.vendor_name}
                </td>
                <td>{v.submissions.toLocaleString()}</td>
                <td>
                  {v.accepted.toLocaleString()} ({pct(v.acceptance_rate)})
                </td>
                <td>{pct(v.gold_accuracy)}</td>
                <td>{pct(v.agreement)}</td>
                <td>{money(v.cost)}</td>
                <td>
                  <strong>{money(v.cost_per_accepted)}</strong>
                </td>
                <td>{v.avg_turnaround_minutes == null ? "—" : `${Math.round(v.avg_turnaround_minutes)} min`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <LineChart title="Gold accuracy (rolling hour)" series={gold} format={(v) => `${Math.round(v * 100)}%`} domain={[0.5, 1]} />
      <LineChart title="Share of answers accepted (rolling hour)" series={accepted} format={(v) => `${Math.round(v * 100)}%`} />

      {vendorView && (
        <>
          <h3>Your workers</h3>
          <p className="muted">Agreement and rejections are live. Gold accuracy appears once a worker has 20 gold results.</p>
          <table>
            <thead>
              <tr>
                <th>Worker</th>
                <th>Answers</th>
                <th>Agreement</th>
                <th>Rejected</th>
                <th>Gold accuracy</th>
              </tr>
            </thead>
            <tbody>
              {workers.map((w) => (
                <tr key={w.worker_id}>
                  <td>
                    {w.display_name} <span className="mono muted">{w.handle}</span>
                  </td>
                  <td>{w.submissions}</td>
                  <td>{pct(w.agreement)}</td>
                  <td>{w.rejected}</td>
                  <td>{w.gold_accuracy == null ? <span className="muted">not yet</span> : `${pct(w.gold_accuracy)} of ${w.gold_n}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <EnvironmentWorkTable />
    </section>
  );
}

/** Environment work on the scorecard (VP3, TO7): how a vendor's versions fare in the sandbox, in review and in training. */
function EnvironmentWorkTable() {
  const [rows, setRows] = useState<EnvironmentWork[]>([]);
  useEffect(() => {
    api<EnvironmentWork[]>("/vendors/environment-work").then(setRows);
  }, []);
  if (rows.length === 0) return null;
  return (
    <>
      <h3>Environment work</h3>
      <p className="muted">Across all briefs, computed the same way for every vendor.</p>
      <table>
        <thead>
          <tr>
            <th>Vendor</th>
            <th>Versions submitted</th>
            <th>Passed the sandbox's checks</th>
            <th>Approved in review</th>
            <th>Sent back</th>
            <th>Quarantined</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.vendor_id}>
              <td>{r.vendor_name}</td>
              <td>{r.submitted}</td>
              <td>{r.passed_checks} of {r.checked} ({pct(r.check_pass_rate)})</td>
              <td>{r.approved} of {r.reviewed} ({pct(r.approval_rate)})</td>
              <td>{r.sent_back}</td>
              <td>{r.quarantined}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.some((r) => r.episodes > 0) && (
        <>
          <h4>In training</h4>
          <p className="muted">
            What happened when training used each vendor's approved versions. Flagged attempts are the ones the hack detector
            flagged; a recall goes out when a version training used is quarantined.
          </p>
          <table>
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Episodes</th>
                <th>Solve rate</th>
                <th>Flagged attempts</th>
                <th>Recalls</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.vendor_id}>
                  <td>{r.vendor_name}</td>
                  <td>{r.episodes.toLocaleString()}</td>
                  <td>{pct(r.solve_rate)}</td>
                  <td>{r.flagged.toLocaleString()} ({pct(r.flagged_share)})</td>
                  <td>{r.recalls}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
