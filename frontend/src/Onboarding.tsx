import { useEffect, useState } from "react";
import { api, type Onboarding as State, type Org, type User } from "./api";

const when = (t: string | null) => (t ? new Date(t).toLocaleString() : "—");

/** Time between two moments, in simulated time when the demo clock runs fast. */
function took(from: string | null, to: string | null): string {
  if (!from || !to) return "—";
  const minutes = Math.round((Date.parse(to) - Date.parse(from)) / 60000);
  return minutes < 120 ? `${minutes} min` : `${(minutes / 60).toFixed(1)} h`;
}

/** Self-serve onboarding (VM3, D46): the vendor's checklist, or data ops' view of every vendor joining. */
export default function Onboarding({ me }: { me: User }) {
  return me.role === "data_ops" ? <DataOps /> : <Vendor me={me} />;
}

function Checklist({ state }: { state: State }) {
  return (
    <ul className="checklist">
      {state.items.map((i) => (
        <li key={i.key}>
          <span className={i.done ? "ok" : "muted"} aria-label={i.done ? "done" : "not yet"}>
            {i.done ? "✓" : "○"}
          </span>{" "}
          <strong>{i.label}</strong>
          <div className="muted small">{i.detail}</div>
        </li>
      ))}
    </ul>
  );
}

function Vendor({ me }: { me: User }) {
  const [state, setState] = useState<State | null>(null);

  useEffect(() => {
    const load = () => api<State>(`/orgs/${me.org_id}/onboarding`).then(setState);
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [me.org_id]);

  if (!state) return null;
  if (!state.invited_at && state.onboarded_at) {
    return (
      <section>
        <h2>Onboarding</h2>
        <p className="muted">{state.org_name} was set up before self-serve onboarding, and can take real work.</p>
      </section>
    );
  }
  const api_url = `${window.location.protocol}//${window.location.hostname}:8000`;
  const image = state.registry_host ? `${state.registry_host}/${state.namespace}/runtime:1` : null;
  return (
    <section>
      <h2>Onboarding</h2>
      <p className="muted">
        {state.complete
          ? `${state.org_name} is ready for real work. Anthropic's data ops can now allocate projects to you.`
          : "Prove your setup on samples before real work comes in. Each item ticks itself when the platform sees it happen; nothing here is self-reported, and the samples never count toward your scorecard."}
      </p>
      <Checklist state={state} />

      {state.capabilities.includes("environments") && (
        <>
          <h3>From your own terminal or CI</h3>
          <p className="muted">
            Create a token with the environments, read and registry scopes under API tokens, then run these. Your CI can run the
            same steps, and the last one waits until this checklist is complete.
          </p>
          <pre className="commands">{[
            `export RLP_TOKEN=<your token>`,
            `curl -sO ${api_url}/tools/rlp.py`,
            ...(image
              ? [
                  `echo "$RLP_TOKEN" | docker login ${state.registry_host} -u ci --password-stdin`,
                  `# your runtime: the platform's base image plus whatever your environments need`,
                  `printf 'FROM ${state.registry_host}/platform/base:1\\n' | docker build -t ${image} -`,
                  `docker push ${image}`,
                ]
              : []),
            `# a directory with task/, grader/, reference/ and manifest.json`,
            `python3 rlp.py submit ./my-environment${image ? ` --image ${image}` : ""}`,
            `python3 rlp.py onboarding --wait`,
          ].join("\n")}</pre>
        </>
      )}
    </section>
  );
}

function DataOps() {
  const [vendors, setVendors] = useState<State[]>([]);
  const [name, setName] = useState("");
  const [admin, setAdmin] = useState("");
  const [capabilities, setCapabilities] = useState<string[]>(["human_work", "environments"]);
  const [link, setLink] = useState<{ vendor: string; url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api<State[]>("/onboarding").then(setVendors);
  useEffect(() => {
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  async function create() {
    setError(null);
    try {
      const org = await api<Org>("/orgs", { method: "POST", body: JSON.stringify({ name, capabilities }) });
      const invite = await api<{ code: string }>(`/orgs/${org.id}/invites`, { method: "POST", body: JSON.stringify({ display_name: admin }) });
      setLink({ vendor: org.name, url: `${window.location.origin}/?invite=${invite.code}` });
      setName("");
      setAdmin("");
      load();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <section>
      <h2>Onboarding</h2>
      <p className="muted">
        Create a vendor and invite its admin. The vendor proves its setup on samples; real work can be allocated once its checklist is
        complete.
      </p>
      <div className="review-form">
        <label className="field">
          Vendor
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Harbor Labs" />
        </label>
        <label className="field">
          Admin's name
          <input value={admin} onChange={(e) => setAdmin(e.target.value)} />
        </label>
        {[
          ["human_work", "Human work"],
          ["environments", "Environments"],
        ].map(([c, label]) => (
          <label key={c} className="toggle">
            <input
              type="checkbox"
              checked={capabilities.includes(c)}
              onChange={(e) => setCapabilities(e.target.checked ? [...capabilities, c] : capabilities.filter((x) => x !== c))}
            />{" "}
            {label}
          </label>
        ))}
        <div className="row">
          <button className="primary" disabled={!name || !admin || capabilities.length === 0} onClick={create}>
            Create and invite
          </button>
          {error && <span className="error">{error}</span>}
        </div>
      </div>
      {link && (
        <div className="alert" role="status">
          <p>
            Send this link to {link.vendor}'s admin. It works once and expires in 7 days; the platform doesn't email it.
          </p>
          <p className="mono">{link.url}</p>
        </div>
      )}

      {vendors.length === 0 ? (
        <p className="muted">No vendor has come in through onboarding yet.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Checklist</th>
              <th>Invited</th>
              <th>To first accepted work</th>
              <th>To ready</th>
            </tr>
          </thead>
          <tbody>
            {vendors.map((v) => (
              <tr key={v.org_id}>
                <td>
                  {v.org_name}
                  {v.onboarded_at && <span className="chip">Ready for real work</span>}
                </td>
                <td>
                  {v.items.filter((i) => i.done).length} of {v.items.length}
                  {!v.complete && (
                    <div className="muted small">Next: {v.items.find((i) => !i.done)?.label}</div>
                  )}
                </td>
                <td>{when(v.invited_at)}</td>
                <td>{took(v.invited_at, v.first_accepted_at)}</td>
                <td>{took(v.invited_at, v.onboarded_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
