// MOCA Library — Ask screen (source-attributed Q&A)

const askStyles = {
  wrap: {
    maxWidth: 820, margin: "0 auto",
    padding: "32px 24px 160px",
    display: "flex", flexDirection: "column", gap: 16,
    minHeight: "100%",
  },
  empty: {
    display: "flex", flexDirection: "column", alignItems: "center",
    gap: 10, padding: "80px 0 40px", textAlign: "center",
  },
  emptyIcon: {
    width: 48, height: 48, borderRadius: 12,
    background: "oklch(0.79 0.18 70.67 / 0.15)",
    color: "var(--accent)",
    display: "flex", alignItems: "center", justifyContent: "center",
    marginBottom: 8,
  },
  emptyTitle: { fontSize: 22, fontWeight: 700, letterSpacing: "-0.015em" },
  emptySub: { color: "var(--fg2)", fontSize: 13, maxWidth: 440 },

  suggestions: {
    display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10,
    width: "100%", maxWidth: 640, marginTop: 20,
  },
  suggestion: {
    padding: "14px 16px", textAlign: "left",
    background: "var(--card)", border: "1px solid var(--border)",
    borderRadius: "var(--radius)", color: "var(--fg1)",
    cursor: "pointer", fontSize: 13, lineHeight: 1.45,
    transition: "all 150ms",
  },

  userRow: { display: "flex", justifyContent: "flex-end" },
  userBubble: {
    background: "var(--primary)", color: "var(--primary-fg)",
    padding: "10px 14px", borderRadius: 12,
    maxWidth: "75%", fontSize: 14,
  },

  aiCard: {
    background: "var(--card)", border: "1px solid var(--border)",
    borderRadius: "var(--radius)", padding: 18,
  },
  aiHead: {
    display: "flex", alignItems: "center", gap: 8, marginBottom: 12,
    fontSize: 11, color: "var(--fg2)",
    textTransform: "uppercase", letterSpacing: "0.08em", fontWeight: 500,
  },
  aiBody: { fontSize: 14.5, lineHeight: 1.65, color: "var(--fg1)" },
  aiSource: {
    color: "var(--accent)",
    textDecoration: "underline",
    textDecorationStyle: "dotted",
    textUnderlineOffset: 3,
    cursor: "pointer",
  },

  aiFoot: {
    display: "flex", gap: 8, marginTop: 14, paddingTop: 14,
    borderTop: "1px solid var(--border)", flexWrap: "wrap",
  },
  sourceChip: {
    display: "inline-flex", alignItems: "center", gap: 6,
    padding: "5px 10px", borderRadius: 6,
    background: "var(--muted)", fontSize: 11.5, color: "var(--fg1)",
    cursor: "pointer",
  },
  sourceChipNum: {
    fontFamily: "var(--font-mono)", fontSize: 10,
    color: "var(--fg3)",
  },

  composerWrap: {
    position: "sticky", bottom: 20, marginTop: "auto",
    display: "flex", justifyContent: "center",
  },
  composer: {
    display: "flex", alignItems: "center", gap: 8, padding: 8,
    background: "oklch(0.21 0 0 / 0.75)",
    backdropFilter: "blur(24px)", WebkitBackdropFilter: "blur(24px)",
    border: "1px solid var(--border)",
    borderRadius: 14,
    boxShadow: "0 20px 25px oklch(0 0 0 / 0.4)",
    width: "100%", maxWidth: 720,
  },
  composerInput: {
    flex: 1, background: "transparent", border: 0, outline: 0,
    color: "var(--fg1)", padding: "10px 12px", fontSize: 14,
    fontFamily: "inherit",
  },

  thinkingDots: {
    display: "inline-flex", gap: 4, alignItems: "center",
  },
  dot: (delay) => ({
    width: 6, height: 6, borderRadius: "50%",
    background: "var(--accent)",
    animation: `moca-pulse 1.2s ${delay}s infinite ease-in-out`,
  }),
};

const SUGGESTIONS = [
  "What's our Q3 revenue from EU markets?",
  "Which entities does Acme Corp relate to?",
  "Summarize the latest board minutes in 3 bullets.",
  "What concepts appear most in Research collection?",
];

function AIAnswer({ q }) {
  // Simulated answer keyed to the question
  const ans = {
    q1: {
      body: (
        <>
          Based on <span style={askStyles.aiSource}>Q3-2025-financials.pdf</span> and{" "}
          <span style={askStyles.aiSource}>eu-market-analysis.pdf</span>, EU markets contributed{" "}
          <b>€14.2M</b> in Q3 2025 — up <b>23% YoY</b>. Germany and France led the region at <b>68%</b> of total EU revenue, with Berlin-based accounts growing fastest.
        </>
      ),
      sources: [
        { n: 1, name: "Q3-2025-financials.pdf", page: "p. 12" },
        { n: 2, name: "eu-market-analysis.pdf", page: "p. 4–7" },
        { n: 3, name: "board-minutes-2025-10.pdf", page: "p. 2" },
      ],
      entities: 7,
    },
  }[q] || null;

  if (!ans) return null;

  return (
    <div style={askStyles.aiCard}>
      <div style={askStyles.aiHead}>
        <window.Icon.Sparkles className="icon icon-sm" style={{ stroke: "var(--accent)" }}/>
        Answer
      </div>
      <div style={askStyles.aiBody}>{ans.body}</div>
      <div style={askStyles.aiFoot}>
        {ans.sources.map(s => (
          <span key={s.n} style={askStyles.sourceChip}>
            <span style={askStyles.sourceChipNum}>[{s.n}]</span>
            <window.Icon.FileText className="icon icon-sm" style={{ stroke: "var(--fg2)" }}/>
            <span>{s.name}</span>
            <span style={askStyles.sourceChipNum}>{s.page}</span>
          </span>
        ))}
        <span className="chip chip-pill" style={{ background: "oklch(0.79 0.18 70.67 / 0.12)", color: "var(--accent)", marginLeft: "auto" }}>
          {ans.entities} entities
        </span>
      </div>
    </div>
  );
}

function Thinking() {
  return (
    <div style={askStyles.aiCard}>
      <div style={askStyles.aiHead}>
        <window.Icon.Sparkles className="icon icon-sm" style={{ stroke: "var(--accent)" }}/>
        Searching · 4 sources · 12 entities
      </div>
      <div style={askStyles.thinkingDots}>
        <span style={askStyles.dot(0)} />
        <span style={askStyles.dot(0.15)} />
        <span style={askStyles.dot(0.3)} />
      </div>
    </div>
  );
}

function AskScreen() {
  const [messages, setMessages] = React.useState([]);
  const [value, setValue] = React.useState("");
  const [thinking, setThinking] = React.useState(false);
  const scrollRef = React.useRef(null);

  const send = (text) => {
    if (!text.trim()) return;
    setMessages(m => [...m, { role: "user", text }]);
    setValue("");
    setThinking(true);
    setTimeout(() => {
      setThinking(false);
      setMessages(m => [...m, { role: "ai", q: "q1" }]);
    }, 900);
  };

  React.useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, thinking]);

  return (
    <div ref={scrollRef} style={{ height: "100%", overflow: "auto" }}>
      <div style={askStyles.wrap}>
        {messages.length === 0 && !thinking && (
          <div style={askStyles.empty}>
            <div style={askStyles.emptyIcon}><window.Icon.Sparkles className="icon icon-lg" /></div>
            <div style={askStyles.emptyTitle}>Ask the library.</div>
            <div style={askStyles.emptySub}>
              Every answer is grounded in your documents. Sources and entities are shown for every claim.
            </div>
            <div style={askStyles.suggestions}>
              {SUGGESTIONS.map((s, i) => (
                <div key={i} style={askStyles.suggestion} onClick={() => send(s)}
                     onMouseOver={e => e.currentTarget.style.background = "oklch(0.24 0 0)"}
                     onMouseOut={e => e.currentTarget.style.background = "var(--card)"}>
                  {s}
                </div>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => m.role === "user"
          ? <div key={i} style={askStyles.userRow}><div style={askStyles.userBubble}>{m.text}</div></div>
          : <AIAnswer key={i} q={m.q} />
        )}

        {thinking && <Thinking />}

        <div style={askStyles.composerWrap}>
          <form
            style={askStyles.composer}
            onSubmit={e => { e.preventDefault(); send(value); }}
          >
            <window.Icon.MessageSquare className="icon" style={{ stroke: "var(--fg2)", marginLeft: 8 }} />
            <input
              value={value}
              onChange={e => setValue(e.target.value)}
              placeholder="Ask a question about your documents…"
              style={askStyles.composerInput}
            />
            <span className="chip chip-pill" style={{ background: "var(--muted)" }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--accent)" }}/>
              All collections
            </span>
            <button type="submit" className="btn btn-primary">
              <window.Icon.Send className="icon icon-sm" /> Ask
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}

window.AskScreen = AskScreen;
