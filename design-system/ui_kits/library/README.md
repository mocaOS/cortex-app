# MOCA Library — Web App UI Kit

Pixel-faithful recreation of the MOCA Library web app. Dark-mode, glassmorphic, Lucide icons, OKLCh colors.

## Files
- `index.html` — the kit entry point. Interactive click-through with sidebar routing between the three core screens.
- `kit.css` — shared component styles (buttons, inputs, chips, cards, shell layout).
- `Icons.jsx` — Lucide-style inline SVG icon set. Outline, 2 px stroke, inherits `currentColor`.
- `Shell.jsx` — `Sidebar` + `Topbar` + `AppShell` wrapper.
- `ManageScreen.jsx` — dashboard with stats, document list (selectable, filterable), active-jobs side panel.
- `ExploreScreen.jsx` — knowledge-graph canvas with entity-type legend, floating toolbar, zoom capsule, and right-hand inspector with properties + relations + sources.
- `AskScreen.jsx` — source-attributed Q&A. Empty-state suggestions, message thread, glass composer with "thinking" pulse and cited source chips.

## Interactive behavior
- Click any of the three top-level nav items (`Manage` / `Explore` / `Ask`) — routes are persisted to `localStorage`.
- **Manage:** click checkboxes to select documents; click filter pills ("Completed", "Processing"…) to filter the list.
- **Explore:** click any node on the graph to focus it — the inspector updates, related edges light up in accent.
- **Ask:** click any suggestion card OR type + press Enter → simulated answer with source chips appears after a short "thinking" state.

## Coverage (components available to lift)
- **Shell:** `Sidebar`, `Topbar`, `AppShell`
- **Buttons:** `.btn` with variants `-primary` / `-secondary` / `-ghost` / `-outline` / `-icon` / `-sm` / `-lg`
- **Inputs:** `.input`, glass composer pattern (see `AskScreen.jsx`)
- **Cards:** `.card`, plus domain cards (`Stat`, `DocRow`, `JobRow`, `AIAnswer`)
- **Chips:** `.chip` (+ `-active`, `-pill`, `-entity`, `-outline`)
- **Status pills:** `StatusChip` with `ok` / `proc` / `fail` / `q` variants
- **Graph:** inline SVG `Graph` component with focus state, glow filter, accent edges
- **Inspector:** entity detail panel with properties, relations, and source list

## Known omissions
This is a visual recreation, not a production app. Real ingest, real graph queries, real LLM calls are all stubbed. Settings and Collections navigation in the sidebar are non-interactive placeholders.
