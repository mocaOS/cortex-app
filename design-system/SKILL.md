---
name: moca-library-design
description: Use this skill to generate well-branded interfaces and assets for MOCA Library, either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

## Quick reference
- **Accent color (only chromatic):** `oklch(0.79 0.18 70.67)` — use sparingly.
- **Fonts:** Inter (UI), JetBrains Mono (code). Both variable, in `fonts/`.
- **Radius default:** `0.5rem`. Glass blur: `24px`.
- **Icons:** Lucide React, outline, 2 px stroke. No emoji.
- **Motion:** opacity 0→1, y 20→0, 300–400 ms, 50 ms stagger.
- **Dark mode is primary.** Add `class="dark"` to `<html>`.
- **Every answer shows sources.** Every job shows status. Trust through transparency.

## File map
- `README.md` — full content & visual foundations
- `colors_and_type.css` — canonical tokens + `@font-face` (import this)
- `tokens.css` — token-only subset
- `fonts/` — TTFs
- `assets/logo.svg` — wordmark
- `preview/*.html` — live component specimens
- `ui_kits/library/` — pixel-faithful web-app recreation (see its README)
