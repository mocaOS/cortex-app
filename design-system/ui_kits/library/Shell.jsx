// Cortex — Shell (Sidebar + Topbar + AppShell wrapper)

const shellStyles = {
  sidebar: {
    padding: "16px 10px",
    borderRight: "1px solid var(--border)",
    background: "oklch(0.17 0 0 / 0.6)",
    backdropFilter: "blur(24px)",
    WebkitBackdropFilter: "blur(24px)",
    display: "flex", flexDirection: "column", gap: 2,
    overflowY: "auto",
  },
  logoRow: {
    display: "flex", alignItems: "center", gap: 10,
    padding: "4px 12px 18px",
  },
  logoImg: { height: 20, width: "auto" },

  // Section header (Library / Organize / Workspace)
  section: {
    fontSize: 10.5, fontWeight: 500, letterSpacing: "0.08em",
    textTransform: "uppercase", color: "var(--fg3)",
    padding: "18px 12px 6px",
  },

  // Top-level nav item
  navItem: (active) => ({
    display: "flex", alignItems: "center", gap: 10,
    padding: "7px 12px",
    borderRadius: "var(--radius)",
    color: active ? "var(--accent-fg)" : "oklch(0.97 0 0)",
    background: active ? "var(--accent)" : "transparent",
    fontSize: 13, fontWeight: active ? 600 : 500,
    cursor: "pointer",
    transition: "background 150ms, color 150ms",
  }),
  navItemHover: {
    background: "oklch(0.27 0 0 / 0.6)",
  },
  count: (active) => ({
    marginLeft: "auto",
    fontFamily: "var(--font-mono)",
    fontSize: 11,
    color: active ? "oklch(0.20 0 0)" : "var(--fg2)",
    fontWeight: 500,
  }),

  // Collection sub-items — smaller, indented
  subItem: (active) => ({
    display: "flex", alignItems: "center", gap: 10,
    padding: "5px 12px 5px 30px",
    borderRadius: "var(--radius-md)",
    color: active ? "oklch(0.97 0 0)" : "oklch(0.80 0 0)",
    background: active ? "oklch(0.27 0 0)" : "transparent",
    fontSize: 12.5,
    cursor: "pointer",
    transition: "background 150ms, color 150ms",
  }),
  bullet: (color) => ({
    width: 6, height: 6, borderRadius: 2, background: color,
    border: "1px solid oklch(1 0 0 / 0.12)",
    flexShrink: 0,
  }),

  // Topbar
  topbar: {
    display: "flex", alignItems: "center", gap: 12,
    padding: "0 20px",
    borderBottom: "1px solid var(--border)",
    background: "oklch(0.15 0 0 / 0.65)",
    backdropFilter: "blur(24px)",
    WebkitBackdropFilter: "blur(24px)",
  },
  crumbs: {
    display: "flex", alignItems: "center", gap: 8, minWidth: 0,
  },
  crumbInactive: {
    fontSize: 12.5, color: "var(--fg2)", fontWeight: 500,
  },
  crumbSep: {
    fontSize: 12, color: "var(--fg3)",
  },
  crumbTitle: {
    fontSize: 15, fontWeight: 600, color: "oklch(0.97 0 0)",
    letterSpacing: "-0.005em",
  },
  meta: {
    fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg2)",
    marginLeft: 8,
  },

  search: {
    display: "flex", alignItems: "center", gap: 8,
    background: "oklch(0.22 0 0 / 0.8)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius)",
    padding: "6px 10px",
    width: 320, maxWidth: "35vw",
    color: "oklch(0.75 0 0)", fontSize: 12.5,
  },
  kbd: {
    fontFamily: "var(--font-mono)", fontSize: 10,
    background: "oklch(0.30 0 0)", color: "oklch(0.82 0 0)",
    padding: "1px 5px", borderRadius: 3, marginLeft: "auto",
    letterSpacing: "0.04em",
  },
  avatar: {
    width: 28, height: 28, borderRadius: "50%",
    background: "linear-gradient(135deg, oklch(0.55 0 0), oklch(0.35 0 0))",
    border: "1px solid var(--border)",
    display: "flex", alignItems: "center", justifyContent: "center",
    fontSize: 11, fontWeight: 600, color: "oklch(0.98 0 0)",
  },
};

// Main navigation — mirrors the real Cortex app IA
const NAV_LIBRARY = [
  { id: "documents",     icon: "FileText", label: "Documents", count: "1,284" },
  { id: "graph",         icon: "Network",  label: "Knowledge Graph" },
  { id: "deduplicate",   icon: "Layers",   label: "Deduplicate", count: 38 },
];
const NAV_WORKSPACE = [
  { id: "ask",     icon: "MessageSquare", label: "Ask" },
];

// Collections become a separately-labeled sub-group
const COLLECTIONS = [
  { id: "all",       label: "All collections", count: 1284, color: "oklch(0.55 0 0)" },
  { id: "finance",   label: "Finance",         count: 124,  color: "oklch(0.65 0.11 155)" },
  { id: "research",  label: "Research",        count: 58,   color: "oklch(0.68 0.08 240)" },
  { id: "legal",     label: "Legal",           count: 31,   color: "oklch(0.78 0.14 60)"  },
  { id: "personal",  label: "Personal",        count: 42,   color: "oklch(0.79 0.18 70.67)" },
];

function NavGroup({ items, route, onRoute }) {
  return items.map(n => {
    const Ic = window.Icon[n.icon];
    const active = route === n.id;
    return (
      <div
        key={n.id}
        style={shellStyles.navItem(active)}
        onClick={() => onRoute(n.id)}
        onMouseOver={e => { if (!active) e.currentTarget.style.background = "oklch(0.27 0 0 / 0.6)"; }}
        onMouseOut={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
      >
        <Ic />
        <span>{n.label}</span>
        {n.count != null && <span style={shellStyles.count(active)}>{n.count}</span>}
      </div>
    );
  });
}

function Sidebar({ route, onRoute, collection, onCollection }) {
  return (
    <aside className="sidebar" style={shellStyles.sidebar}>
      <div style={shellStyles.logoRow}>
        <img src="../../assets/logo.svg" alt="Cortex" style={shellStyles.logoImg} />
      </div>

      <div style={shellStyles.section}>Library</div>
      <NavGroup items={NAV_LIBRARY} route={route} onRoute={onRoute} />

      <div style={shellStyles.section}>Workspace</div>
      <NavGroup items={NAV_WORKSPACE} route={route} onRoute={onRoute} />

      <div style={shellStyles.section}>Collections</div>
      {COLLECTIONS.map(c => {
        const active = collection === c.id;
        return (
          <div
            key={c.id}
            style={shellStyles.subItem(active)}
            onClick={() => onCollection(c.id)}
            onMouseOver={e => { if (!active) e.currentTarget.style.background = "oklch(0.22 0 0)"; }}
            onMouseOut={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
          >
            <span style={shellStyles.bullet(c.color)} />
            <span>{c.label}</span>
            <span style={shellStyles.count(false)}>{c.count}</span>
          </div>
        );
      })}

      <div style={{ marginTop: "auto", paddingTop: 12, borderTop: "1px solid var(--border)" }}>
        <div
          style={shellStyles.navItem(false)}
          onMouseOver={e => e.currentTarget.style.background = "oklch(0.27 0 0 / 0.6)"}
          onMouseOut={e => e.currentTarget.style.background = "transparent"}
        >
          <window.Icon.Settings />
          <span>Settings</span>
        </div>
      </div>
    </aside>
  );
}

function Topbar({ crumbs = [], right }) {
  return (
    <header className="topbar" style={shellStyles.topbar}>
      <div style={shellStyles.crumbs}>
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <React.Fragment key={i}>
              <span style={last ? shellStyles.crumbTitle : shellStyles.crumbInactive}>{c.label}</span>
              {c.meta && <span style={shellStyles.meta}>{c.meta}</span>}
              {!last && <window.Icon.ChevronRight className="icon icon-sm" style={{ stroke: "var(--fg3)" }}/>}
            </React.Fragment>
          );
        })}
      </div>

      <div style={{ flex: 1 }} />

      <div style={shellStyles.search}>
        <window.Icon.Search className="icon icon-sm" />
        <span>Search entities, docs, jobs…</span>
        <span style={shellStyles.kbd}>⌘K</span>
      </div>

      {right}

      <button className="btn btn-icon btn-ghost" aria-label="Notifications">
        <window.Icon.Zap />
      </button>
      <div style={shellStyles.avatar}>EM</div>
    </header>
  );
}

function AppShell({ route, onRoute, collection, onCollection, crumbs, rightOfTopbar, children }) {
  return (
    <div className="app">
      <Sidebar route={route} onRoute={onRoute} collection={collection} onCollection={onCollection} />
      <Topbar crumbs={crumbs} right={rightOfTopbar} />
      <main className="main">{children}</main>
    </div>
  );
}

Object.assign(window, { Sidebar, Topbar, AppShell });
