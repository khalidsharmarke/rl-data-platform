import { useEffect, useState } from "react";
import { api, type ApiToken, type AuditEvent, type User } from "./api";

const SCOPES: Record<string, string> = {
  environments: "Environments: create environments and submit versions to your briefs",
  read: "Read: your scorecards, cases, briefs and versions",
  registry: "Registry: docker login, then push and pull your own images",
};

const when = (t: string | null) => (t ? new Date(t).toLocaleString() : "—");

/** API tokens (VM2, D45): credentials for a vendor's own tooling, never for a person. */
export default function Tokens({ me }: { me: User }) {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["environments", "read", "registry"]);
  const [created, setCreated] = useState<ApiToken | null>(null);
  const [activity, setActivity] = useState<{ token: ApiToken; events: AuditEvent[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isAdmin = me.role === "vendor_admin";

  const load = () => api<ApiToken[]>("/tokens").then(setTokens);
  useEffect(() => {
    load();
  }, []);

  async function create() {
    setError(null);
    try {
      setCreated(await api<ApiToken>("/tokens", { method: "POST", body: JSON.stringify({ name, scopes }) }));
      setName("");
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  async function revoke(t: ApiToken) {
    await api(`/tokens/${t.id}/revoke`, { method: "POST" });
    load();
  }

  async function showActivity(t: ApiToken) {
    setActivity({ token: t, events: await api<AuditEvent[]>(`/audit?token_id=${t.id}&limit=200`) });
  }

  return (
    <section>
      <h2>API tokens</h2>
      <p className="muted">
        {isAdmin
          ? "Tokens are for your organization's tools, such as a CI that submits environments or a script that reads your scorecard. A token acts for your vendor, never for a person, and everything it does is recorded under its name. Tokens expire after 90 days: to rotate one, create a new token, move your tools to it, then revoke the old one."
          : "Every vendor's tokens. Revoke a token that has leaked, then read everything it did."}
      </p>

      {isAdmin && (
        <div className="review-form">
          <label className="field">
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. builder-ci" />
          </label>
          {Object.entries(SCOPES).map(([s, label]) => (
            <label key={s} className="toggle">
              <input
                type="checkbox"
                checked={scopes.includes(s)}
                onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))}
              />{" "}
              {label}
            </label>
          ))}
          <div className="row">
            <button className="primary" disabled={!name || scopes.length === 0} onClick={create}>
              Create token
            </button>
            {error && <span className="error">{error}</span>}
          </div>
        </div>
      )}

      {created?.secret && (
        <div className="alert" role="status">
          <p>
            <strong>{created.name}</strong>: copy this token now. It won't be shown again.
          </p>
          <p className="mono">{created.secret}</p>
          <button onClick={() => setCreated(null)}>I've copied it</button>
        </div>
      )}

      <table>
        <thead>
          <tr>
            {!isAdmin && <th>Vendor</th>}
            <th>Name</th>
            <th>Starts with</th>
            <th>Scopes</th>
            <th>Last used</th>
            <th>Expires</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {tokens.map((t) => (
            <tr key={t.id} className={t.revoked_at ? "muted" : undefined}>
              {!isAdmin && <td>{t.org_name}</td>}
              <td>{t.name}</td>
              <td className="mono">{t.prefix}…</td>
              <td>{t.scopes.join(", ")}</td>
              <td>{when(t.last_used_at)}</td>
              <td>{t.revoked_at ? `Revoked ${when(t.revoked_at)}` : when(t.expires_at)}</td>
              <td className="row">
                <button onClick={() => showActivity(t)}>What it did</button>
                {!t.revoked_at && <button onClick={() => revoke(t)}>Revoke</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {tokens.length === 0 && <p className="muted">No tokens yet.</p>}

      {activity && (
        <>
          <h3>Everything {activity.token.name} did</h3>
          {activity.events.length === 0 ? (
            <p className="muted">Nothing recorded yet.</p>
          ) : (
            <table>
              <thead>
                <tr><th>When</th><th>Action</th><th>On</th></tr>
              </thead>
              <tbody>
                {activity.events.map((e) => (
                  <tr key={e.id}>
                    <td>{when(e.at)}</td>
                    <td className="mono">{e.action}</td>
                    <td className="mono">{e.entity_type} {e.entity_id.slice(0, 8)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}
