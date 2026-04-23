// MOCA Library — Manage screen (dashboard + document list)

const manageStyles = {
  wrap: { padding: "24px 28px 48px", maxWidth: 1280, margin: "0 auto" },
  heroRow: { display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 20 },
  h1: { fontSize: 24, fontWeight: 700, letterSpacing: "-0.015em", margin: 0 },
  sub: { color: "var(--fg2)", fontSize: 13, marginTop: 4 },

  statsGrid: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 },
  stat: {
    background: "var(--card)", border: "1px solid var(--border)",
    borderRadius: "var(--radius)", padding: 16,
    display: "flex", alignItems: "center", gap: 14,
  },
  statIcon: (accent) => ({
    width: 42, height: 42, borderRadius: "var(--radius)",
    background: accent ? "oklch(0.79 0.18 70.67 / 0.15)" : "var(--muted)",
    color: accent ? "var(--accent)" : "var(--fg1)",
    display: "flex", alignItems: "center", justifyContent: "center",
  }),
  statLb: { fontSize: 11, textTransform: "uppercase", letterSpacing: "0.08em", color: "var(--fg2)", fontWeight: 500 },
  statV: { fontSize: 22, fontWeight: 700, marginTop: 2, lineHeight: 1 },
  statDelta: { fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--fg2)", marginTop: 4 },

  twoCol: { display: "grid", gridTemplateColumns: "1fr 340px", gap: 16 },

  panel: { background: "var(--card)", border: "1px solid var(--border)", borderRadius: "var(--radius)" },
  panelHead: {
    display: "flex", alignItems: "center", gap: 12,
    padding: "12px 16px", borderBottom: "1px solid var(--border)",
  },
  panelTitle: { fontSize: 13, fontWeight: 600 },

  toolbar: { display: "flex", gap: 8, alignItems: "center", padding: "10px 16px", borderBottom: "1px solid var(--border)" },

  row: {
    display: "grid",
    gridTemplateColumns: "24px 28px 1fr 140px 110px 130px 24px",
    gap: 12, alignItems: "center",
    padding: "10px 16px",
    borderBottom: "1px solid var(--border)",
    fontSize: 13,
  },
  rowLast: { borderBottom: 0 },
  checkbox: (on) => ({
    width: 16, height: 16, borderRadius: 4,
    border: on ? "0" : "1.5px solid oklch(0.55 0 0)",
    background: on ? "var(--accent)" : "transparent",
    display: "flex", alignItems: "center", justifyContent: "center",
    cursor: "pointer",
  }),
  docIcon: {
    width: 24, height: 24, borderRadius: 4, background: "var(--muted)",
    display: "flex", alignItems: "center", justifyContent: "center",
    color: "var(--fg2)",
  },
  meta: { fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg2)" },
  status: (kind) => {
    const map = {
      ok:   { bg: "oklch(0.79 0.18 70.67 / 0.1)", fg: "var(--accent)",      dot: "var(--accent)" },
      proc: { bg: "var(--muted)",                  fg: "var(--fg2)",         dot: "var(--fg2)"    },
      fail: { bg: "oklch(0.7 0.19 22 / 0.14)",     fg: "oklch(0.75 0.19 22)", dot: "oklch(0.75 0.19 22)" },
      q:    { bg: "transparent",                   fg: "var(--fg3)",          dot: "var(--fg3)", border: "1px solid var(--border)" },
    };
    const s = map[kind] || map.ok;
    return {
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "3px 9px", borderRadius: 999,
      fontSize: 11, background: s.bg, color: s.fg,
      border: s.border || "0",
    };
  },
  dot: (color) => ({ width: 6, height: 6, borderRadius: "50%", background: color }),
  progBar: { height: 3, background: "var(--muted)", borderRadius: 2, marginTop: 6, overflow: "hidden" },
  progFill: (pct) => ({ height: "100%", width: pct + "%", background: "var(--accent)", transition: "width 400ms ease-out" }),

  // jobs side panel
  job: { padding: "10px 14px", borderBottom: "1px solid var(--border)" },
  jobTop: { display: "flex", alignItems: "center", gap: 8, marginBottom: 4 },
  jobName: { fontSize: 12.5, fontWeight: 500, flex: 1, minWidth: 0 },
  jobPhase: { fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--fg2)" },
};

const DOCS = [
  { id: "1", name: "Q3-2025-financials.pdf",    size: "2.4 MB",  date: "Oct 14", coll: "Finance",  status: "ok",   entities: 142, pct: 100 },
  { id: "2", name: "research-notes-march.md",   size: "48 KB",   date: "Oct 14", coll: "Research", status: "proc", entities: null, pct: 62 },
  { id: "3", name: "vendor-contract-acme.docx", size: "312 KB",  date: "Oct 13", coll: "Legal",    status: "ok",   entities: 27,  pct: 100 },
  { id: "4", name: "board-minutes-2025-10.pdf", size: "890 KB",  date: "Oct 12", coll: "Legal",    status: "fail", entities: null, pct: 0 },
  { id: "5", name: "competitor-landscape.md",   size: "124 KB",  date: "Oct 12", coll: "Research", status: "ok",   entities: 84,  pct: 100 },
  { id: "6", name: "eu-market-analysis.pdf",    size: "1.7 MB",  date: "Oct 11", coll: "Finance",  status: "ok",   entities: 198, pct: 100 },
  { id: "7", name: "onboarding-v2.pdf",         size: "560 KB",  date: "Oct 11", coll: "Research", status: "q",    entities: null, pct: 0 },
];

const JOBS = [
  { name: "research-notes-march.md", phase: "Extracting entities", pct: 62 },
  { name: "onboarding-v2.pdf",        phase: "Queued",              pct: 0 },
  { name: "partner-deck.pdf",         phase: "Chunking",            pct: 18 },
];

function StatusChip({ kind, pct }) {
  const label = { ok: "Completed", proc: `${pct}%`, fail: "Failed", q: "Queued" }[kind];
  return (
    <span style={manageStyles.status(kind)}>
      <span style={manageStyles.dot(manageStyles.status(kind).color)} />
      {label}
    </span>
  );
}

function Stat({ icon, label, value, delta, accent }) {
  const Ic = window.Icon[icon];
  return (
    <div style={manageStyles.stat}>
      <div style={manageStyles.statIcon(accent)}><Ic className="icon icon-lg" /></div>
      <div style={{ minWidth: 0 }}>
        <div style={manageStyles.statLb}>{label}</div>
        <div style={manageStyles.statV}>{value}</div>
        {delta && <div style={manageStyles.statDelta}>{delta}</div>}
      </div>
    </div>
  );
}

function DocRow({ doc, selected, onToggle }) {
  return (
    <div style={manageStyles.row}>
      <div style={manageStyles.checkbox(selected)} onClick={onToggle}>
        {selected && <window.Icon.Check className="icon" style={{ width: 11, height: 11, stroke: "var(--accent-fg)", strokeWidth: 3 }} />}
      </div>
      <div style={manageStyles.docIcon}><window.Icon.FileText className="icon icon-sm" /></div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 500 }} className="truncate">{doc.name}</div>
        <div style={manageStyles.meta}>
          {doc.size} · {doc.date} · <span style={{ color: "var(--accent)" }}>{doc.coll}</span>
          {doc.entities != null && <> · {doc.entities} entities</>}
        </div>
        {doc.status === "proc" && (
          <div style={manageStyles.progBar}><div style={manageStyles.progFill(doc.pct)} /></div>
        )}
      </div>
      <div style={manageStyles.meta}>{doc.coll}</div>
      <StatusChip kind={doc.status} pct={doc.pct} />
      <div style={manageStyles.meta}>{doc.date}</div>
      <button className="btn btn-icon btn-ghost"><window.Icon.MoreH /></button>
    </div>
  );
}

function JobRow({ job }) {
  return (
    <div style={manageStyles.job}>
      <div style={manageStyles.jobTop}>
        <window.Icon.FileText className="icon icon-sm" style={{ stroke: "var(--fg2)" }} />
        <div style={manageStyles.jobName} className="truncate">{job.name}</div>
        <span style={manageStyles.jobPhase}>{job.pct}%</span>
      </div>
      <div style={manageStyles.jobPhase}>{job.phase}</div>
      {job.pct > 0 && (
        <div style={{ ...manageStyles.progBar, marginTop: 8 }}>
          <div style={manageStyles.progFill(job.pct)} />
        </div>
      )}
    </div>
  );
}

function ManageScreen() {
  const [selected, setSelected] = React.useState(new Set(["1"]));
  const [filter, setFilter] = React.useState("all");

  const toggle = (id) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const filtered = DOCS.filter(d => {
    if (filter === "all") return true;
    return d.status === filter;
  });

  return (
    <div style={manageStyles.wrap}>
      <div style={manageStyles.heroRow}>
        <div>
          <h1 style={manageStyles.h1}>Library</h1>
          <div style={manageStyles.sub}>Ingest documents. Watch them become a graph.</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn btn-outline btn-sm"><window.Icon.Refresh className="icon icon-sm" /> Refresh</button>
          <button className="btn btn-primary"><window.Icon.Upload className="icon icon-sm" /> Ingest</button>
        </div>
      </div>

      <div style={manageStyles.statsGrid}>
        <Stat icon="FileText"      label="Documents"  value="1,284" delta="+18 today" />
        <Stat icon="Network"       label="Entities"   value="27,501" delta="+312 today" accent />
        <Stat icon="GitBranch"     label="Relations"  value="94,218" delta="+1,042 today" />
        <Stat icon="Clock"         label="Avg ingest" value="4.2s"   delta="p95 · 11.8s" />
      </div>

      <div style={manageStyles.twoCol}>
        <section style={manageStyles.panel}>
          <div style={manageStyles.panelHead}>
            <div style={manageStyles.panelTitle}>Documents</div>
            <span style={{ ...manageStyles.meta, color: "var(--fg2)" }}>
              {selected.size ? `${selected.size} selected` : `${filtered.length} total`}
            </span>
            <div style={{ flex: 1 }} />
            <button className="btn btn-ghost btn-sm"><window.Icon.Filter className="icon icon-sm" /> Filter</button>
          </div>
          <div style={manageStyles.toolbar}>
            {["all", "ok", "proc", "fail", "q"].map(f => {
              const labels = { all: "All", ok: "Completed", proc: "Processing", fail: "Failed", q: "Queued" };
              return (
                <span
                  key={f}
                  className={"chip chip-pill" + (filter === f ? " chip-active" : "")}
                  onClick={() => setFilter(f)}
                  style={{ cursor: "pointer" }}
                >
                  {labels[f]}
                </span>
              );
            })}
            <div style={{ flex: 1 }} />
            {selected.size > 0 && (
              <>
                <button className="btn btn-ghost btn-sm"><window.Icon.Link className="icon icon-sm" /> Merge</button>
                <button className="btn btn-ghost btn-sm" style={{ color: "oklch(0.75 0.19 22)" }}>
                  <window.Icon.Trash className="icon icon-sm" /> Delete
                </button>
              </>
            )}
          </div>
          <div>
            {filtered.map(d => (
              <DocRow key={d.id} doc={d} selected={selected.has(d.id)} onToggle={() => toggle(d.id)} />
            ))}
          </div>
        </section>

        <section style={manageStyles.panel}>
          <div style={manageStyles.panelHead}>
            <div style={manageStyles.panelTitle}>Active jobs</div>
            <span className="chip chip-pill" style={{ marginLeft: "auto", background: "oklch(0.79 0.18 70.67 / 0.12)", color: "var(--accent)" }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent)" }} />
              {JOBS.filter(j => j.pct > 0 && j.pct < 100).length} running
            </span>
          </div>
          {JOBS.map((j, i) => <JobRow key={i} job={j} />)}
          <div style={{ padding: 14 }}>
            <button className="btn btn-outline btn-sm" style={{ width: "100%" }}>View all jobs</button>
          </div>
        </section>
      </div>
    </div>
  );
}

window.ManageScreen = ManageScreen;
