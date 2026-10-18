import { useEffect, useState } from "react";
import Cases from "./Cases";
import Detection from "./Detection";
import VendorReview from "./VendorReview";
import Feedback from "./Feedback";
import Projects from "./Projects";
import Review from "./Review";
import Training from "./Training";
import Scorecards from "./Scorecards";
import Work from "./Work";
import { api, saveToken, savedToken, type User } from "./api";
import Onboarding from "./Onboarding";
import Tokens from "./Tokens";
import Workspaces from "./Workspaces";

export const ROLE_LABELS: Record<string, string> = {
  researcher: "Researcher",
  data_ops: "Data ops",
  vendor_admin: "Vendor admin",
  vendor_reviewer: "Vendor reviewer",
  worker: "Worker",
  environment_builder: "Environment builder",
};

export default function App() {
  const [me, setMe] = useState<User | null>(null);
  const [checked, setChecked] = useState(false);

  const [inviteError, setInviteError] = useState<string | null>(null);

  useEffect(() => {
    // An invite link signs its person in once (D46), then the code is gone from the address bar.
    const code = new URLSearchParams(window.location.search).get("invite");
    if (code) {
      window.history.replaceState(null, "", window.location.pathname);
      api<{ access_token: string; user: User }>("/auth/invites/accept", { method: "POST", body: JSON.stringify({ code }) }, null)
        .then((r) => (saveToken(r.access_token), setMe(r.user)))
        .catch((e) => setInviteError(String(e.message ?? e)))
        .finally(() => setChecked(true));
      return;
    }
    if (!savedToken()) return setChecked(true);
    api<User>("/auth/me")
      .then(setMe)
      .catch(() => saveToken(null))
      .finally(() => setChecked(true));
  }, []);

  if (!checked) return null;
  if (!me) return <DemoLogin onSignedIn={setMe} inviteError={inviteError} />;

  return (
    <main className="page">
      <header className="bar">
        <strong>RL Data Platform</strong>
        <span>
          {me.display_name} · {ROLE_LABELS[me.role] ?? me.role} · {me.org_name}
        </span>
        <button onClick={() => (saveToken(null), setMe(null))}>Sign out</button>
      </header>
      <Views key={me.id} me={me} />
    </main>
  );
}

function Views({ me }: { me: User }) {
  const onboarding = me.role === "vendor_admin" && !me.org_onboarded;
  const tabs = [
    ...(onboarding ? ["Onboarding"] : []),
    ...(me.role === "worker" ? ["Work", "Feedback"] : []),
    ...(me.role === "vendor_reviewer" ? ["Review before submitting"] : []),
    ...(me.role !== "worker" ? ["Projects"] : []),
    ...(["data_ops", "researcher", "vendor_admin"].includes(me.role) ? ["Scorecards"] : []),
    ...(me.role === "researcher" ? ["Review"] : []),
    ...(["data_ops", "researcher"].includes(me.role) ? ["Training"] : []),
    ...(["data_ops", "vendor_admin"].includes(me.role) ? ["Cases"] : []),
    ...(["data_ops", "researcher"].includes(me.role) ? ["Detection"] : []),
    ...(me.role === "data_ops" || (me.role === "vendor_admin" && !onboarding) ? ["Onboarding"] : []),
    ...(["data_ops", "vendor_admin"].includes(me.role) ? ["API tokens"] : []),
    "Workspaces",
  ];
  const [tab, setTab] = useState(tabs[0]);
  return (
    <>
      <nav className="tabs">
        {tabs.map((t) => (
          <button key={t} className={t === tab ? "tab active" : "tab"} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>
      {tab === "Work" && <Work />}
      {tab === "Feedback" && <Feedback />}
      {tab === "Projects" && <Projects me={me} />}
      {tab === "Review" && <Review me={me} />}
      {tab === "Review before submitting" && <VendorReview />}
      {tab === "Training" && <Training />}
      {tab === "Scorecards" && <Scorecards me={me} />}
      {tab === "Cases" && <Cases me={me} />}
      {tab === "Detection" && <Detection canChange={me.role === "data_ops"} />}
      {tab === "Onboarding" && <Onboarding me={me} />}
      {tab === "API tokens" && <Tokens me={me} />}
      {tab === "Workspaces" && <Workspaces me={me} />}
    </>
  );
}

function DemoLogin({ onSignedIn, inviteError }: { onSignedIn: (user: User) => void; inviteError: string | null }) {
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<User[]>("/auth/demo-users", {}, null).then(setUsers).catch((e) => setError(String(e)));
  }, []);

  async function signIn(user: User) {
    const result = await api<{ access_token: string; user: User }>(
      "/auth/demo-login",
      { method: "POST", body: JSON.stringify({ user_id: user.id }) },
      null,
    );
    saveToken(result.access_token);
    onSignedIn(result.user);
  }

  const orgs = [...new Set(users.map((u) => u.org_name))];

  return (
    <main className="page">
      <h1>RL Data Platform</h1>
      <p className="muted">Demo sign-in: pick a person to see the platform as them. After <code>make demo</code>, a good path is:</p>
      <ol className="guide">
        <li><strong>Sam Lindqvist</strong> (Anthropic data ops) → Scorecards: three vendors compared on the same measures, and Brightline's dip.</li>
        <li><strong>Grace Adeyemi</strong> (Brightline's admin) → Cases: what the platform observed about one of her workers, and her decision.</li>
        <li><strong>Sam</strong> again → Cases → the case: lineage, clean-up, and which datasets held the answers.</li>
        <li><strong>Rae</strong> → Projects → Task health: the broken tasks workers flagged, and who flags.</li>
        <li><strong>Sam</strong> → Detection: each rule's precision from reviewed flags, a new rule held in shadow, and what the audit sample found.</li>
        <li><strong>Rae Okafor</strong> (researcher) → Review, and any <strong>worker</strong> → Work, to see the work itself.</li>
        <li><strong>Hana Kowalski</strong> (Northwind's reviewer) → Review before submitting: the vendor's own check before answers reach Anthropic.</li>
        <li><strong>Sofia Lindgren</strong> (Kestrel's environment builder) → Projects: six environments and what the sandbox said about each.</li>
        <li><strong>Rae</strong> → Training → the second run: one environment's solve rate leaps as attempts get shorter.</li>
        <li><strong>Sam</strong> → Cases → "slugify edge cases": the hack detector's case, the recall, and training's rollback.</li>
        <li><strong>Sam</strong> → Cases → Kestrel Labs: a vendor case, Kestrel's response and the pause; Scorecards: how each vendor handles its own cases.</li>
        <li><strong>Sam</strong> → Onboarding: Harbor Labs, from invite to ready for real work. <strong>Ines Duarte</strong> (Harbor's admin) → Onboarding: the checklist and the commands for her own terminal.</li>
      </ol>
      {inviteError && <p className="error">{inviteError}</p>}
      {error && <p className="error">Couldn't load demo users: {error}</p>}
      <div className="orgs">
        {orgs.map((org) => (
          <section key={org} className="card">
            <h2>{org}</h2>
            <ul>
              {users
                .filter((u) => u.org_name === org)
                .map((u) => (
                  <li key={u.id}>
                    <button onClick={() => signIn(u)}>
                      {u.display_name}
                      <span className="role">{ROLE_LABELS[u.role] ?? u.role}</span>
                    </button>
                  </li>
                ))}
            </ul>
          </section>
        ))}
      </div>
    </main>
  );
}
