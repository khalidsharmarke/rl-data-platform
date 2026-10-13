import { useEffect, useState } from "react";
import { api, type AuditEvent, type Member, type Org, type User } from "./api";
import { ROLE_LABELS } from "./App";

const CAPABILITY_LABELS: Record<string, string> = {
  human_work: "Human work",
  environments: "Environments",
};

export default function Workspaces({ me }: { me: User }) {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const canReadAudit = me.role === "data_ops" || me.role === "vendor_admin";

  useEffect(() => {
    api<Org[]>("/orgs").then((list) => {
      setOrgs(list);
      setSelected((current) => current ?? list.find((o) => o.id === me.org_id)?.id ?? list[0]?.id ?? null);
    });
    if (canReadAudit) api<AuditEvent[]>("/audit?limit=15").then(setEvents);
  }, [me.org_id, canReadAudit]);

  useEffect(() => {
    if (selected) api<Member[]>(`/orgs/${selected}/members`).then(setMembers);
  }, [selected]);

  const orgName = (id: string | null) => orgs.find((o) => o.id === id)?.name ?? "—";

  return (
    <div className="stack">
      <section>
        <h2>Workspaces</h2>
        <p className="muted">
          {me.org_kind === "internal"
            ? "Anthropic staff see every workspace. Workers outside your own organization appear by handle only."
            : "You see your own workspace only."}
        </p>
        <div className="tabs">
          {orgs.map((o) => (
            <button key={o.id} className={o.id === selected ? "tab active" : "tab"} onClick={() => setSelected(o.id)}>
              {o.name}
              {o.capabilities.map((c) => (
                <span key={c} className="chip">
                  {CAPABILITY_LABELS[c] ?? c}
                </span>
              ))}
            </button>
          ))}
        </div>
        <table>
          <thead>
            <tr>
              <th>Handle</th>
              <th>Name</th>
              <th>Role</th>
            </tr>
          </thead>
          <tbody>
            {members.map((m) => (
              <tr key={m.id}>
                <td className="mono">{m.handle}</td>
                <td>{m.display_name ?? <span className="muted">hidden</span>}</td>
                <td>{ROLE_LABELS[m.role] ?? m.role}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {canReadAudit && (
        <section>
          <h2>Recent audit events</h2>
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Workspace</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{new Date(e.at).toLocaleString()}</td>
                  <td className="mono">{e.action}</td>
                  <td>{orgName(e.org_id)}</td>
                  <td>{e.actor_id ? (e.actor_id === me.id ? "You" : "Staff") : "System"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
