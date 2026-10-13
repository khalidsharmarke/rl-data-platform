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

export type Project = {
  id: string;
  name: string;
  task_type: string;
  current_schema_version: number;
  target_volume: number;
  overlap: number;
  status: string;
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
