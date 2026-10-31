// API client: one place for base URL, auth header and the shared error shape.
//   { "error": { "code", "message", "source", "retryable" } }

const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;
  source?: string | null;
  constructor(status: number, code: string, message: string, retryable = false, source?: string | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.source = source;
  }
}

export function getToken(): string | null {
  return localStorage.getItem("tminus.token");
}

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(init.headers as Record<string, string>) };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { ...init, headers, body });
  } catch (e) {
    throw new ApiError(0, "network_error", "Cannot reach the T-Minus backend. Check your connection.", true);
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const err = data?.error;
    if (res.status === 401 && token) {
      localStorage.removeItem("tminus.token");
      window.dispatchEvent(new Event("tminus:logout"));
    }
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `HTTP ${res.status}`, !!err?.retryable, err?.source);
  }
  return data as T;
}
