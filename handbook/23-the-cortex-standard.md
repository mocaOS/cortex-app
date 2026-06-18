# Chapter 23: The Cortex Standard

> *Knowledge should be free to move. Memory should outlive the model. Answers should be fast, grounded, and honest. This is the standard we hold Cortex to — and the standard we believe the field should hold itself to.*

This chapter is different from the others. The preceding twenty-two chapters tell you *how* Cortex works. This one tells you *why it works the way it does* — the convictions underneath the engineering, and how to wield them to build something great.

---

## 1. Why a Graph, and Why Now

The industry spent two years pretending a vector index was a memory system. It isn't. A pile of embeddings can tell you which paragraphs *sound* similar to a question; it cannot tell you that Pindar van Arman *trained* a robot that *painted* a portrait that *sold* at a MOCA auction. Similarity is not understanding. **Structure is.**

A knowledge graph is the state of the art for AI memory because it stores the two things a vector store throws away:

- **Entities** — the people, works, systems, concepts, and organizations your documents are actually *about*.
- **Relationships** — the verbs that connect them, with direction and meaning.

That structure is what lets an agent reason across documents instead of within one, follow a chain three hops deep, and answer a question whose answer lives in the *space between* sources rather than in any single passage. Vectors find the neighborhood. The graph walks the street.

Cortex is not "RAG with extra steps." It is a memory substrate: documents in, a traversable, queryable, *durable* graph out — the long-term memory layer your agents keep even as the models in front of them are replaced.

## 2. What Makes a Cortex Graph State-of-the-Art

Anyone can prompt an LLM to spit out triples. Building a graph you can *trust* and *query at speed* is the hard part. Cortex's pipeline is engineered end-to-end for that:

- **LLM-assisted extraction with discipline.** Entities and relationships are extracted per chunk, then resolved and de-duplicated with fuzzy matching (rapidfuzz) so "Microsoft Dynamics 365" and "MS Dynamics 365 F&O" become one node, not two. Reasoning is deliberately suppressed on these structured-extraction calls — chain-of-thought corrupts structured output and burns budget for no gain (see Chapter 4, `EXTRACTION_REASONING_MODE`).
- **Hybrid retrieval, fused.** Every query runs three retrievals in parallel — vector similarity, full-text keyword, and graph traversal — unified with Reciprocal Rank Fusion and then sharpened by a cross-encoder re-ranker. No single retrieval modality is trusted alone; the graph catches what vectors miss and vice versa.
- **Communities, not just nodes.** Leiden/Louvain community detection clusters the graph into themes, each with an LLM-generated summary, so the system can answer broad questions ("what is this corpus *about*?") that have no single home in any document.
- **An agentic answer layer.** A researcher agent plans and runs retrieval across tools (knowledge search, entity lookup, community search, live skills); a writer composes the final, cited answer. Two modes — snappy chat and deep research — share the same graph.
- **Provenance throughout.** Every answer carries `[src_N]` citations back to real chunks. Grounding isn't a feature flag; it's the contract.

The graph lives in Neo4j with vector, full-text, and graph indexes in one engine. It is exposed through 60+ REST endpoints. It is yours.

## 3. The Open-Systems Principle

Cortex is open source because **memory you don't control isn't memory — it's rent.**

AI frameworks rise and fall in a season. If your organization's accumulated knowledge is trapped inside one vendor's proprietary memory format, you don't own your knowledge; you lease it, and the lease renews on their terms. Cortex refuses that bargain:

- **Any model, any provider.** The LLM is configured behind a single OpenAI-compatible interface. OpenAI, Anthropic, Venice, a self-hosted vLLM, a GPU box — swap the endpoint, keep the graph. Per-provider concerns (like reasoning control) are handled centrally so the rest of the system never has to care which vendor is on the other end.
- **No lock-in on your data.** Export the entire library — documents, chunks, embeddings, entities, relationships, communities — as a portable archive, any time. When the next great framework ships, connect it to the graph you already own.
- **Self-hostable, end to end.** Docker, Coolify, or bare metal. Your infrastructure, your keys, your data residency.

This is the passion underneath the project: **open systems compound; closed systems decay.** A graph you can read, move, fork, and extend gets better every year. A black box gets exactly as good as its vendor's last quarter.

## 4. The Answer Layer: Fast, Grounded, Concise

A knowledge graph is only as good as the answers it gives a human waiting on a screen. The standard here is non-negotiable: **answers must be fast, grounded, and concise — and they must never silently fail.**

This is also where most knowledge systems quietly fall apart, and where Cortex draws a hard line:

- **Fast — measured in time-to-first-token.** A user feels latency before the first word, not after the last. On reasoning-capable models, hidden chain-of-thought streams in a side channel the user never sees, delaying that first token by many seconds and — across an agentic loop — exhausting the budget into *empty answers*. Cortex suppresses reasoning on the chat/answer path by default (`DEFAULT_REASONING_MODE=off`; Chapter 4), cutting time-to-first-token from seconds to sub-second on the same model. The thinking that matters happens in the *researcher's* explicit, visible steps — not in invisible token-burn before the answer.
- **Grounded — every claim cited.** Answers reference `[src_N]` sources that trace back to real chunks. An answer you can't verify is a rumor with good grammar.
- **Concise — respect the reader.** Chat answers lead with the answer, in the first sentence, and stop when they're done. Length is not depth. (Deep-research mode exists for when depth genuinely *is* the ask — see Chapter 10.)
- **Honest — no empty answers.** A system that returns nothing, slowly, has failed twice. Cortex's answer path is engineered so a capable model produces a real, cited answer within budget — and degraded paths surface a clear signal, never a silent blank.

### Two modes, one graph

| Mode | What it's for | Behavior |
|------|---------------|----------|
| **Chat (speed)** | The conversational default — fast, snappy, concise | Reasoning suppressed, lean retrieval, first token in well under a second on a good model |
| **Deep Research (quality)** | "Go find everything and synthesize it" | The full agentic researcher loop, multi-hop, exhaustive — streamed so it stays alive over long runs |

The rule of thumb: **chat for conversation, research for reports.** Don't make a user wait 90 seconds for a one-line fact, and don't cram a literature review into a chat bubble.

### Choosing a model

Because Cortex is provider-agnostic, the model is *yours* to choose — and the right choice is empirical, not fashionable. The repository ships a Q&A chat benchmark that holds the graph fixed and measures each candidate model on the only axes that matter for this layer: **time-to-first-token, total latency, reliability (no empty/timeout answers), and answer quality.** A model that "benchmarks well" on leaderboards can still overthink itself into empty chat answers; measure it on *your* graph, with thinking off, and let the numbers pick. Greatness here is a measured property, not a vibe.

## 5. How We Hold Ourselves to the Standard

A standard you don't measure is a slogan. Cortex treats the answer layer as something to be *benchmarked*, not asserted:

- Questions generated from the live graph, so the test reflects the real corpus.
- The graph held fixed while only the model varies — a fair fight.
- Streaming metrics that capture what users feel: first-token latency, total time, timeout/empty rate, output rate.
- An independent judge scoring faithfulness, completeness, groundedness, and conciseness.

When a model returns empty answers, we treat it as a *bug in our plumbing first* and the model's fault second — because usually it is. That posture is the standard: the system is responsible for getting the best out of whatever model you point it at.

## 6. Building Something Great

The tools are here; greatness is in how you use them:

- **Curate, don't dump.** A focused collection of high-signal documents produces a sharper graph than ten thousand pages of noise. The graph mirrors what you feed it.
- **Use collections as lenses.** Scope knowledge by team, project, or persona. A virtual curator with a tight collection has a point of view; one with everything has none.
- **Let the graph rebuild.** Knowledge is a living thing. Push new learnings, regenerate, and let updated understanding propagate to every agent and app that reads from the library.
- **Keep your memory portable.** Export regularly. Your graph should be ready to outlive any single model, framework, or hosting choice — including ours.
- **Demand grounded answers.** If an answer isn't cited, don't ship it. Trust is the product.

---

## The Standard, Stated

1. **Structure over similarity.** Memory is entities and relationships, not a bag of vectors.
2. **Portable by default.** If you can't export it and move it, you don't own it.
3. **Any model, behind one interface.** The graph outlives the LLM in front of it.
4. **Fast is first-token.** Suppress invisible thinking on the answer path; respect the human waiting.
5. **Grounded or it doesn't ship.** Every claim cites a source.
6. **Concise is a courtesy.** Lead with the answer; depth is a separate mode, on request.
7. **Never fail silently.** A slow blank is the worst answer of all.
8. **Measure greatness.** Benchmark the answer layer; let evidence, not hype, choose the model.
9. **Open compounds.** Closed systems decay; open systems get better every year.

This is the Cortex standard. It is what we build to, and what we invite the rest of the field to build to.

*Built by MOCA (The Museum of Crypto Art) — with a stubborn belief in open systems.*

---

*Continue to [Chapter 1: Introduction](01-introduction.md) for the full tour, or [Chapter 10: Ask AI](10-ask-ai.md) for the answer layer in practice.*
