export type User = {
  id: string;
  display_name: string;
  handle: string;
  role: string;
  org_id: string;
  org_name: string;
  org_kind: "internal" | "vendor";
};

const TOKEN_KEY = "rl-token";

export function savedToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage can be unavailable (private windows); the session just won't survive a reload.
  }
}

export async function api<T>(path: string, init: RequestInit = {}, token = savedToken()): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (!response.ok) {
    const detail = await response.json().then((b) => b.detail, () => null);
    throw new Error(typeof detail === "string" ? detail : `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) return null as T;
  return response.json() as Promise<T>;
}

export type Org = {
  id: string;
  name: string;
  kind: "internal" | "vendor";
  capabilities: string[];
  domains: string[];
};

export type Member = {
  id: string;
  handle: string;
  role: string;
  display_name: string | null;
};

export type AuditEvent = {
  id: number;
  at: string;
  actor_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string;
  org_id: string | null;
};

export type Brief = {
  description: string;
  languages: string[];
  constraints: string[];
  difficulty_mix: Record<string, number>;
  review_rubric: string[];
};
export type Project = {
  id: string;
  name: string;
  kind: "human_data" | "environment";
  brief: Brief | null;
  task_type: string;
  current_schema_version: number;
  target_volume: number;
  overlap: number;
  status: string;
};

export type EnvironmentRow = {
  id: string; project_id: string; vendor_name: string; name: string; created_at: string;
  versions: number; latest_status: string | null;
};
export type Allocation = {
  id: string;
  vendor_id: string;
  vendor_name: string;
  contract_id: string;
  share: string;
  status: string;
  routed_tasks: number;
  submissions: number;
};

export type Contract = {
  id: string;
  vendor_id: string;
  kind: string;
  price: string;
  price_unit: string;
};

export type Assignment = {
  id: string;
  expires_at: string;
  task: {
    id: string;
    project_id: string;
    project_name: string;
    schema_version: number;
    instructions: string;
    payload: { prompt: string; response_a: string; response_b: string };
  };
};

export type QualityRow = {
  id: string;
  label: string;
  submissions: number;
  gold_n: number;
  gold_accuracy: number | null;
  compared: number;
  agreement: number | null;
  reviewed: number;
  rejected: number;
  accepted: number;
};

export type Quality = { metric_version: string; vendors: QualityRow[]; workers: QualityRow[] };

export type ReviewItem = {
  submission_id: string;
  vendor_name: string;
  worker_handle: string;
  submitted_at: string;
  payload: { prompt: string; response_a: string; response_b: string };
  answer: { choice: string; rationale?: string };
};

export type FeedbackItem = {
  submission_id: string;
  project_name: string;
  prompt: string;
  your_answer: { choice: string; rationale?: string };
  reason: string;
  reviewed_at: string;
};

export const pct = (x: number | null | undefined) => (x == null ? "—" : `${Math.round(x * 100)}%`);

export type VendorComparison = {
  vendor_id: string;
  vendor_name: string;
  submissions: number;
  accepted: number;
  acceptance_rate: number | null;
  gold_n: number;
  gold_accuracy: number | null;
  agreement: number | null;
  rejected: number;
  cost: number;
  cost_per_accepted: number | null;
  avg_turnaround_minutes: number | null;
  avg_time_on_task_s: number | null;
};

export type ScorePoint = {
  period_start: string;
  submissions: number;
  accepted: number;
  gold_n: number | null;
  gold_accuracy: number | null;
  agreement: number | null;
  cost_per_accepted: number | null;
};

export type Scorecard = {
  metric_version: string;
  vendor_name: string;
  gold_shown_until: string | null;
  points: ScorePoint[];
  alerts: { period_start: string; metric: string; recent: number; baseline: number }[];
};

export type WorkerQuality = {
  worker_id: string;
  handle: string;
  display_name: string | null;
  submissions: number;
  agreement: number | null;
  rejected: number;
  gold_accuracy: number | null;
  gold_n: number | null;
};

export type CaseSummary = {
  id: string;
  vendor_name: string;
  subject_type: string;
  subject_handle: string;
  subject_name: string | null;
  owner: string;
  status: string;
  opened_at: string;
  decision: string | null;
  decided_at: string | null;
  held: boolean;
  observations: string[];
};

export type CaseDetail = CaseSummary & {
  subject_id?: string;
  project_id?: string | null;
  decision_reason: string | null;
  flags: { observation: string; rule: string | null; rule_version: string | null }[];
  samples: { submission_id: string; prompt: string; answer: { choice: string; rationale?: string }; time_on_task_s: number; pasted: boolean }[];
  actions: { action: string; by: string; note: string | null; at: string }[];
};

export type Lineage = {
  worker_id: string;
  handle: string;
  vendor_name: string;
  submissions: number;
  held: number;
  removed: number;
  accepted_now: number;
  datasets: { dataset_id: string; created_at: string; rows_from_subject: number; current: boolean }[];
};

export type Dataset = {
  id: string; project_id: string; created_at: string; metric_version: string; row_count: number; provisional: number; supersedes: string | null;
};
export type Grade = {
  worker_id: string; handle: string; vendor_name: string; gold_n: number; gold_accuracy: number | null;
  interval: [number, number] | null; quality: "meets" | "below" | "uncertain";
  integrity: "suspended" | "held" | "watch" | "unproven" | "trusted"; later_gold_needed: number | null;
  watch_progress: [number, number] | null;
};
export type Escalation = {
  id: string; handle: string; vendor_name: string; rule: string; started_at: string; ended_at: string | null;
  outcome: "cleared" | "confirmed" | "case" | null; gold_since: number; gold_right_since: number;
};
export type Grades = { version: string; quality_bar: number; workers: Grade[] };
export type Release = {
  accepted: number; verified: number; provisional: number;
  waiting: { detection: number; gap: number; evidence: number };
  detection_through: number; as_of: string; release_after_minutes: number; release_cap_minutes: number;
};
