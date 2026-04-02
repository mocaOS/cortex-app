# Design System

The project has a portable design system at `design-system/moca-cortex/`:

## Files

- `MASTER.md` — Complete design spec: colors (OKLCh), typography (Inter + JetBrains Mono), spacing, glass morphism, animation tokens, component patterns, accessibility checklist, z-index scale, and anti-patterns. This is the source of truth for all visual decisions.
- `tokens.css` — Drop-in CSS file with all custom properties (light + dark themes), glass/glow/shimmer classes. Import this into any new project to reuse the design system.
- `tailwind.preset.ts` — Tailwind preset with all color/font/radius tokens. Use via `presets: [mocaPreset]` in other projects.
- `pages/*.md` — Page-specific overrides (dashboard, ask, explore, documents) that take precedence over MASTER.md for those pages.

## Key Characteristics

- Monochrome foundation with a single dynamic accent color (`oklch(0.79 0.18 70.67)`, configurable via `NEXT_PUBLIC_ACCENT_COLOR`)
- Dark mode default
- Glass morphism surfaces (24px blur)
- Framer Motion animations
- Lucide icons only

## Additional Reference

Design context and principles are also documented in `.impeccable.md` at the repo root.
