// Lightweight API client. Reads base URL + token from localStorage so the SPA
// can talk to the OpenKB REST API after the user configures the connection.

const LS_BASE = "openkb_api_base";
const LS_TOKEN = "openkb_token";

export function getApiBase() {
  return localStorage.getItem(LS_BASE) || "";
}

export function getToken() {
  return localStorage.getItem(LS_TOKEN) || "";
}

export function setConnection(apiBase, token) {
  localStorage.setItem(LS_BASE, apiBase);
  localStorage.setItem(LS_TOKEN, token);
}

export function hasConnection() {
  return !!getToken();
}

// Resolve the API base. In dev (Vite proxy) the relative "/api" paths are
// proxied; otherwise use the configured absolute base. Falls back to same-origin.
export function baseUrl() {
  return getApiBase().replace(/\/$/, "");
}

// Surface 401s to the UI so the connection modal can reopen for re-entry.
export function notifyUnauthorized() {
  window.dispatchEvent(new CustomEvent("openkb:unauthorized"));
}

export async function request(path, { method = "GET", body, headers } = {}) {
  const token = getToken();
  const finalHeaders = {
    ...(body && !(body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...headers,
  };
  const init = { method, headers: finalHeaders };
  if (body !== undefined) init.body = body instanceof FormData ? body : JSON.stringify(body);
  const res = await fetch(baseUrl() + path, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
    } catch {
      // keep default message
    }
  const err = new Error(msg);
  err.status = res.status;
  if (res.status === 401) notifyUnauthorized();
  throw err;
}
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

export const api = {
  listKbs: () => request("/api/v1/kbs"),
  initKb: (kb) => request("/api/v1/init", { method: "POST", body: { kb } }),
  status: (kb) => request("/api/v1/status", { method: "POST", body: { kb } }),
  list: (kb) => request("/api/v1/list", { method: "POST", body: { kb } }),
  lint: (kb, fix) => request("/api/v1/lint", { method: "POST", body: { kb, fix } }),
  watchStatus: (kb) => request("/api/v1/watch/status", { method: "POST", body: { kb } }),
  watchStart: (kb, debounce) =>
    request("/api/v1/watch/start", { method: "POST", body: { kb, debounce } }),
  watchStop: (kb) => request("/api/v1/watch/stop", { method: "POST", body: { kb } }),
  chatSessions: (kb) =>
    request("/api/v1/chat/sessions", { method: "POST", body: { kb } }),
  chatSessionLoad: (kb, sessionId) =>
    request("/api/v1/chat/sessions/load", { method: "POST", body: { kb, session_id: sessionId } }),
  chatSessionDelete: (kb, sessionId) =>
    request("/api/v1/chat/sessions/delete", { method: "POST", body: { kb, session_id: sessionId } }),
};
