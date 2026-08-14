# Chapter 28: Cortex Videogen

A knowledge base full of product facts, feature docs and real numbers is most of a marketing video — the substance is there, the production isn't. What normally stands between the two is an agency, an editor, and a budget measured in thousands. **Cortex Videogen** collapses that distance to a form and two approvals: it researches your instance for grounded claims, directs AI footage around them, and assembles a finished video with narration, subtitles and a call-to-action — for single-digit dollars and roughly half an hour end to end.

Cortex Videogen is a standalone application: its own deployment, connected to your Cortex backend purely through the [REST API](15-api-reference.md) with a read-scoped key. Where the [Apps](24-apps.md) chapter describes small web apps that run *inside* your instance, this — like [Cortex Chat](25-cortex-chat.md) and [Cortex Trainings](27-cortex-trainings.md) — treats the instance as a service.

The source lives at [github.com/mocaOS/cortex-videogen](https://github.com/mocaOS/cortex-videogen).

## What it gives you

A 15–120 second marketing video, landscape (16:9) or native vertical (9:16), delivered as a publish-ready MP4 master plus a lighter social copy. Inside it:

- **AI footage directed as choreographed one-take shots** — internal beat timelines and continuous camera journeys, not a slideshow of static clips. [Venice.ai](https://venice.ai) is the sole media provider.
- **Gapless narration** — per-sentence TTS with an exact timeline; shot lengths derive from the voiceover, so speech flows continuously across cuts.
- **Burned-in karaoke subtitles** — the only text layer over footage, because the large majority of feed video plays muted; the opening line renders larger as the written hook.
- **A uniform color grade** and a locally rendered **CTA end card** in your brand accent — crisp typography that generative video cannot produce, at zero generation cost.
- **Consistency from reference images** — optional character/product uploads (1–3) keep the same subject recognizable across every shot; style uploads lock the aesthetic. Every shot is generated from a reference-conditioned start frame.

## The workflow and its two gates

One form starts a project: topic, audience, call-to-action, format, length, a pacing preset, the video model and resolution (picked from Venice's live catalog — draft on a cheap model, publish at maximum quality), optional reference images, and optionally a collection to scope research to.

Production then runs six steps, pausing twice for a human:

1. **Research** — agentic Deep Research against your instance, with two-channel grounding: the synthesized brief *plus* verbatim evidence excerpts from hybrid search, so exact names and numbers survive summarization.
2. **Storyboard** — *the content gate.* A retention-first storyboard is drafted and shown next to the research it is grounded in. Approve it, regenerate it with feedback, or re-run the research with steering ("list the registry apps by name") — all free, as many rounds as needed. The generation models are forbidden to invent facts; your feedback counts as binding grounding.
3. **Voiceover** — synthesized per sentence for an exact caption timeline.
4. **Video generation** — *the cost gate.* Every shot and start frame is quoted against live pricing and the run waits for confirmation. Queued jobs are ticket-tracked, so even a crash mid-generation never pays for the same clip twice.
5. **Assembly** — a deterministic offline render composites footage, captions, grade, audio and the CTA card.
6. **QA** — the file is probed for duration, orientation and deliverables before the run reports done.

Every artefact is cached on disk; a resume regenerates only what is missing, and regenerating one shot costs one shot.

## Costs

Reference: a 30-second video runs roughly **$5–7 of footage** on the default MiniMax model at 2K, plus ~$0.35 per generated start frame — the quote gate shows the exact figure for your storyboard before anything is spent. Model and resolution are per-project choices, so a cheap test render and a maximum-quality final are the same form with one dropdown changed.

## Setting it up

Videogen is a Node.js application (Node 22+; ffmpeg is bundled, and the first render fetches a pinned headless browser once):

```bash
git clone https://github.com/mocaOS/cortex-videogen
cd cortex-videogen
npm install
cp .env.example .env
npm run dev            # http://localhost:3010
```

Three values are required in `.env`:

| Variable | What it is |
|----------|-----------|
| `VENICE_API_KEY` | Venice.ai key — pays for the storyboard LLM, images, TTS and video |
| `CORTEX_BASE_URL` | Your instance URL |
| `CORTEX_API_KEY` | A **read**-scoped key ([Chapter 5](05-security.md)), ideally collection-scoped |

Optional: `ACCENT_COLOR` and `BRAND_NAME` brand the captions and end card; `APP_LANG` switches the app UI (en/de) — the video's content language is chosen per project.

One operator note that applies to Trainings just as much: **content quality in = quality out.** These apps surface what the graph knows, and both show you the research precisely so a coverage gap is caught at the storyboard gate instead of in the finished video. A vague topic retrieves the knowledge base's dominant general cluster — write descriptive topics that use the specific subject's vocabulary.

## Where to go next

- [Chapter 27: Cortex Trainings](27-cortex-trainings.md) — the sibling standalone app, for interactive courses
- [Chapter 29: Cortex Slides](29-cortex-slides.md) — the sibling standalone app, for presentation decks
- [Chapter 25: Cortex Chat](25-cortex-chat.md) — the standalone app for everyday question answering
- [Chapter 10: Ask AI](10-ask-ai.md) — the deep-research capability the research step builds on
- [Chapter 5: Security](05-security.md) — API keys, scopes, and collection-scoped access
