import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Bell, Building2, ChevronsLeft, ClipboardCheck, FileSearch, LayoutDashboard, LogOut, Menu, ScanSearch, UserCog, Users } from "lucide-react";
import { api, useApi, usePoll } from "../api";
import { useAuth } from "../auth";
import { Button, fmtDate, ThemeToggle } from "../ui";
import { AccountPanel, OfficersPanel } from "./SettingsPanels";
import { BrandLogo } from "./BrandLogo";

// Exactly five primary sections.
export const NAV = [
  { to: "/app", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/app/institutions", label: "Institutions", icon: Building2 },
  { to: "/app/verifier", label: "Institution Verifier", icon: FileSearch },
  { to: "/app/inspections", label: "Inspection & Analysis", icon: ScanSearch },
  { to: "/app/reports", label: "Reports & Audit", icon: ClipboardCheck },
];

// On serverless hosting the server cannot run background work on its own; while the app is open it asks the server
// to run queued work (document processing, AI analysis, re-checks) and backs off when there is nothing to do.
function useRequestDrivenJobs() {
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const loop = async (delay: number) => {
      timer = setTimeout(async () => {
        if (stop) return;
        let next = 15000;
        try {
          const r = await api("/api/jobs/drain", { method: "POST", quiet401: true });
          next = r.ran > 0 || r.pending > 0 ? 1500 : 15000;
        } catch { next = 20000; }
        if (!stop) loop(next);
      }, delay);
    };
    api("/api/health", { quiet401: true }).then((h) => { if (h.jobs_mode === "request" && !stop) loop(500); }).catch(() => {});
    return () => { stop = true; if (timer) clearTimeout(timer); };
  }, []);
}

export default function AppShell() {
  useRequestDrivenJobs();
  const { me, logout } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem("ivp-sidebar") === "collapsed";
    } catch {
      return false;
    }
  });
  const [mobileOpen, setMobileOpen] = useState(false);
  const [menu, setMenu] = useState<"" | "account" | "bell">("");
  const [panel, setPanel] = useState<"" | "account" | "officers">("");
  const notes = useApi<any>("/api/notifications");
  usePoll(true, () => notes.reload(true), 20000);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => setMobileOpen(false), [loc.pathname]);
  useEffect(() => {
    const close = (e: MouseEvent) => menuRef.current && !menuRef.current.contains(e.target as Node) && setMenu("");
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const toggleCollapse = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem("ivp-sidebar", c ? "open" : "collapsed");
      } catch {
        /* ignore */
      }
      return !c;
    });
  };
  const initials = (me?.officer.full_name || "?").split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className={`shell ${collapsed ? "collapsed" : ""} ${mobileOpen ? "mobile-open" : ""}`}>
      <aside className="sidebar" aria-label="Primary">
        <div className="brand">
          <BrandLogo size={32} style={{ borderRadius: 8, border: "1px solid rgba(133, 120, 97, 0.35)" }} />
          <span className="brand-text">
            <span className="brand-wordmark" style={{ fontSize: 22, lineHeight: 1 }}>Docura</span>
            <span className="tiny muted" style={{ display: "block", fontSize: 9.5, letterSpacing: "0.04em", textTransform: "uppercase" }}>Verifier</span>
          </span>
        </div>
        <nav className="stack sm" style={{ gap: 2 }}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-item ${isActive ? "active" : ""}`} title={collapsed ? n.label : undefined}>
              <n.icon size={18} />
              <span className="nav-label">{n.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot desktop-only">
          <button className="nav-item" style={{ all: "unset", cursor: "pointer", display: "flex", gap: 11, padding: "9px 10px", color: "var(--muted)" }} onClick={toggleCollapse} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            <ChevronsLeft size={18} style={{ transform: collapsed ? "rotate(180deg)" : undefined }} />
            <span className="nav-label small">Collapse</span>
          </button>
        </div>
      </aside>
      {mobileOpen && <div className="scrim" onClick={() => setMobileOpen(false)} />}
      <div className="main">
        <header className="topbar">
          <Button variant="ghost" icon={<Menu size={18} />} className="mobile-only" aria-label="Open navigation" onClick={() => setMobileOpen(true)} />
          <div className="grow" />
          <div className="row" ref={menuRef} style={{ position: "relative" }}>
            <ThemeToggle />
            <Button variant="ghost" icon={<Bell size={17} />} aria-label={`Notifications (${notes.data?.unread || 0} unread)`} onClick={() => setMenu(menu === "bell" ? "" : "bell")}>
              {notes.data?.unread ? <span className="badge bad" style={{ height: 18, padding: "0 6px" }}>{notes.data.unread}</span> : null}
            </Button>
            <button className="btn ghost" onClick={() => setMenu(menu === "account" ? "" : "account")} aria-haspopup="menu" aria-expanded={menu === "account"}>
              <span style={{ width: 28, height: 28, borderRadius: 99, background: "var(--brand-soft)", color: "var(--brand)", display: "grid", placeItems: "center", fontSize: 12, fontWeight: 700 }}>{initials}</span>
              <span className="desktop-only small" style={{ textAlign: "left", lineHeight: 1.2 }}>
                {me?.officer.full_name}
                <br />
                <span className="muted tiny">AICTE Officer</span>
              </span>
            </button>
            {menu === "account" && (
              <div className="menu" role="menu">
                <div className="small" style={{ padding: "6px 10px 8px" }}>
                  <div style={{ fontWeight: 600 }}>{me?.officer.full_name}</div>
                  <div className="muted break">{me?.officer.email}</div>
                </div>
                <div className="divider" />
                <button className="menu-item" role="menuitem" onClick={() => { setPanel("account"); setMenu(""); }}><UserCog size={16} /> Account & security</button>
                <button className="menu-item" role="menuitem" onClick={() => { setPanel("officers"); setMenu(""); }}><Users size={16} /> Officer accounts</button>
                <div className="divider" />
                <button className="menu-item" role="menuitem" onClick={async () => { await logout(); nav("/login"); }}><LogOut size={16} /> Sign out</button>
              </div>
            )}
            {menu === "bell" && (
              <div className="menu" style={{ width: 360, maxWidth: "calc(100vw - 24px)" }}>
                <div className="row between" style={{ padding: "4px 8px 8px" }}>
                  <strong className="small">Notifications</strong>
                  <Button size="sm" variant="ghost" onClick={async () => { await api("/api/notifications/read-all", { method: "POST" }); notes.reload(true); }}>Mark all read</Button>
                </div>
                <div style={{ maxHeight: 380, overflow: "auto" }}>
                  {!notes.data?.items?.length && <div className="muted small" style={{ padding: 12 }}>No notifications yet.</div>}
                  {notes.data?.items?.map((n: any) => (
                    <button key={n.id} className="menu-item" style={{ alignItems: "flex-start", opacity: n.read_at ? 0.65 : 1 }}
                      onClick={async () => { await api(`/api/notifications/${n.id}/read`, { method: "POST" }); setMenu(""); notes.reload(true); if (n.link) nav(n.link); }}>
                      <span className="dot" style={{ background: n.read_at ? "transparent" : "var(--accent)", marginTop: 6 }} />
                      <span className="stack sm" style={{ gap: 2 }}>
                        <span style={{ fontWeight: 600 }}>{n.title}</span>
                        {n.body && <span className="muted tiny">{n.body}</span>}
                        <span className="muted tiny">{fmtDate(n.created_at, true)}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </header>
        <main className="content" id="main">
          <Outlet />
        </main>
      </div>
      <AccountPanel open={panel === "account"} onClose={() => setPanel("")} />
      <OfficersPanel open={panel === "officers"} onClose={() => setPanel("")} />
    </div>
  );
}
