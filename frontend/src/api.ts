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
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
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
