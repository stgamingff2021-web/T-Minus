import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, apiUrl } from "./api";
import { Lang, STRINGS, StringKey } from "../i18n";
import { useLocalStorage } from "./hooks";

export type User = { name: string; role: "officer" | "admin" };
export type Toast = { id: number; kind: "info" | "warn" | "danger" | "ok"; title: string; body?: string; href?: string };

type Ctx = {
  user: User | null;
  login: (name: string, passcode: string) => Promise<void>;
  logout: () => void;
  requireLogin: () => boolean;
  loginOpen: boolean;
  setLoginOpen: (v: boolean) => void;
  theme: "dark" | "light";
  setTheme: (t: "dark" | "light") => void;
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (k: StringKey) => string;
  toasts: Toast[];
  toast: (t: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
};

const AppCtx = createContext<Ctx | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useLocalStorage<User | null>("tminus.user", null);
  const [theme, setTheme] = useLocalStorage<"dark" | "light">("tminus.theme", "dark");
  const [lang, setLang] = useLocalStorage<Lang>("tminus.lang", "en");
  const [loginOpen, setLoginOpen] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    document.documentElement.lang = lang === "ta" ? "ta" : "en";
  }, [lang]);

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((x) => x.id !== id)), []);
  const toast = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = Date.now() + Math.random();
      setToasts((ts) => [...ts.slice(-4), { ...t, id }]);
      window.setTimeout(() => dismiss(id), t.kind === "danger" ? 12000 : 7000);
    },
    [dismiss],
  );

  const login = useCallback(
    async (name: string, passcode: string) => {
      const r = await api<{ token: string; user: User }>("/api/auth/login", { method: "POST", json: { name, passcode } });
      localStorage.setItem("tminus.token", r.token);
      setUser(r.user);
      setLoginOpen(false);
      toast({ kind: "ok", title: `Signed in as ${r.user.name}`, body: r.user.role === "admin" ? "Admin: chaos switch enabled" : undefined });
    },
    [setUser, toast],
  );
  const logout = useCallback(() => {
    localStorage.removeItem("tminus.token");
    setUser(null);
  }, [setUser]);

  useEffect(() => {
    const h = () => setUser(null);
    window.addEventListener("tminus:logout", h);
    return () => window.removeEventListener("tminus:logout", h);
  }, [setUser]);

  const requireLogin = useCallback(() => {
    if (user && localStorage.getItem("tminus.token")) return true;
    setLoginOpen(true);
    return false;
  }, [user]);

  // System notifications (§7.4): PREPARE/ACT entries, disagreement, publications.
  useEffect(() => {
    const es = new EventSource(apiUrl("/api/notifications/stream"));
    es.addEventListener("notice", (m) => {
      const ev = JSON.parse((m as MessageEvent).data);
      if (ev.type === "level_change")
        toast({ kind: ev.level === "ACT" ? "danger" : "warn", title: `${ev.district} entered ${ev.level}`, body: `${ev.hazard} trigger · ${ev.action}`, href: `/district/${ev.district_id}` });
      else if (ev.type === "sources_disagree")
        toast({ kind: "warn", title: `Sources disagree · ${ev.district}`, body: (ev.notes || []).slice(-1)[0], href: `/district/${ev.district_id}` });
      else if (ev.type === "published") toast({ kind: "ok", title: "Alert published to the public page (exercise)", href: "/public" });
      else if (ev.type === "chaos") toast({ kind: "warn", title: `Chaos switch: ${ev.source} ${ev.enabled ? "OFF (simulated outage)" : "restored"}`, body: `by ${ev.by}` });
      else if (ev.type === "run_finished") toast({ kind: ev.status === "completed" ? "ok" : "warn", title: `Planner run ${ev.status}`, href: `/run/${ev.run_id}` });
      else if (ev.type === "scan_finished") window.dispatchEvent(new CustomEvent("tminus:scan", { detail: ev }));
    });
    return () => es.close();
  }, [toast]);

  const t = useCallback((k: StringKey) => STRINGS[lang][k] ?? STRINGS.en[k], [lang]);
  const value = useMemo(
    () => ({ user, login, logout, requireLogin, loginOpen, setLoginOpen, theme, setTheme, lang, setLang, t, toasts, toast, dismiss }),
    [user, login, logout, requireLogin, loginOpen, theme, setTheme, lang, setLang, t, toasts, toast, dismiss],
  );
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
}
