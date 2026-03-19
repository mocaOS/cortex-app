# Documents Page Override

> Overrides `MASTER.md` for document management.

## Layout

- Filter bar: sticky below header, `glass` surface
- Document list: `flex flex-col gap-3`
- Bulk action bar: appears on multi-select, sticky bottom

## Components

### DocumentCard
- Surface: `glass glass-hover`
- Status indicator: accent dot for processing, success for complete, destructive for error
- Progress bar: `bg-accent` fill on `bg-muted` track, `h-1 rounded-full`
- Actions: ghost buttons, right-aligned
- File type icon: Lucide (not emoji)

### Upload Zone
- `drop-zone` class with dashed border
- Active state: `border-foreground bg-muted`
- File type indicators: Lucide icons with labels

### Filters
- Filter chips: `bg-secondary text-secondary-foreground rounded-md px-3 py-1`
- Active filter: `bg-accent text-accent-foreground`
- Search input: `glass` with Lucide Search icon

## Animation
- Card entrance: stagger `0.02s`, `opacity + y` transition
- Upload progress: smooth width transition `300ms`
- Bulk action bar: slide-up `200ms` from bottom
