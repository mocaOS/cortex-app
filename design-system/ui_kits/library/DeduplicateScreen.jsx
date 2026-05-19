// Cortex — Deduplicate screen
// Review candidate duplicate clusters and merge/dismiss.

const dedupStyles = {
  wrap: { display: "grid", gridTemplateColumns: "320px 1fr", height: "100%", overflow: "hidden" },

  list: {
    borderRight: "1px solid var(--border)",
    background: "oklch(0.15 0 0)",
    display: "flex", flexDirection: "column", minHeight: 0,
  },
  listHead: { padding: "14px 16px", borderBottom: "1px solid var(--border)" },
  listTitle: { fontSize: 13, fontWeight: 600, color: "oklch(0.97 0 0)" },
  listSub: { fontSize: 11, color: "var(--fg2)", marginTop: 2, fontFamily: "var(--font-mono)" },

  tabs: { display: "flex", gap: 4, padding: "8px 12px", borderBottom: "1px solid var(--border)" },
  tab: (active) => ({
    padding: "5px 10px", borderRadius: "var(--radius)",
    fontSize: 12, fontWeight: 500,
    background: active ? "oklch(0.27 0 0)" : "transparent",
    color: active ? "oklch(0.97 0 0)" : "var(--fg2)",
    cursor: "pointer",
  }),

  clusters: { overflowY: "auto", flex: 1, padding: 8 },
  cluster: (active) => ({
    padding: "12px 14px",
    borderRadius: "var(--radius-md)",
    marginBottom: 4,
    background: active ? "oklch(0.22 0 0)" : "transparent",
    border: active ? "1px solid var(--border)" : "1px solid transparent",
    cursor: "pointer",
  }),
  clusterHead: { display: "flex", alignItems: "center", gap: 8, marginBottom: 6 },
  similarity: (score) => ({
    fontFamily: "var(--font-mono)", fontSize: 10.5, fontWeight: 600,
    padding: "2px 6px", borderRadius: 4,
    background: score >= 95 ? "oklch(0.35 0.08 30 / 0.4)" : "oklch(0.28 0 0)",
    color: score >= 95 ? "oklch(0.85 0.08 30)" : "oklch(0.85 0 0)",
    border: score >= 95 ? "1px solid oklch(0.45 0.1 30 / 0.5)" : "1px solid var(--border)",
  }),
  clusterTitle: {
    fontSize: 13, fontWeight: 500, color: "oklch(0.97 0 0)",
    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
  },
  clusterMeta: { fontSize: 11, color: "var(--fg2)", fontFamily: "var(--font-mono)" },

  // Right pane — diff view
  main: { display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" },
  mainHead: {
    padding: "16px 24px", borderBottom: "1px solid var(--border)",
    display: "flex", alignItems: "center", gap: 12,
  },
  mainTitle: { fontSize: 16, fontWeight: 600, color: "oklch(0.97 0 0)" },
  actions: { marginLeft: "auto", display: "flex", gap: 8 },

  body: { flex: 1, overflow: "auto", padding: 24 },
  cols: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 },
  col: {
    background: "var(--card)",
    border: "1px solid var(--border)",
    borderRadius: "var(--radius-lg)",
    padding: 20,
  },
  colBadge: (primary) => ({
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "3px 8px", borderRadius: "var(--radius)",
    fontSize: 11, fontFamily: "var(--font-mono)",
    background: primary ? "var(--accent)" : "oklch(0.27 0 0)",
    color: primary ? "var(--accent-fg)" : "oklch(0.85 0 0)",
    fontWeight: 600, marginBottom: 10,
  }),
  colTitle: { fontSize: 14, fontWeight: 600, color: "oklch(0.97 0 0)", marginBottom: 8, lineHeight: 1.35 },
  colMeta: {
    display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px",
    fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--fg2)",
    padding: "10px 0", borderTop: "1px solid var(--border)", borderBottom: "1px solid var(--border)",
    margin: "12px 0",
  },
  colMetaKey: { color: "var(--fg3)" },
  colMetaVal: { color: "oklch(0.90 0 0)" },
  snippet: { fontSize: 12.5, color: "oklch(0.85 0 0)", lineHeight: 1.55, marginTop: 8 },
  diff: { background: "oklch(0.35 0.08 140 / 0.25)", color: "oklch(0.92 0 0)", padding: "0 3px", borderRadius: 2 },

  entity: {
    display: "inline-flex", alignItems: "center", gap: 4,
    padding: "2px 8px", borderRadius: 999,
    fontSize: 11, fontFamily: "var(--font-mono)",
    background: "oklch(0.22 0 0)", color: "var(--fg1)",
    border: "1px solid var(--border)",
    marginRight: 4, marginBottom: 4,
  },
};

const DEDUP_CLUSTERS = [
  { id: "c1", similarity: 98, title: "Q3 2025 Financial Report", docs: 2, source: "Drive + Email" },
  { id: "c2", similarity: 96, title: "ACME acquisition — term sheet", docs: 3, source: "Slack + Drive" },
  { id: "c3", similarity: 94, title: "Board meeting notes · Oct 14", docs: 2, source: "Notion" },
  { id: "c4", similarity: 91, title: "Employee handbook v4.2", docs: 2, source: "Drive" },
  { id: "c5", similarity: 88, title: "Product roadmap H1 2026", docs: 4, source: "Notion + Drive" },
  { id: "c6", similarity: 85, title: "Customer interview — Stripe", docs: 2, source: "Granola" },
  { id: "c7", similarity: 83, title: "SOC 2 audit evidence", docs: 2, source: "Drive" },
];

function DeduplicateScreen() {
  const [tab, setTab] = React.useState("pending");
  const [selected, setSelected] = React.useState("c1");

  return (
    <div style={dedupStyles.wrap}>
      {/* Left list */}
      <div style={dedupStyles.list}>
        <div style={dedupStyles.listHead}>
          <div style={dedupStyles.listTitle}>Duplicate candidates</div>
          <div style={dedupStyles.listSub}>38 clusters · 84 documents</div>
        </div>

        <div style={dedupStyles.tabs}>
          {[
            { id: "pending", label: "Pending", count: 38 },
            { id: "merged",  label: "Merged",  count: 412 },
            { id: "ignored", label: "Ignored", count: 17 },
          ].map(t => (
            <div key={t.id} style={dedupStyles.tab(tab === t.id)} onClick={() => setTab(t.id)}>
              {t.label} <span style={{ color: "var(--fg3)", marginLeft: 4 }}>{t.count}</span>
            </div>
          ))}
        </div>

        <div style={dedupStyles.clusters}>
          {DEDUP_CLUSTERS.map(c => (
            <div
              key={c.id}
              style={dedupStyles.cluster(selected === c.id)}
              onClick={() => setSelected(c.id)}
            >
              <div style={dedupStyles.clusterHead}>
                <span style={dedupStyles.similarity(c.similarity)}>{c.similarity}%</span>
                <span style={dedupStyles.clusterMeta}>{c.docs} docs</span>
              </div>
              <div style={dedupStyles.clusterTitle}>{c.title}</div>
              <div style={dedupStyles.clusterMeta}>{c.source}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Right: diff view */}
      <div style={dedupStyles.main}>
        <div style={dedupStyles.mainHead}>
          <span style={dedupStyles.similarity(98)}>98% match</span>
          <div style={dedupStyles.mainTitle}>Q3 2025 Financial Report</div>
          <div style={dedupStyles.actions}>
            <button className="btn btn-ghost">
              <window.Icon.Eye /> Ignore
            </button>
            <button className="btn btn-secondary">
              <window.Icon.GitBranch /> Keep both
            </button>
            <button className="btn btn-primary">
              <window.Icon.Layers /> Merge into primary
            </button>
          </div>
        </div>

        <div style={dedupStyles.body}>
          <div style={dedupStyles.cols}>
            {/* Primary */}
            <div style={dedupStyles.col}>
              <div style={dedupStyles.colBadge(true)}>PRIMARY</div>
              <div style={dedupStyles.colTitle}>Q3 2025 Financial Report — Final.pdf</div>
              <div style={dedupStyles.colMeta}>
                <span style={dedupStyles.colMetaKey}>source</span>
                <span style={dedupStyles.colMetaVal}>Google Drive / Finance</span>
                <span style={dedupStyles.colMetaKey}>ingested</span>
                <span style={dedupStyles.colMetaVal}>Oct 21, 2025 · 14:02</span>
                <span style={dedupStyles.colMetaKey}>size</span>
                <span style={dedupStyles.colMetaVal}>2.4 MB · 24 pages</span>
                <span style={dedupStyles.colMetaKey}>entities</span>
                <span style={dedupStyles.colMetaVal}>47 linked</span>
              </div>
              <div style={{ fontSize: 11, color: "var(--fg3)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500, marginBottom: 6 }}>
                Matching snippet
              </div>
              <div style={dedupStyles.snippet}>
                Revenue grew <span style={dedupStyles.diff}>24.3% year-over-year</span> driven by enterprise
                expansion in North America and APAC. Net retention reached <span style={dedupStyles.diff}>118%</span>,
                up from 114% in Q2.
              </div>
              <div style={{ marginTop: 14 }}>
                {["ACME Corp", "Q3 2025", "Revenue", "NRR", "Enterprise"].map(e => (
                  <span key={e} style={dedupStyles.entity}>{e}</span>
                ))}
              </div>
            </div>

            {/* Duplicate */}
            <div style={dedupStyles.col}>
              <div style={dedupStyles.colBadge(false)}>DUPLICATE</div>
              <div style={dedupStyles.colTitle}>Q3-financial-report-FINAL-v2.pdf</div>
              <div style={dedupStyles.colMeta}>
                <span style={dedupStyles.colMetaKey}>source</span>
                <span style={dedupStyles.colMetaVal}>Outlook · attachment</span>
                <span style={dedupStyles.colMetaKey}>ingested</span>
                <span style={dedupStyles.colMetaVal}>Oct 22, 2025 · 09:18</span>
                <span style={dedupStyles.colMetaKey}>size</span>
                <span style={dedupStyles.colMetaVal}>2.4 MB · 24 pages</span>
                <span style={dedupStyles.colMetaKey}>entities</span>
                <span style={dedupStyles.colMetaVal}>44 linked</span>
              </div>
              <div style={{ fontSize: 11, color: "var(--fg3)", textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500, marginBottom: 6 }}>
                Matching snippet
              </div>
              <div style={dedupStyles.snippet}>
                Revenue grew <span style={dedupStyles.diff}>24.3% YoY</span> driven by enterprise
                expansion in North America and APAC. Net retention reached <span style={dedupStyles.diff}>118%</span>,
                up from 114% in Q2.
              </div>
              <div style={{ marginTop: 14 }}>
                {["ACME Corp", "Q3 2025", "Revenue", "NRR", "Enterprise"].map(e => (
                  <span key={e} style={dedupStyles.entity}>{e}</span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { DeduplicateScreen });
