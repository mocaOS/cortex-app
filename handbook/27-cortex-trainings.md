# Chapter 27: Cortex Trainings

A knowledge base answers questions people think to ask. It does nothing for the questions they don't know they should be asking — which is most of onboarding, most of compliance, and most of "how do we actually do this here". That gap is normally filled by training material, and training material is expensive enough that it gets written once and then quietly goes stale. **Cortex Trainings** closes the gap by generating it from the knowledge you already have.

Cortex Trainings is a standalone application: its own deployment, its own users, connected to your Cortex backend purely through the [REST API](15-api-reference.md) with a read-only key. You describe a topic and an audience; an agent researches your instance and writes a complete curriculum; you approve that curriculum as a plain document; only then does it generate video, narration, animations and interactions, and deliver the finished course as a **single HTML file** that runs offline on double-click. Where the [Apps](24-apps.md) chapter describes small web apps that run *inside* your instance, this is the other kind — like [Cortex Chat](25-cortex-chat.md), it treats the instance as a service.

The source lives at [github.com/mocaOS/cortex-trainings](https://github.com/mocaOS/cortex-trainings), and it is listed alongside the rest of the ecosystem on the [Cortex apps page](https://cortex.eco/apps).

## What it gives you

- **Courses built from your own documents.** Every claim traces back to something in your instance, and the curriculum cites the documents it used, with dates.
- **A written approval gate.** The content is finished and signed off as a document before a cent is spent on media — which is also the only form a legal, compliance, or subject-matter reviewer can actually review.
- **One file as the deliverable.** No LMS required, no hosting, no accounts. Mail it, drop it in Drive, upload it to an LMS if you have one.
- **Interactive, not a video wall.** Levels, XP, badges, quizzes, sorting tasks, myth-or-fact cards, a final check, a printable cheat sheet, and progress that survives closing the tab.
- **Any language.** Learner-facing text and the voiceover follow the language you choose per training; the interface language is configured separately.
- **Honest gaps.** When the knowledge base doesn't cover something, the curriculum says so and asks, instead of quietly filling the hole with plausible general knowledge.

## The two-part workflow

The workflow is deliberately split, and the split is the point.

**Part 1 — Curriculum.** You give four inputs the app refuses to guess: topic and learning objectives, audience and prior knowledge, content language, and desired duration (which determines the number of levels). The agent then researches your instance — a broad fan-out of deep-research queries before it writes anything, and further queries to fill gaps as it goes — and produces `curriculum.md`: a fact sheet, learning objectives, a level table, and per level the teaching text, the voiceover script, a media plan, and the fully written-out interaction. Plus a final check, a cheat sheet, cited sources, and a production estimate. This costs a handful of queries and some agent tokens.

**Part 2 — Production.** Only reachable after explicit approval. The curriculum becomes binding: what gets produced is what the document says. Media generation runs as a resumable pipeline with live progress, and it pauses twice for human judgement — once to choose the guide character's reference image, once to confirm the quoted video cost before anything is generated.

Why split them: a text change in Part 1 is free. The same change after production means a new voiceover, a new render, and possibly a new film. Video dominates the cost of a training by an order of magnitude, so the entire design pushes decisions earlier, when they are still cheap.

## What a training unit is

A single self-contained HTML file, typically 10–15 MB, structured as levels. Each level pairs one medium with one interaction:

| Medium | Used for |
|---|---|
| Film (generated video) | Story moments, emotion, the opening hook. Expensive — two or three per training at most. |
| Animation | Concepts, lists, rules, processes, numbers. Rendered locally, free, and razor-sharp text in any language — which generative video cannot do. |
| Image | Context for an interaction screen. Nearly free. |

Interactions vary per level on purpose: repetition is what makes e-learning feel like a chore. A guide character — always an abstract object, never a human, because abstract objects stay consistent across image generations — carries the visual identity, in a single accent colour applied throughout.

## The key model

Cortex Trainings only ever reads. Give it a **read-only key** (`cortex_ro_…`), and prefer one scoped to the collections a training may draw on — the exposure of a leaked key is then bounded to content you already chose to expose. See [Chapter 5: Security](05-security.md) for the key model.

Two instance flags must be enabled, because the research phase relies on agentic retrieval: `ENABLE_AGENTIC_RAG` and `ENABLE_AGENT_RESEARCH`. The app reads through `POST /api/search`, `POST /api/ask/stream`, the community and graph endpoints, and `GET /api/documents/{id}/content` for primary sources.

The key stays server-side in the app's own deployment and is never exposed to a browser, which also means the app's origin does not need to appear in your instance's CORS allowlist.

## Costs and the gates

Two things cost money, and they are on opposite ends of the scale.

Research and curriculum writing cost a few queries against your instance plus agent tokens — cents. Video generation costs dollars, priced per second, and is quoted live before anything runs. A four-level training with one short film lands around five to seven dollars in total; a longer one with two films roughly doubles that. Animations cost nothing.

That asymmetry is why the pipeline is shaped the way it is: research generously, generate once. The single most effective lever is the one the curriculum itself will usually suggest — solve a concept as a free animation instead of a film.

## Setting it up

### Prerequisites

- Node 22 or newer
- `ffmpeg` and `ffprobe` on `PATH`
- A headless Chromium (for rendering animations)
- A Cortex instance with `ENABLE_AGENTIC_RAG` and `ENABLE_AGENT_RESEARCH` enabled, and a read-only API key
- An account with the AI provider the app uses for generation

### Configuration

Configuration is entirely environment variables: the instance base URL and read-only key, the provider key and the model choices for agent, image, video and speech, the interface language, the accent colour, and a storage path for generated projects. The repository's `.env.example` is the canonical list, and `docs/configuration.md` in that repo explains each value with tested defaults.

### First run

1. Point the app at your instance and confirm it can read — the briefing form offers topic suggestions drawn from your knowledge base when the connection works.
2. Create a project and fill in the four briefing inputs.
3. Start the research run and wait a few minutes; review the curriculum it writes.
4. Request changes in the chat until the document is right. This is free — use it.
5. Approve, then pick the guide-character reference image and confirm the video quote.
6. Download the finished HTML file and click through it before sending it to anyone.

Projects are stored as plain files — every curriculum version, the production plan, per-step state, and all media — so a run can be inspected, resumed after a failure, or recovered.

## Where to go next

- [Chapter 24: Apps](24-apps.md) — the other kind of app: small web apps that run inside your instance
- [Chapter 25: Cortex Chat](25-cortex-chat.md) — the other standalone app, for everyday question answering
- [Chapter 10: Ask AI](10-ask-ai.md) — the retrieval and deep-research capabilities the curriculum phase builds on
- [Chapter 5: Security](05-security.md) — API keys, scopes, and collection-scoped access
- [Chapter 15: API Reference](15-api-reference.md) — the endpoints a standalone app consumes
