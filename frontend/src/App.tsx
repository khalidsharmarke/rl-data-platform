import { useEffect, useState } from "react";
import Cases from "./Cases";
import Feedback from "./Feedback";
import Projects from "./Projects";
import Review from "./Review";
import Scorecards from "./Scorecards";
import Work from "./Work";
import { api, saveToken, savedToken, type User } from "./api";
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

  useEffect(() => {
    if (!savedToken()) return setChecked(true);
    api<User>("/auth/me")
      .then(setMe)
      .catch(() => saveToken(null))
      .finally(() => setChecked(true));
  }, []);

  if (!checked) return null;
  if (!me) return <DemoLogin onSignedIn={setMe} />;

  return (
    <main className="page">
      <header className="bar">
        <strong>RL Data Platform</strong>
        <span>
          {me.display_name} · {ROLE_LABELS[me.role] ?? me.role} · {me.org_name}
        </span>
        <button onClick={() => (saveToken(null), setMe(null))}>Sign out</button>
      </header>
      <Views me={me} />
    </main>
  );
}

function Views({ me }: { me: User }) {
  const tabs = [
    ...(me.role === "worker" ? ["Work", "Feedback"] : []),
    ...(me.role !== "worker" ? ["Projects"] : []),
    ...(["data_ops", "researcher", "vendor_admin"].includes(me.role) ? ["Scorecards"] : []),
    ...(me.role === "researcher" ? ["Review"] : []),
    ...(["data_ops", "vendor_admin"].includes(me.role) ? ["Cases"] : []),
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
      {tab === "Review" && <Review />}
      {tab === "Scorecards" && <Scorecards me={me} />}
      {tab === "Cases" && <Cases me={me} />}
      {tab === "Workspaces" && <Workspaces me={me} />}
    </>
  );
}

function DemoLogin({ onSignedIn }: { onSignedIn: (user: User) => void }) {
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
        <li><strong>Rae Okafor</strong> (researcher) → Review, and any <strong>worker</strong> → Work, to see the work itself.</li>
      </ol>
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
