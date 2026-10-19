export type User = {
  id: string;
  display_name: string;
  handle: string;
  role: string;
  org_id: string;
  org_name: string;
  org_kind: "internal" | "vendor";
  org_onboarded: boolean;
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

/** A random id this browser keeps, sent with every request (AB12). The browser's own word, so untrusted. */
function deviceId(): string | null {
  try {
    let id = localStorage.getItem("rl-device");
    if (!id) localStorage.setItem("rl-device", (id = crypto.randomUUID()));
    return id;
  } catch {
    return null;
  }
}

export async function api<T>(path: string, init: RequestInit = {}, token = savedToken()): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const device = deviceId();
  if (device) headers.set("X-Device-Id", device);
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
  token_id: string | null;
};

export type ApiToken = {
  id: string;
  org_id: string;
  org_name: string;
  name: string;
  scopes: string[];
  prefix: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  secret?: string;
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
  onboarding: boolean;
};

export type EnvironmentRow = {
  id: string; project_id: string; vendor_name: string; name: string; created_at: string;
  versions: number; latest_status: string | null;
};
export type VersionFile = { path: string; part: "task" | "grader" | "reference" | "probe"; hash: string; size: number };
export type EnvVersion = {
  id: string; environment_id: string; number: number; status: string; created_at: string; status_changed_at: string;
  manifest: { base_image: string; vendor_image?: string; test_command: string; prepare_command: string | null; files: VersionFile[] };
};
export type VersionRow = {
  id: string; environment_id: string; environment_name: string; project_id: string; vendor_name: string;
  number: number; status: string; status_changed_at: string; batch: string | null;
};
export type CheckRun = { check_name: string; passed: boolean; outcome: string; detail: string; duration_ms: number; logs: string; created_at: string };
export type EnvReview = { decision: "approve" | "send_back"; rubric: Record<string, boolean>; feedback: string; reviewer: string; created_at: string };
export type FileDiff = { path: string; part: string; change: "added" | "removed" | "changed" | "unchanged"; diff: string[] | null };
export type VersionDiff = { version_id: string; against: string | null; against_number: number | null; files: FileDiff[] };
export type EnvProgress = { target: number; environments: number; approved: number; in_review: number; failed: number; in_progress: number };
export type Allocation = {
  id: string;
  vendor_id: string;
  vendor_name: string;
  contract_id: string;
  share: string;
  status: string;
  vendor_review: boolean;
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
    task_type: string;
    schema_version: number;
    instructions: string;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    payload: Record<string, any>;
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
  task_type: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: Record<string, any>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  answer: Record<string, any>;
};

export type FeedbackItem = {
  submission_id: string;
  project_name: string;
  task_type: string;
  prompt: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  your_answer: Record<string, any>;
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
  workers_gold_accuracy: number | null;
  vendor_reviewed: number;
  vendor_corrected: number;
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
  by_person: boolean;
  shared_at: string | null;
};

export type CaseDetail = CaseSummary & {
  subject_id?: string;
  project_id?: string | null;
  decision_reason: string | null;
  flags: { observation: string; rule: string | null; rule_version: string | null }[];
  samples: { submission_id: string; prompt: string; answer: { choice?: string; rationale?: string }; time_on_task_s: number; pasted: boolean;
    client_signals: { first_input_ms?: number; focus_changes?: number; content_viewed?: boolean } }[];
  api_signals: { claims_last_hour: number; most_claims_in_a_minute: number; sessions_last_day: number; devices_last_day: number;
    other_workers_on_those_devices: number } | null;
  actions: { action: string; by: string; note: string | null; at: string }[];
  environment: {
    version_id: string; environment_id: string; name: string; number: number; status: string;
    pulls: { puller: string; run_label: string | null; pulled_at: string }[];
  } | null;
  respond_by: string | null;
  can_act: boolean;
};

export type CaseHandling = {
  vendor_id: string; vendor_name: string; opened: number; decided: number; cleared: number; suspended_or_removed: number;
  reinstated: number; open_now: number; open_over_3_days: number; median_hours_to_decide: number | null;
};
export type VendorQueue = {
  waiting: number;
  items: { submission_id: string; project_name: string; worker_handle: string; worker_name: string; submitted_at: string;
           task_type: string; payload: Record<string, string>; answer: { choice: string; rationale?: string } }[];
};

export type TaskHealth = {
  broken: { task_id: string; payload: Record<string, string>; is_gold: boolean; broken_status: "paused" | "retired"; answers: number;
            flags: { reason: string; handle: string; at: string }[] }[];
  flaggers: { handle: string; vendor_name: string; flags: number; retired: number }[];
  agreement_alert: { batch_added_at: string; batch_tasks: number; agreement: number; others: number; n: number; others_n: number;
                     tasks: { task_id: string; payload: Record<string, string>; disagreements: number; broken_status: string | null }[] } | null;
};

export type RuleMeasure = {
  rule_version: string; detector: string; mode: "shadow" | "live"; bar: number | null; changed_at: string;
  findings: number; subjects: number; reviewed: number; abusive: number; precision: number | null; can_promote: boolean;
};

export type DetectionQuality = {
  rules: RuleMeasure[]; audited: number; audited_abusive: number; miss_rate: number | null; min_reviewed: number;
};

export type EnvironmentWork = {
  vendor_id: string; vendor_name: string; submitted: number; checked: number; passed_checks: number; reviewed: number;
  approved: number; sent_back: number; quarantined: number; check_pass_rate: number | null; approval_rate: number | null;
  episodes: number; solved: number; flagged: number; recalls: number; solve_rate: number | null; flagged_share: number | null;
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

export type RunRow = {
  id: string; label: string; first_seen_at: string; episodes: number; environments: number;
  last_step: number | null; last_episode_at: string | null;
};
export type StepPoint = { step: number; episodes: number; solve_rate: number | null; mean_length: number | null };
export type VersionStats = {
  version_id: string; environment_id: string; environment_name: string; vendor_name: string; number: number; status: string;
  episodes: number; solve_rate: number | null; mean_reward: number | null; reward_variance: number | null;
  mean_length: number | null; difficulty: string | null; series: StepPoint[];
};
export type RunDetail = { run: RunRow; processed_at: string | null; unprocessed: number; versions: VersionStats[] };
export type EpisodeRow = { episode_id: string; step: number; reward: number; passed: boolean; length: number; touched: string[]; received_at: string };
export type EpisodeDetail = EpisodeRow & { run_label: string; version_id: string; environment_name: string; vendor_name: string; transcript: string | null };
export type RecallRun = {
  run_label: string; first_step: number | null; last_step: number | null; episodes: number;
  flagged: number | null; first_flagged_step: number | null;
};
export type Recall = {
  id: number; version_id: string; environment_name: string; issued_at: string; runs: RecallRun[];
  reports: { run_label: string; action: "rolled_back" | "continued"; checkpoint_step: number | null; note: string | null; reported_at: string }[];
};
export type HackCheck = {
  id: string; version_id: string; environment_name: string; run_label: string; observation: string; created_at: string;
  episodes: { episode_id: string; step: number; reward: number; length: number; transcript: string | null }[];
};

export type ChecklistItem = { key: string; label: string; done: boolean; detail: string; at: string | null };
export type Onboarding = {
  org_id: string;
  org_name: string;
  capabilities: string[];
  items: ChecklistItem[];
  complete: boolean;
  invited_at: string | null;
  first_accepted_at: string | null;
  onboarded_at: string | null;
  namespace: string;
  registry_host: string | null;
};

export type GradeSpread = {
  gold_shown_until: string | null;
  quality: { meets: number; below: number; uncertain: number };
  integrity: { trusted: number; unproven: number; held: number; suspended: number };
};
