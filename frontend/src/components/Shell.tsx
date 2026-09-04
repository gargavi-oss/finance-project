import { useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import Brand from "./Brand";
import { useAuth } from "../contexts/AuthContext";
import { getHealth } from "../lib/api";

const NAV = [
  { to: "/app/live", label: "Live verification", caption: "Submit & analyse", icon: ScanIcon },
  { to: "/app/verdict", label: "Verdict reports", caption: "Inspect evidence", icon: ReportIcon },
  { to: "/app/ring", label: "Ring detection", caption: "Link fingerprints", icon: LinkIcon },
  { to: "/app/audit", label: "Audit trail", caption: "Review decisions", icon: HistoryIcon },
];

const PAGE_META: Record<string, [string, string]> = {
  "/app/live": ["Live verification", "Launch a six-agent investigation"],
  "/app/verdict": ["Verdict reports", "Review the complete evidence dossier"],
  "/app/ring": ["Ring detection", "Find linked documents across vendors"],
  "/app/audit": ["Audit trail", "Trace every analyst decision"],
};

export default function Shell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [health, setHealth] = useState<{ version: string; llm_provider: string; llm_model: string | null } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const meta = PAGE_META[location.pathname] ?? ["Control room", "Evidence operations"];

  useEffect(() => { getHealth().then(setHealth).catch(() => setHealth(null)); }, []);
  useEffect(() => { setMobileOpen(false); setSearchOpen(false); }, [location.pathname]);

  async function signOut() {
    await logout();
    navigate("/");
  }

  return (
    <div className="app-shell">
      <button className="mobile-menu" aria-label="Open navigation" onClick={() => setMobileOpen(true)}><MenuIcon /></button>
      {mobileOpen && <button className="mobile-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
      <aside className={`app-sidebar ${mobileOpen ? "is-open" : ""}`}>
        <div className="sidebar-brand"><Brand inverted to="/app/live" /><button className="mobile-close" onClick={() => setMobileOpen(false)}>×</button></div>
        <div className="workspace-selector">
          <span className="workspace-symbol">NX</span>
          <div><strong>Nexora Finance</strong><small>Production workspace</small></div>
          <span className="chevron">⌄</span>
        </div>
        <nav className="sidebar-nav" aria-label="Application navigation">
          <span className="nav-label">Investigation workspace</span>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => `sidebar-link ${isActive ? "active" : ""}`}>
              <item.icon /><span><strong>{item.label}</strong><small>{item.caption}</small></span><i>↗</i>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-system">
          <div className="system-line"><span className="live-dot" /><strong>System operational</strong></div>
          <dl><div><dt>Reasoning</dt><dd>{health?.llm_provider === "gemini" ? "Gemini" : health?.llm_provider ?? "Demo"}</dd></div><div><dt>Version</dt><dd>{health?.version ?? "2.0.4"}</dd></div></dl>
        </div>
        <div className="sidebar-user">
          <div className="avatar">{initials(user?.name)}</div>
          <div><strong>{user?.name}</strong><small>{user?.role}</small></div>
          <button onClick={signOut} aria-label="Sign out" title="Sign out"><LogoutIcon /></button>
        </div>
      </aside>

      <section className="app-stage">
        <header className="app-topbar">
          <div><span className="topbar-kicker">Evidence control room</span><h1>{meta[0]}</h1><p>{meta[1]}</p></div>
          <div className="topbar-actions">
            <button className="icon-button" aria-label="Search cases" onClick={() => setSearchOpen((v) => !v)}><SearchIcon /></button>
            <button className="button button-primary" onClick={() => navigate("/app/live")}>New investigation <span>＋</span></button>
          </div>
        </header>
        {searchOpen && (
          <div className="global-search">
            <SearchIcon /><input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search cases, vendors, or document IDs…" />
            <button onClick={() => setSearchOpen(false)}>Close</button>
            {query && <div className="search-hint">Open Verdict reports or Audit trail to locate “{query}”.</div>}
          </div>
        )}
        <main className="app-content">{children}</main>
        <footer className="app-footer"><span>DocForensic AI · Team Nexora</span><span>Model: {health?.llm_model ?? "deterministic fallback"} · Session protected</span></footer>
      </section>
    </div>
  );
}

function initials(name?: string) { return (name ?? "Analyst").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase(); }
function Icon({ children }: { children: React.ReactNode }) { return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{children}</svg>; }
function ScanIcon(){return <Icon><path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3"/><path d="M7 12h10"/></Icon>}
function ReportIcon(){return <Icon><path d="M6 3h8l4 4v14H6z"/><path d="M14 3v5h5M9 13h6M9 17h4"/></Icon>}
function LinkIcon(){return <Icon><path d="M10 13a4.5 4.5 0 0 0 6.4 0l2-2a4.5 4.5 0 0 0-6.4-6.4l-1 1"/><path d="M14 11a4.5 4.5 0 0 0-6.4 0l-2 2A4.5 4.5 0 0 0 12 19.4l1-1"/></Icon>}
function HistoryIcon(){return <Icon><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></Icon>}
function SearchIcon(){return <Icon><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></Icon>}
function LogoutIcon(){return <Icon><path d="M10 5H5v14h5M14 8l4 4-4 4M18 12H9"/></Icon>}
function MenuIcon(){return <Icon><path d="M4 7h16M4 12h16M4 17h16"/></Icon>}
