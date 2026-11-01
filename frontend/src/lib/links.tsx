// Sealed page URLs. Identifiers in the address bar (district, planner run) are encrypted tokens
// issued by the backend (see backend/app/links.py), so a URL reveals nothing about the record it
// points to and cannot be edited by hand. Requests are batched and results cached for the session.

import { ReactNode, useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "./api";

export type LinkKind = "district" | "run";

const sealedCache = new Map<string, string>(); // "kind:id"    -> token
const openedCache = new Map<string, string>(); // "kind:token" -> id

type Pending = { kind: LinkKind; id: string; resolve: (t: string) => void; reject: (e: Error) => void };
let queue: Pending[] = [];
let flushScheduled = false;

function flush() {
  flushScheduled = false;
  const batch = queue;
  queue = [];
  if (!batch.length) return;
  const unique = Array.from(new Map(batch.map((p) => [`${p.kind}:${p.id}`, { kind: p.kind, id: p.id }])).values());
  api<{ tokens: string[] }>("/api/links/seal", { method: "POST", json: { items: unique } })
    .then(({ tokens }) => {
      unique.forEach((u, i) => {
        sealedCache.set(`${u.kind}:${u.id}`, tokens[i]);
        openedCache.set(`${u.kind}:${tokens[i]}`, u.id);
      });
      batch.forEach((p) => p.resolve(sealedCache.get(`${p.kind}:${p.id}`)!));
    })
    .catch((e) => batch.forEach((p) => p.reject(e as Error)));
}

export function seal(kind: LinkKind, id: string): Promise<string> {
  const hit = sealedCache.get(`${kind}:${id}`);
  if (hit) return Promise.resolve(hit);
  return new Promise((resolve, reject) => {
    queue.push({ kind, id, resolve, reject });
    if (!flushScheduled) {
      flushScheduled = true;
      setTimeout(flush, 0);
    }
  });
}

export async function open(kind: LinkKind, token: string): Promise<string> {
  const hit = openedCache.get(`${kind}:${token}`);
  if (hit) return hit;
  const { id } = await api<{ id: string }>("/api/links/open", { method: "POST", json: { kind, token } });
  openedCache.set(`${kind}:${token}`, id);
  sealedCache.set(`${kind}:${id}`, token);
  return id;
}

export async function pagePath(kind: LinkKind, id: string): Promise<string> {
  return `/${kind}/${await seal(kind, id)}`;
}

/** Sealed path for a record, or undefined until the backend has issued it. */
export function useSealedPath(kind: LinkKind, id?: string | null): string | undefined {
  const [path, setPath] = useState<string | undefined>(() => {
    const t = id ? sealedCache.get(`${kind}:${id}`) : undefined;
    return t ? `/${kind}/${t}` : undefined;
  });
  useEffect(() => {
    let live = true;
    if (!id) {
      setPath(undefined);
      return;
    }
    seal(kind, id).then((t) => live && setPath(`/${kind}/${t}`)).catch(() => undefined);
    return () => {
      live = false;
    };
  }, [kind, id]);
  return path;
}

/** `<Link>` to a district or run page. Renders as plain text for the instant before the token exists. */
export function SecureLink({ kind, id, children, className, title }: { kind: LinkKind; id: string; children: ReactNode; className?: string; title?: string }) {
  const to = useSealedPath(kind, id);
  if (!to) return <span className={className} title={title}>{children}</span>;
  return <Link to={to} className={className} title={title}>{children}</Link>;
}

/** Imperative navigation to a district or run page. */
export function useGo() {
  const nav = useNavigate();
  return useCallback(async (kind: LinkKind, id: string) => nav(await pagePath(kind, id)), [nav]);
}

/**
 * Resolve the `:token` route parameter to the real identifier.
 * A legacy plain link such as /district/chennai is upgraded to its sealed form in place.
 */
export function useRouteId(kind: LinkKind): { id: string | null; error: Error | null } {
  const { token } = useParams();
  const nav = useNavigate();
  const [state, setState] = useState<{ id: string | null; error: Error | null }>({ id: null, error: null });
  useEffect(() => {
    let live = true;
    setState({ id: null, error: null });
    if (!token) return;
    open(kind, token)
      .then((id) => live && setState({ id, error: null }))
      .catch(async (e) => {
        // Not a valid token: treat it as a legacy plain id and redirect to the sealed address.
        try {
          const sealed = await seal(kind, token);
          if (live) nav(`/${kind}/${sealed}`, { replace: true });
        } catch {
          if (live) setState({ id: null, error: e as Error });
        }
      });
    return () => {
      live = false;
    };
  }, [kind, token, nav]);
  return state;
}
