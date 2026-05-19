# Cortex — Design System

> An agentic, portable knowledge base for the AI era. Cortex ingests documents, extracts entities and relationships with LLMs, builds a Neo4j knowledge graph, and exposes it through a REST API + web UI so AI agents and knowledge workers can search, ask, and explore.

This folder is the single source of truth for Cortex's visual language: colors, type, motion, components, and copy. It powers both the product UI and anything brand-adjacent (slides, marketing, mocks, throwaway prototypes).

---

## Product at a glance

- **Mission control meets modern design tool.** Operators run ingest jobs, inspect pipelines, watch entity extraction happen in real time.
- **Ask. Explore. Manage.** The three top-level modes of the app.
  - **Manage** — upload & ingest docs, watch job queue, manage source collections.
  - **Explore** — graph canvas of entities + relationships; filter, pivot, isolate neighborhoods.
  - **Ask** — source-attributed Q&A; every answer cites documents and surfaces the entities it pulled from.
- **For two audiences at once.** Technical (engineers wiring up agents to the REST API) and non-technical (analysts dragging PDFs into a graph). The UI is bilingual — same screen, same chrome, different depth.

### Source material
- Repo: **mocaOS/cortex-app** (read via GitHub on demand)
- Fonts (uploaded): `InterVariable.ttf`, `InterVariable-Italic.ttf`, `JetBrainsMono[wght].ttf`, `JetBrainsMono-Italic[wght].ttf` — copied into `fonts/`
- Brand brief from the user (accent color, glass morphism, motion rules) — captured verbatim in the Visual Foundations section below.

---

## Index

| File / folder | What it is |
|---|---|
| `README.md` | This file — orientation, content & visual foundations, iconography |
| `SKILL.md` | Agent-skill front-matter so this folder works as a drop-in Claude Code skill |
| `colors_and_type.css` | The canonical CSS — tokens, semantic vars, font faces. Import this anywhere. |
| `tokens.css` | Raw token map (subset of above, no `@font-face`) |
| `fonts/` | Inter + JetBrains Mono variable TTFs |
| `assets/` | Logos, brand marks, shipped imagery |
| `preview/*.html` | Small cards that populate the Design System tab |
| `ui_kits/library/` | Cortex web app — pixel-faithful UI recreation with interactive screens |
| `reference/MASTER.md` | Raw research notes pulled from the repo (not canonical — treat as scratch) |

---

## Content fundamentals

**Voice: confident, technical, lowercase-friendly, no hype.** Cortex talks to people who ship. It doesn't explain what an LLM is; it tells you what it did with yours.

- **Casing.** Sentence case everywhere — buttons, headings, nav. Title case only in legal/proper nouns ("Neo4j", "Cortex"). ALL CAPS is reserved for small eyebrow labels (`SECTION LABEL`, letter-spacing +0.06em).
- **Person.** "You" for the operator. "It" / "the graph" / "the library" for the system — never "we" or "our AI". The product is a tool, not a colleague.
- **Tense.** Past for completed work ("Extracted 142 entities from 3 docs"), present for state ("2 jobs running"), imperative for actions ("Ingest", "Ask", "Pin to canvas").
- **Numbers.** Always precise. `27,501 entities`. `€14.2M`. `3 sources`. Never "a lot" or "many". The product's whole value prop is provenance, so the copy matches.
- **Status copy.** Terse. `Completed` / `Processing 62%` / `Failed — retry` / `Queued`. Never `Yay! All done! 🎉`.
- **Empty states.** Describe the next action, not the absence. ✅ "Drop a PDF to begin extraction." ❌ "No documents yet."
- **Error copy.** What happened + what to do. ✅ "Neo4j connection dropped — retry or check `NEO4J_URL`." ❌ "Something went wrong."
- **AI answers.** Every answer is followed by source chips (`3 sources`, `7 entities`). The copy never asserts without attribution — "Based on *Q3-2025-financials.pdf*, …" is the canonical opener.
- **Emoji.** None in product UI. None in marketing. Occasionally in speaker notes or internal dashboards.
- **Unicode accents used sparingly.** `→` in flows/CTAs ("Open graph →"), `·` as a separator in metadata, `…` for in-progress states ("Extracting entities…"). No em-dashes in button copy.

### Micro-examples
```
Ingest                  ← button
27,501 entities         ← stat
Processing · 62%        ← chip
Drop files to ingest    ← empty state
Failed — click to retry ← error chip
Based on Q3-2025-financials.pdf, EU markets contributed €14.2M.   ← AI answer opener
```

---

## Visual foundations

### Mood
Bold, powerful, futuristic — operating advanced technology that's still intuitive. Notion/Arc clarity crossed with Perplexity/ChatGPT source attribution. **Never** looks like a demo, tutorial, or generic SaaS dashboard.

### Color
- **Monochrome + one accent.** Neutrals in OKLCh from near-black `oklch(0.12 0 0)` to near-white `oklch(0.98 0 0)`. That's the whole palette.
- **Accent: `oklch(0.79 0.18 70.67)`** — a warm yellow-green. It's the only chromatic hue in the system.
  - Used for: primary CTA, active nav, the currently-selected node on the graph, the single highlight series in a chart, mission-critical status.
  - NOT used for: success toasts, generic "nice" accents, hover backgrounds, gradients-for-gradients'-sake. Overuse dilutes its power — one accent per screen is the ideal.
- **Semantic.** Destructive borrows a desaturated red; success/info/warning are muted — they never compete with the accent.
- **Dark mode is primary.** Light mode works but is secondary; test features in dark first.

### Typography
- **Inter Variable (400–700)** for all UI. Default body 14–16px / line-height 1.5.
- **JetBrains Mono Variable** for code, entity IDs, graph queries, monospace metadata.
- **Tracking.** Display sizes (≥ 24px) use `-0.01em` to `-0.02em`. Body is neutral. Small labels (`<12px`, uppercase) use `+0.06em` letter-spacing.
- **No display serifs, no script faces.** Ever.

### Spacing & rhythm
- **4 px base grid.** Tokens: 4 / 8 / 12 / 16 / 24 / 32 / 48.
- **16 px is the standard gutter** inside cards; 24 px is card padding; 48 px separates sections.
- **Alignment is ruthless.** Left-align everything except numbers (right-align in tables).

### Corners
- **`0.5rem` (8 px) is the default radius** on cards, buttons, inputs, chips.
- Use `4 px` on entity tags and inline chips, `16 px` on modals, and full-pill on filter chips + avatar. Never use hard squares; never use `2xl+` as a "fun" treatment.

### Surfaces, glass & depth
- The signature surface is **translucent glass**: `background: oklch(0.21 0 0 / 0.55)` + `backdrop-filter: blur(24px)` + `1 px` hairline border at `oklch(1 0 0 / 0.1)`.
- Apply glass to: **sidebars, top nav, floating toolbars, modal shells, ask composer**.
- Apply opaque cards (`--card`, no blur) to: **list rows, inline cards, dashboard tiles**. Glass-on-glass is forbidden — one layer of blur per region.
- Depth ladder:
  1. Page background (near-black)
  2. Glass chrome (nav, sidebar) — blur over background
  3. Opaque cards (content)
  4. Popover / tooltip — opaque + `0 10 15px rgba(0,0,0,.4)` shadow
  5. Modal — opaque + spring entrance + dim backdrop

### Borders
- Neutral border is `oklch(1 0 0 / 0.1)` in dark, `oklch(0 0 0 / 0.1)` in light. Always `1 px`. Never 2 px unless it's a focus ring or dashed dropzone.
- Focus ring: `0 0 0 2px oklch(0.55 0 0 / 0.35)` (neutral, not accent — accent is reserved).

### Shadows
- `sm 0 1 2px rgba(0,0,0,.3)` resting
- `md 0 4 6px rgba(0,0,0,.35)` elevated
- `lg 0 10 15px rgba(0,0,0,.4)` popover
- `xl 0 20 25px rgba(0,0,0,.5)` modal
- **Accent glow** `0 0 20px oklch(0.79 0.18 70.67 / .35)` — used **once** per screen at most, on the element that deserves attention (live job running, graph node focused).

### Backgrounds & imagery
- Default background is flat near-black. Occasionally a **very soft radial glow** at 15 % opacity behind hero content to telegraph depth — never a hard gradient.
- No stock photography. No hand-drawn illustrations. If imagery appears, it's either:
  - Real screenshots of the product (dark UI, sharp)
  - Graph visualizations (OG hero = nodes + edges as the illustration)
  - Abstract noise/grain overlays at ≤ 5 % opacity
- **Avoid** bluish-purple gradients, rainbow mesh, emoji cards, illustrated characters.

### Motion
Framer Motion. **Animation communicates state changes or spatial relationships — never decoration.**
- **Entrance.** `opacity 0→1, y 20→0`, 300–400 ms, `ease-out`, `50 ms` stagger for list items.
- **Exit.** 200 ms `ease-in`, opacity only (no y).
- **Modal.** Spring physics: `damping 25, stiffness 300`.
- **Micro-interactions.** 150–200 ms, `ease-out`.
- Hover: color/border shift only, no movement. Press: subtle `scale(0.98)` on primary buttons only.
- Graph: edges draw in, nodes pop-in sequentially on initial layout. Never pan/zoom without user intent.

### Hover / press / focus states
- **Hover.** On primary: 90 % opacity of fill. On ghost/secondary: fade in `--muted` bg. No brightness shifts.
- **Press.** `scale(0.98)` + 200 ms ease-out, primary buttons only.
- **Focus.** Neutral 2 px outer ring, offset 2 px. The ring is neutral — accent fills, not borders, indicate active state.
- **Active nav item.** Background fills with accent; text becomes `--accent-fg` (near-black).

### Transparency & blur — when
- **Yes:** app chrome (sidebar, top bar, ask composer, command palette), modal shells
- **No:** data surfaces (tables, cards, charts). Blur destroys perceived precision and contrast-locked numbers are the whole product.

### Protection gradients & capsules
- When text overlays a busy surface (graph canvas, imagery), wrap it in a **capsule** (pill-shaped glass chip) rather than adding a gradient overlay. Protection gradients are only used at the top/bottom of scrolling regions inside the graph canvas (24 px fade to canvas bg).

### Layout rules
- **Fixed elements:** sidebar (left), top bar, optional right inspector panel. Content scrolls between them.
- **Max content width:** 1440 px for dashboards, 800 px for reading/chat, full-bleed for graph canvas.
- **Densest areas** (job queue, entity lists) use 12 px row padding; **calmest areas** (ask view) use 32+ px.

### Cards
- Opaque, `--card` bg, 1 px border, 8 px radius, 16–24 px padding, resting shadow `sm`.
- Never rely on shadow alone for separation — always 1 px border.
- Never use the colored-left-border trope.

---

## Iconography

- **Lucide React, exclusively.** All icons in the product are Lucide outline icons at 1.5–2 px stroke, `currentColor` fill `none`. We do not mix icon libraries.
- **Size ladder.** 14 px inside chips/badges, 16 px inline with text, 20–22 px as feature icons on cards, 24 px for primary toolbar actions, 32 px only for empty states / drop zones.
- **Color.** Icons inherit `--fg1` or `--fg2` depending on context. The accent color is used on an icon **only** when it represents an active selection, a live job, or the "network/graph" concept (the product's core metaphor).
- **No emoji in product UI.** Not in empty states, not in toasts, not in marketing body copy. The product's tone is technical — emoji breaks it. Exceptions: speaker notes only.
- **No Unicode-as-icon.** Use Lucide `ArrowRight`, not `→`, in any icon slot. `→` is fine inside **prose** (copy, headings) but never as a button glyph.
- **No PNG icons.** All icons must be SVG (sharp at any zoom level for the graph canvas).
- **How to reference Lucide.** Either import from `lucide-react` in JSX, or use the inline SVG shapes shown in `preview/icons.html` as a copy-paste source. When prototyping in plain HTML, pull from the Lucide CDN: `https://cdn.jsdelivr.net/npm/lucide@latest`.
- **Logo.** `assets/logo.svg` — monochrome wordmark, min 24 px height. Never tint the logo; never apply effects.

---

## Caveats & flags

- **No color tokens pulled from the repo yet** — OKLCh values are computed from the user's brief. Treat them as canonical until the `mocaOS/cortex-app` repo token file confirms/overrides.
- **Fonts:** the user uploaded Inter Variable + JetBrains Mono Variable (both Italic & Roman). No substitutions needed.
- **Iconography is CDN-linked (Lucide).** If the repo ships an icon sprite, swap to that when building production code.

---

## Quick start (for agents)

1. `<link rel="stylesheet" href="colors_and_type.css">` — this sets up `@font-face`, tokens, and semantic CSS vars.
2. Add `<html class="dark">` (default) or leave off for light mode.
3. Copy what you need from `preview/*.html` or `ui_kits/library/`.
4. Never invent a new color. Never use emoji. One accent per screen.
