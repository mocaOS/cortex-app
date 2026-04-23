// MOCA Library — Explore screen (knowledge graph canvas)

const exploreStyles = {
  wrap: { display: "grid", gridTemplateColumns: "260px 1fr 320px", height: "100%", overflow: "hidden" },

  left: { borderRight: "1px solid var(--border)", background: "oklch(0.15 0 0)", display: "flex", flexDirection: "column" },
  leftHead: { padding: "14px 16px", borderBottom: "1px solid var(--border)" },
  leftTitle: { fontSize: 13, fontWeight: 600 },
  leftSub: { fontSize: 11, color: "var(--fg2)", marginTop: 2 },

  filterGroup: { padding: "12px 16px", borderBottom: "1px solid var(--border)" },
  filterLabel: { fontSize: 10.5, fontWeight: 500, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--fg3)", marginBottom: 8 },
  filterRow: {
    display: "flex", alignItems: "center", gap: 8,
    padding: "5px 0", fontSize: 13, cursor: "pointer",
  },
  legendDot: (color) => ({ width: 10, height: 10, borderRadius: "50%", background: color, border: "1px solid oklch(1 0 0 / 0.15)" }),
  filterCount: { marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--fg2)" },

  canvasWrap: {
    position: "relative", overflow: "hidden",
    background: "oklch(0.12 0 0)",
    backgroundImage: `
      radial-gradient(oklch(1 0 0 / 0.05) 1px, transparent 1px)
    `,
    backgroundSize: "32px 32px",
  },
  canvasOverlay: {
    position: "absolute", inset: 0,
    background: "radial-gradient(ellipse at 60% 40%, oklch(0.79 0.18 70.67 / 0.1), transparent 55%)",
    pointerEvents: "none",
  },
  floatingToolbar: {
    position: "absolute", top: 16, left: 16,
    display: "flex", gap: 4, padding: 4,
    background: "oklch(0.21 0 0 / 0.65)",
    backdropFilter: "blur(24px)", WebkitBackdropFilter: "blur(24px)",
    border: "1px solid var(--border)", borderRadius: "var(--radius)",
  },
  zoomBar: {
    position: "absolute", bottom: 16, right: 16,
    display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
    background: "oklch(0.21 0 0 / 0.65)",
    backdropFilter: "blur(24px)", WebkitBackdropFilter: "blur(24px)",
    border: "1px solid var(--border)", borderRadius: "var(--radius)",
    fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--fg2)",
  },
  legendCapsule: {
    position: "absolute", bottom: 16, left: 16,
    display: "flex", gap: 10, padding: "8px 14px",
    background: "oklch(0.21 0 0 / 0.65)",
    backdropFilter: "blur(24px)", WebkitBackdropFilter: "blur(24px)",
    border: "1px solid var(--border)", borderRadius: 999,
    fontSize: 11.5, color: "var(--fg1)",
  },

  // Inspector
  inspector: { borderLeft: "1px solid var(--border)", background: "var(--card)", overflowY: "auto" },
  inspHead: { padding: "16px", borderBottom: "1px solid var(--border)" },
  inspKind: { fontSize: 10.5, fontWeight: 500, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--accent)" },
  inspName: { fontSize: 18, fontWeight: 600, marginTop: 4, letterSpacing: "-0.01em" },
  inspMeta: { fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--fg2)", marginTop: 8 },
  inspActions: { display: "flex", gap: 6, marginTop: 14 },

  inspSection: { padding: "14px 16px", borderBottom: "1px solid var(--border)" },
  inspSectTitle: { fontSize: 11, fontWeight: 600, letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--fg2)", marginBottom: 10 },
  propRow: { display: "flex", justifyContent: "space-between", fontSize: 12, padding: "4px 0" },
  propKey: { color: "var(--fg2)", fontFamily: "var(--font-mono)" },
  propVal: { color: "var(--fg1)" },

  relationRow: {
    display: "flex", alignItems: "center", gap: 10,
    padding: "8px 0", fontSize: 12.5,
    borderBottom: "1px solid var(--border)",
  },
  relationVerb: { color: "var(--fg3)", fontFamily: "var(--font-mono)", fontSize: 11 },

  sourceRow: {
    display: "flex", alignItems: "center", gap: 8,
    padding: "8px 10px", marginBottom: 6,
    background: "var(--muted)", borderRadius: "var(--radius-md)",
    fontSize: 12,
  },
};

const ENTITY_TYPES = [
  { id: "person",   label: "Person",       color: "oklch(0.9 0 0)",   count: 412 },
  { id: "org",      label: "Organization", color: "oklch(0.7 0 0)",   count: 128 },
  { id: "concept",  label: "Concept",      color: "oklch(0.55 0 0)",  count: 1206 },
  { id: "event",    label: "Event",        color: "oklch(0.45 0 0)",  count: 38 },
  { id: "location", label: "Location",     color: "oklch(0.35 0 0)",  count: 94 },
];

// Pre-computed graph layout (pseudo force-directed positions, viewBox 700×800)
const NODES = [
  { id: "n1",  label: "Acme Corp",          type: "org",      r: 24, cx: 350, cy: 380, focus: true },
  { id: "n2",  label: "Q3 Earnings Report", type: "concept",  r: 17, cx: 180, cy: 230 },
  { id: "n3",  label: "Jane Doe",           type: "person",   r: 15, cx: 500, cy: 180 },
  { id: "n4",  label: "EU Markets",         type: "concept",  r: 19, cx: 540, cy: 460 },
  { id: "n5",  label: "Berlin Summit",      type: "event",    r: 15, cx: 620, cy: 290 },
  { id: "n6",  label: "Germany",            type: "location", r: 16, cx: 640, cy: 560 },
  { id: "n7",  label: "Revenue Growth",     type: "concept",  r: 14, cx: 210, cy: 540 },
  { id: "n8",  label: "John Smith",         type: "person",   r: 13, cx: 100, cy: 380 },
  { id: "n9",  label: "Board Minutes",      type: "concept",  r: 13, cx: 390, cy: 620 },
  { id: "n10", label: "France",             type: "location", r: 13, cx: 530, cy: 660 },
  { id: "n11", label: "CFO",                type: "concept",  r: 12, cx: 130, cy: 620 },
];
const EDGES = [
  { s: "n1", t: "n2", w: 2 }, { s: "n1", t: "n3", w: 1.5 }, { s: "n1", t: "n4", w: 2.2 },
  { s: "n1", t: "n7", w: 1 }, { s: "n1", t: "n9", w: 1 },
  { s: "n4", t: "n5", w: 1 }, { s: "n4", t: "n6", w: 1.4 }, { s: "n4", t: "n10", w: 1 },
  { s: "n3", t: "n5", w: 1 }, { s: "n2", t: "n7", w: 1 },
  { s: "n8", t: "n11", w: 1 }, { s: "n8", t: "n1", w: 1 }, { s: "n11", t: "n1", w: 1 },
  { s: "n9", t: "n7", w: 0.8 },
];
const colorFor = (type) => ENTITY_TYPES.find(e => e.id === type).color;

const ACCENT = "oklch(0.79 0.18 70.67)";
const BG_DARK = "oklch(0.12 0 0)";
const EDGE_COLOR = "oklch(1 0 0 / 0.15)";

function Graph({ focusedId, onFocus }) {
  const node = (id) => NODES.find(n => n.id === id);
  return (
    <svg viewBox="0 0 700 800" preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%", display: "block" }}>
      <defs>
        <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="6" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>
      {EDGES.map((e, i) => {
        const s = node(e.s), t = node(e.t);
        const active = e.s === focusedId || e.t === focusedId;
        return (
          <line
            key={i}
            x1={s.cx} y1={s.cy} x2={t.cx} y2={t.cy}
            stroke={active ? ACCENT : EDGE_COLOR}
            strokeWidth={active ? e.w + 0.8 : e.w}
          />
        );
      })}
      {NODES.map(n => {
        const isFocus = n.id === focusedId;
        return (
          <g key={n.id} style={{ cursor: "pointer" }} onClick={() => onFocus(n.id)}>
            <circle
              cx={n.cx} cy={n.cy} r={n.r}
              fill={isFocus ? ACCENT : colorFor(n.type)}
              stroke={isFocus ? ACCENT : BG_DARK}
              strokeWidth={isFocus ? 3 : 2}
              filter={isFocus ? "url(#glow)" : undefined}
            />
            <text
              x={n.cx} y={n.cy + n.r + 13}
              textAnchor="middle"
              fontSize="10.5"
              fontFamily="Inter, sans-serif"
              fontWeight={isFocus ? 600 : 500}
              fill={isFocus ? "oklch(0.98 0 0)" : "oklch(0.7 0 0)"}
            >{n.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

function Inspector({ focusedId }) {
  const n = NODES.find(x => x.id === focusedId);
  if (!n) return null;
  const type = ENTITY_TYPES.find(e => e.id === n.type);
  const rels = EDGES.filter(e => e.s === n.id || e.t === n.id).map(e => {
    const otherId = e.s === n.id ? e.t : e.s;
    return { other: NODES.find(x => x.id === otherId), verb: e.s === n.id ? "mentions" : "mentioned_by" };
  });

  return (
    <aside style={exploreStyles.inspector} key={n.id}>
      <div style={exploreStyles.inspHead}>
        <div style={exploreStyles.inspKind}>{type.label}</div>
        <div style={exploreStyles.inspName}>{n.label}</div>
        <div style={exploreStyles.inspMeta}>id: ent_{n.id}_a1f3 · confidence 0.94</div>
        <div style={exploreStyles.inspActions}>
          <button className="btn btn-primary btn-sm"><window.Icon.Pin className="icon icon-sm" /> Pin</button>
          <button className="btn btn-outline btn-sm"><window.Icon.MessageSquare className="icon icon-sm" /> Ask</button>
          <button className="btn btn-ghost btn-icon"><window.Icon.MoreH /></button>
        </div>
      </div>

      <div style={exploreStyles.inspSection}>
        <div style={exploreStyles.inspSectTitle}>Properties</div>
        <div style={exploreStyles.propRow}><span style={exploreStyles.propKey}>type</span><span style={exploreStyles.propVal}>{n.type}</span></div>
        <div style={exploreStyles.propRow}><span style={exploreStyles.propKey}>mentions</span><span style={exploreStyles.propVal}>{rels.length * 4}</span></div>
        <div style={exploreStyles.propRow}><span style={exploreStyles.propKey}>first_seen</span><span style={exploreStyles.propVal}>2025-09-08</span></div>
        <div style={exploreStyles.propRow}><span style={exploreStyles.propKey}>last_seen</span><span style={exploreStyles.propVal}>2025-10-14</span></div>
      </div>

      <div style={exploreStyles.inspSection}>
        <div style={exploreStyles.inspSectTitle}>Relations · {rels.length}</div>
        {rels.slice(0, 6).map((r, i) => (
          <div key={i} style={exploreStyles.relationRow}>
            <span style={exploreStyles.relationVerb}>{r.verb}</span>
            <span style={{ flex: 1 }}>{r.other.label}</span>
            <window.Icon.ChevronRight className="icon icon-sm" style={{ stroke: "var(--fg3)" }} />
          </div>
        ))}
      </div>

      <div style={exploreStyles.inspSection}>
        <div style={exploreStyles.inspSectTitle}>Sources · 3</div>
        <div style={exploreStyles.sourceRow}>
          <window.Icon.FileText className="icon icon-sm" />
          <span className="truncate">Q3-2025-financials.pdf</span>
          <span className="mono fg3" style={{ fontSize: 10 }}>p. 12</span>
        </div>
        <div style={exploreStyles.sourceRow}>
          <window.Icon.FileText className="icon icon-sm" />
          <span className="truncate">board-minutes-2025-10.pdf</span>
          <span className="mono fg3" style={{ fontSize: 10 }}>p. 4</span>
        </div>
        <div style={exploreStyles.sourceRow}>
          <window.Icon.FileText className="icon icon-sm" />
          <span className="truncate">eu-market-analysis.pdf</span>
          <span className="mono fg3" style={{ fontSize: 10 }}>p. 28</span>
        </div>
      </div>
    </aside>
  );
}

function ExploreScreen() {
  const [focused, setFocused] = React.useState("n1");
  return (
    <div style={exploreStyles.wrap}>
      <aside style={exploreStyles.left}>
        <div style={exploreStyles.leftHead}>
          <div style={exploreStyles.leftTitle}>Knowledge Graph</div>
          <div style={exploreStyles.leftSub}>27,501 entities · 94,218 relations</div>
        </div>

        <div style={exploreStyles.filterGroup}>
          <div style={exploreStyles.filterLabel}>Entity Types</div>
          {ENTITY_TYPES.map(t => (
            <div key={t.id} style={exploreStyles.filterRow}>
              <span style={exploreStyles.legendDot(t.color)} />
              <span>{t.label}</span>
              <span style={exploreStyles.filterCount}>{t.count}</span>
            </div>
          ))}
        </div>

        <div style={exploreStyles.filterGroup}>
          <div style={exploreStyles.filterLabel}>Collections</div>
          <div style={exploreStyles.filterRow}><window.Icon.Folder className="icon icon-sm" style={{ stroke: "var(--fg2)" }}/> Finance <span style={exploreStyles.filterCount}>124</span></div>
          <div style={exploreStyles.filterRow}><window.Icon.Folder className="icon icon-sm" style={{ stroke: "var(--fg2)" }}/> Research <span style={exploreStyles.filterCount}>58</span></div>
          <div style={exploreStyles.filterRow}><window.Icon.Folder className="icon icon-sm" style={{ stroke: "var(--fg2)" }}/> Legal <span style={exploreStyles.filterCount}>31</span></div>
        </div>

        <div style={exploreStyles.filterGroup}>
          <div style={exploreStyles.filterLabel}>Layout</div>
          <div style={exploreStyles.filterRow}><window.Icon.Layers className="icon icon-sm" /> Force-directed</div>
          <div style={exploreStyles.filterRow}><window.Icon.GitBranch className="icon icon-sm" /> Hierarchical</div>
        </div>
      </aside>

      <div style={exploreStyles.canvasWrap}>
        <div style={exploreStyles.canvasOverlay} />

        <div style={exploreStyles.floatingToolbar}>
          <button className="btn btn-icon btn-ghost" title="Filter"><window.Icon.Filter /></button>
          <button className="btn btn-icon btn-ghost" title="Search"><window.Icon.Search /></button>
          <button className="btn btn-icon btn-ghost" title="Refresh"><window.Icon.Refresh /></button>
          <div style={{ width: 1, background: "var(--border)", margin: "4px 2px" }}/>
          <button className="btn btn-icon btn-ghost" title="Download"><window.Icon.Download /></button>
        </div>

        <Graph focusedId={focused} onFocus={setFocused} />

        <div style={exploreStyles.legendCapsule}>
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--accent)" }} /> Focused
          </span>
          <span style={{ color: "var(--fg3)" }}>·</span>
          <span style={{ color: "var(--fg2)" }}>Click a node to inspect · drag to pan · scroll to zoom</span>
        </div>

        <div style={exploreStyles.zoomBar}>
          <button className="btn btn-icon btn-ghost btn-sm" style={{ padding: 2, width: 22, height: 22 }}>−</button>
          <span>100%</span>
          <button className="btn btn-icon btn-ghost btn-sm" style={{ padding: 2, width: 22, height: 22 }}>+</button>
        </div>
      </div>

      <Inspector focusedId={focused} />
    </div>
  );
}

window.ExploreScreen = ExploreScreen;
