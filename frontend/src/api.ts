// API client: same-origin requests, session cookie, CSRF header on mutations.
import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error {
  status: number;
  data: any;
  constructor(status: number, message: string, data: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function csrf(): string {
  const m = document.cookie.match(/(?:^|;\s*)ivp_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData; headers?: Record<string, string>; quiet401?: boolean } = {}): Promise<T> {
  const method = opts.method || (opts.body || opts.form ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json", ...(opts.headers || {}) };
  if (method !== "GET") headers["X-CSRF-Token"] = csrf();
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, { method, headers, body, credentials: "same-origin" });
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    if (res.status === 401 && !opts.quiet401 && onUnauthorized) onUnauthorized();
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : detail?.message || (data?.errors ? data.errors.map((e: any) => `${e.field}: ${e.message}`).join("; ") : `Request failed (${res.status})`);
    throw new ApiError(res.status, msg, data);
  }
  return data as T;
}

export function qs(params: Record<string, any>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function useApi<T = any>(path: string | null, deps: any[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!!path);
  const seq = useRef(0);
  const load = useCallback(async (silent = false) => {
    if (!path) return;
    const n = ++seq.current;
    if (!silent) setLoading(true);
    try {
      const d = await api<T>(path);
      if (n === seq.current) {
        setData(d);
        setError(null);
      }
    } catch (e: any) {
      if (n === seq.current) setError(e.message || "Failed to load");
    } finally {
      if (n === seq.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    load();
  }, [load, ...deps]);
  return { data, error, loading, reload: load, setData };
}

/** Poll while a condition holds (e.g. background job running). */
export function usePoll(active: boolean, fn: () => void, ms = 2500) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [active, ms]);
}

export async function download(path: string) {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    const d = await res.json().catch(() => null);
    throw new Error(d?.detail || `Download failed (${res.status})`);
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || "download";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function idempotencyKey() {
  return (crypto as any).randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
