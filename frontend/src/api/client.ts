/** Low-level HTTP helper for the FastAPI backend. */

export const API_URL: string = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true";
export const WS_URL = API_URL.replace(/^http/, "ws") + "/ws";

/** Error carrying the HTTP status and the backend's `detail` message (e.g. 409 conflicts). */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// ---------- Demo identity (recorded in the audit trail via the X-User header; not authentication) ----------
const ACTOR_KEY = "dr.actor";
export function getActor(): string {
  try {
    return localStorage.getItem(ACTOR_KEY) || "Coordinator";
  } catch {
    return "Coordinator";
  }
}
export function setActor(name: string): void {
  try {
    localStorage.setItem(ACTOR_KEY, name.trim() || "Coordinator");
  } catch {
    /* storage unavailable: keep the default */
  }
}

export async function http<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...rest,
      headers: {
        ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
        "X-User": getActor(),
        ...headers,
      },
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    });
  } catch {
    // fetch() only throws when no HTTP response arrived: server down, wrong URL, or CORS blocked.
    throw new ApiError(0,
      `Cannot reach the backend at ${API_URL}. Start it with "cd backend && source .venv/bin/activate && ` +
      `uvicorn app.main:app --port 8000", or check VITE_API_URL and CORS_ORIGINS (this page is ${window.location.origin}).`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const post = <T,>(path: string, json?: unknown) => http<T>(path, { method: "POST", json: json ?? {} });
export const patch = <T,>(path: string, json: unknown) => http<T>(path, { method: "PATCH", json });
