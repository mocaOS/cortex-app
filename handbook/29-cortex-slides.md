# Chapter 29: Cortex Slides

Most decks made from a knowledge base inherit the knowledge base's looks: a template, a logo in the corner, walls of bullet points. **Cortex Slides** inverts that. It researches your instance for the substance, has you approve every word of the outline, and then has an image model paint each slide *in full* — typography, artwork and layout as one designed page — delivering a `.pptx` with speaker notes plus a PDF for single-digit dollars per deck. It is currently in **beta**.

Cortex Slides is a standalone application: its own deployment, connected to your Cortex backend purely through the [REST API](15-api-reference.md) with a read-scoped key. Where the [Apps](24-apps.md) chapter describes small web apps that run *inside* your instance, this — like [Cortex Chat](25-cortex-chat.md), [Cortex Trainings](27-cortex-trainings.md) and [Cortex Videogen](28-cortex-videogen.md) — treats the instance as a service. It is the third sibling of Trainings and Videogen and shares their architecture DNA: a resumable file-backed pipeline, human gates before every spend, per-artefact caching, two-channel research grounding.

The source lives at [github.com/mocaOS/cortex-slides](https://github.com/mocaOS/cortex-slides) and is open source.

## What it gives you

A 6–30 slide deck — pitch, training, or report, each with its own narrative structure — in your chosen content language, delivered as:

- **A `.pptx`** with one full-bleed painted image per slide and real, editable **speaker notes** in the notes pane (~60–120 spoken words per slide — the slides carry the skeleton, the notes carry the argument).
- **A PDF** of the same pages for sharing.
- **Full-image slides.** gpt-image-2 (via [Venice.ai](https://venice.ai), the sole AI provider) paints the entire slide from a prompt that combines the layout's composition brief, the slide's exact approved text, its artwork concept and a deck-wide design system. Eight layouts (title, section, bullets, statement, image-full, two-column, quote, data) keep the deck varied without ever looking templated.
- **A design system you approve from a finished page** — ~12 presets, and the sample you sign off on is a fully painted slide from *your* deck, not a color swatch. Approved styles save to a cross-project library.
- **Consistency from reference images** — optional subject uploads (1–3) keep a product or mascot recognizable across slides; style uploads lock the aesthetic.

The trade of full-image slides is explicit: text on the slides is painted pixels, not editable PowerPoint text, and image models occasionally misspell. The whole workflow is shaped around that — see the gates below.

## The workflow and its three gates

One form starts a project: topic, audience, purpose, content language, slide count, text density, an optional call-to-action (carried verbatim onto the final slide), optional sections for long trainings, optional reference images, and optionally a collection to scope research to.

Production then runs six steps, pausing three times for a human:

1. **Research** — two-channel grounding against your instance: agentic Deep Research writes the argument, verbatim evidence excerpts from hybrid search keep exact names and numbers intact through summarization, and the knowledge graph's topic communities are appended as outline fuel. Sectioned decks get one pass per section. Free.
2. **Outline** — *the content gate.* The full deck is drafted and shown next to the research it is grounded in. Approve it (optionally with per-slide hand edits, applied verbatim — your words outrank the model's), regenerate with feedback, or re-run the research with steering — all free, as many rounds as needed. This gate carries more weight than in the sibling apps: **every word approved here is painted into pixels**, so proofread the outline like it's the deck. It is.
3. **Style** — *the look gate.* Three presets are proposed with rationale, then the deck's most representative slide is painted in full as a paid sample (one image; cached per preset, so comparing styles never re-pays). Approve, switch preset, or give feedback for a revised look.
4. **Visuals** — *the cost gate.* Every missing slide image is quoted against live pricing and the run waits for confirmation. The approved sample already counts as generated. With reference uploads, every slide is conditioned on them; without, generation is seeded and reproducible.
5. **Assembly** — pure packaging into `.pptx` and PDF; nothing is generated here.
6. **QA** — structural probes (slide count matches the outline, notes present, PDF pages), ending by naming what it cannot verify: the painted text. Proofread every slide.

Every artefact is cached on disk; a resume regenerates only what is missing.

**After delivery**, the deck stays editable at the right granularity: speaker-note changes are free (they live in the notes pane, not the image), and a text or artwork fix on one slide answers with a live one-image quote, then repaints exactly that slide. That per-slide loop is the designed remedy for painted-text errors — not a reason to regenerate a deck.

## Costs

Slide images are effectively the entire spend; research and outline are cents. Reference: **~$0.50 per slide** on gpt-image-2 at 2K/high — a 12-slide deck lands around **$6**, quoted exactly before anything is generated. Each style-sample round costs one image, and fixing one slide later costs one image.

## Setting it up

Cortex Slides is a Node.js application (Node 22+):

```bash
git clone https://github.com/mocaOS/cortex-slides
cd cortex-slides
npm install
cp .env.example .env
npm run dev            # http://localhost:3020
```

Three values are required in `.env`:

| Variable | What it is |
|----------|-----------|
| `VENICE_API_KEY` | Venice.ai key — pays for research/outline LLM calls and slide images |
| `CORTEX_BASE_URL` | Your instance URL |
| `CORTEX_API_KEY` | A **read**-scoped key ([Chapter 5](05-security.md)), ideally collection-scoped |

Optional: `ACCENT_COLOR` (hex) and `BRAND_NAME` feed the presets that use them; `APP_LANG` switches the app UI (en/de) — the deck's content language is chosen per project; `IMAGE_RESOLUTION` (1K/2K) and `IMAGE_QUALITY` pick the image tier. The instance needs `ENABLE_AGENTIC_RAG` and `ENABLE_AGENT_RESEARCH` for the research step, and `npm run smoke` validates the whole packaging path with locally drawn placeholders for $0.

The operator note from the sibling apps applies unchanged — **content quality in = quality out** — plus one of its own: treat the outline gate as the proofreading pass. A typo approved there is a typo painted onto a slide, and while one slide repaints cheaply, reading carefully once is cheaper still.

## Where to go next

- [Chapter 27: Cortex Trainings](27-cortex-trainings.md) — the sibling standalone app, for interactive courses
- [Chapter 28: Cortex Videogen](28-cortex-videogen.md) — the sibling standalone app, for marketing videos
- [Chapter 25: Cortex Chat](25-cortex-chat.md) — the standalone app for everyday question answering
- [Chapter 10: Ask AI](10-ask-ai.md) — the deep-research capability the research step builds on
- [Chapter 5: Security](05-security.md) — API keys, scopes, and collection-scoped access
