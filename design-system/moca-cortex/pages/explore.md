# Explore / Graph Page Override

> Overrides `MASTER.md` for the knowledge graph visualization.

## Layout

- Full-width graph canvas, no max-width constraint
- Side panel: `glass` surface, `w-80`, overlays on mobile
- Tabs for: Entities, Relationships, Communities, Deduplication

## Components

### KnowledgeGraph (react-force-graph-2d)
- Background: `var(--background)`
- Node color: `var(--muted-foreground)` default, `var(--accent)` for selected/highlighted
- Link color: `var(--border)` with `0.3` opacity
- Node labels: `var(--foreground)`, `12px` Inter
- Hover: node glow effect using accent color

### Entity Browser
- List items: `glass-hover` treatment
- Entity type badges: `bg-muted text-muted-foreground rounded-sm px-2 py-0.5`
- Selected entity: `border-accent` left border indicator

### Community Browser
- Community cards: `glass` with member count badge
- Color coding: use `--chart-1` through `--chart-5` for community groups

## Animation
- Graph: physics simulation (no Framer Motion — handled by force-graph)
- Panel slide: `300ms ease-out` from right
- Tab switch: crossfade `200ms`
