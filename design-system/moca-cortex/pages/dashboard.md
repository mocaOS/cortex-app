# Dashboard Page Override

> Overrides `MASTER.md` for the main dashboard / stats view.

## Layout

- Stats grid: `grid-cols-2 sm:grid-cols-4` with `gap-4`
- Each stat card uses `glass` surface with animated count-up values
- Section spacing: `mb-6` between stat grid and content area

## Components

### StatsCard
- Icon: Lucide, `20px`, `text-accent` when active
- Value: `text-2xl font-bold` with Framer Motion number animation
- Label: `text-sm text-muted-foreground`
- Surface: `glass glass-hover` with `p-4 rounded-lg`

## Animation
- Stats entrance: stagger `0.05s` per card, `opacity: 0, y: 20` → `opacity: 1, y: 0`
- Values: count-up animation over `400ms`

## Charts
- Use Recharts with monochrome `--chart-1` through `--chart-5`
- Accent color for the primary/highlighted series only
- Grid lines: `var(--border)` opacity
- Always show legend and tooltip
