import { ReactNode, useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { Activity, BookOpenText, History, Languages, LogIn, LogOut, Map, Megaphone, Moon, Radio, ScrollText, Sun } from "lucide-react";
import { useApp } from "../lib/app";
import { useApi } from "../lib/hooks";
import { ago, istTime } from "../lib/format";
import { LoginModal, Toasts } from "./ui";

function SourcesPill() {
  const { data } = useApi<any>("/api/sources", 20000);
  if (!data) return null;
  const relevant = data.sources.filter((s: any) => !["never fetched", "disabled"].includes(s.status));
  const bad = relevant.filter((s: any) => s.status === "failed").length;
  const sim = relevant.filter((s: any) => s.status === "simulated outage").length;
  const stale = relevant.filter((s: any) => s.status === "stale").length;
  const cls = bad ? "danger" : sim ? "violet" : stale ? "warn" : relevant.length ? "ok" : "";
  const label = bad ? `${bad} failed` : sim ? `${sim} simulated outage` : stale ? `${stale} stale` : relevant.length ? "all live" : "no fetches yet";
  return (
    <NavLink to="/sources" className={`pill ${cls}`} title="Sources panel">
      <Radio size={12} /> Sources: {label}
    </NavLink>
  );
}

function ScanChip() {
  const { data, reload } = useApi<any>("/api/health", 15000);
  useEffect(() => {
    const h = () => reload();
    window.addEventListener("tminus:scan", h);
    return () => window.removeEventListener("tminus:scan", h);
  }, [reload]);
  if (!data) return null;
  const ls = data.last_scan;
  return (
    <span className="pill" title={ls ? `Scan ${ls.id} · ${ls.status}` : "No scan yet"}>
      <Activity size={12} className={data.scan_running ? "spin" : ""} />
      {data.scan_running ? "Scanning…" : ls ? `Scan ${istTime(ls.finished_at).replace(" IST", "")} · ${ago(ls.finished_at)}` : "No scan yet"}
    </span>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout, setLoginOpen, theme, setTheme, lang, setLang, t } = useApp();
  const [clock, setClock] = useState(istTime(new Date().toISOString()));
  useEffect(() => {
    const id = window.setInterval(() => setClock(istTime(new Date().toISOString())), 15000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand" style={{ color: "inherit", textDecoration: "none" }}>
          <span className="logo">
            <svg width="18" height="18" viewBox="0 0 32 32" aria-hidden><path d="M5 8h22M16 8v18" stroke="#f97316" strokeWidth="4" strokeLinecap="round" /><circle cx="25" cy="24" r="3.5" fill="#ef4444" /></svg>
          </span>
          <span>
            <span className="word">T<b>–</b>MINUS</span>
            <span className="tag">forecasts → action, before impact</span>
          </span>
        </NavLink>
        <nav className="nav">
          <NavLink to="/" end><Map size={14} /> <span className="lbl">{t("map")}</span></NavLink>
          <NavLink to="/sources"><Radio size={14} /> <span className="lbl">{t("sources")}</span></NavLink>
          <NavLink to="/replay"><History size={14} /> <span className="lbl">{t("replay")}</span></NavLink>
          <NavLink to="/plans"><BookOpenText size={14} /> <span className="lbl">{t("plans")}</span></NavLink>
          <NavLink to="/public"><Megaphone size={14} /> <span className="lbl">{t("public")}</span></NavLink>
          {user && <NavLink to="/audit"><ScrollText size={14} /> <span className="lbl">{t("audit")}</span></NavLink>}
        </nav>
        <div className="row right" style={{ justifyContent: "flex-end", flexShrink: 0 }}>
          <ScanChip />
          <SourcesPill />
          <span className="pill mono hide-lg" title="Officer clock">{clock}</span>
          <button className="btn ghost sm" onClick={() => setLang(lang === "en" ? "ta" : "en")} title="English / தமிழ்">
            <Languages size={14} /> {lang === "en" ? "தமிழ்" : "EN"}
          </button>
          <button className="btn ghost sm" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title="Theme">
            {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
          </button>
          {user ? (
            <button className="btn sm" onClick={logout} title={t("logout")}>
              <LogOut size={14} /> {user.name} <span className="pill">{user.role}</span>
            </button>
          ) : (
            <button className="btn sm primary" onClick={() => setLoginOpen(true)}><LogIn size={14} /> {t("login")}</button>
          )}
        </div>
      </header>
      <main style={{ minHeight: 0 }}>{children}</main>
      <footer className="footer">
        <span><b>Prototype decision-support tool</b> — not an official warning source. Nothing is sent without officer approval.</span>
        <span>Weather data: Open-Meteo (CC BY 4.0)</span>
        <span>Flood: GloFAS v4 via Open-Meteo</span>
        <span>© OpenStreetMap contributors</span>
        <span>Routing: openrouteservice</span>
        <span>Official alerts: NDMA SACHET</span>
      </footer>
      <LoginModal />
      <Toasts />
    </div>
  );
}
