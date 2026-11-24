/**
 * Authentication is delegated to the Python API — the single source of truth for sessions and
 * permissions. A token is valid here exactly when `GET /api/v1/auth/me` accepts it, so logout,
 * suspension and permission changes apply to WebSockets too (re-checked periodically).
 */

import type { BusinessAccess } from "./permissions.js";

export interface Me {
  user: { id: string; email: string; full_name: string; is_owner: boolean };
  businesses: BusinessAccess[];
}

export class AuthError extends Error {}

export async function fetchMe(apiUrl: string, token: string, timeoutMs = 5000): Promise<Me> {
  const res = await fetch(`${apiUrl.replace(/\/$/, "")}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (res.status === 401 || res.status === 403) throw new AuthError("Session is not valid");
  if (!res.ok) throw new Error(`ERP API returned ${res.status}`);
  return (await res.json()) as Me;
}
