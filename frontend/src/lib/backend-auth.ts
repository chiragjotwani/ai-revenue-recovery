/**
 * Server-only backend API key (Phase 15: Security & Fintech Hardening).
 * The backend now rejects every request with no `X-API-Key` header
 * (`app.core.auth`) -- this frontend calls it server-to-server from
 * Server Components / Server Actions only, so a single operator-role key
 * (never exposed to the browser, never `NEXT_PUBLIC_*`) covers every
 * call site this app makes, matching the existing `API_BASE_URL`
 * convention (server-only env var, read at request time -- KI-001).
 */
export function backendAuthHeaders(): Record<string, string> {
  const apiKey = process.env.BACKEND_API_KEY;
  return apiKey ? { "X-API-Key": apiKey } : {};
}
