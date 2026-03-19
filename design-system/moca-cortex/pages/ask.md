# Ask / Chat Page Override

> Overrides `MASTER.md` for the AI Q&A chat interface.

## Layout

- Full-height: `min-h-[calc(100vh-nav)]` with flex column
- Messages area: scrollable, `flex-1`
- Input: fixed/sticky at bottom with `glass` treatment

## Components

### ChatMessage
- User messages: `bg-primary text-primary-foreground rounded-lg p-4`
- AI messages: `bg-card border border-border rounded-lg p-4`
- Source citations: inline links with `text-accent` color
- Thinking blocks: collapsible, `bg-muted rounded-md p-3`
- Graph context: visual badge with entity count

### AskInput
- `glass` surface with `rounded-xl`
- Min height: `44px`, expands on multiline
- Submit button: `bg-accent text-accent-foreground`
- Settings toggle: ghost button with gear icon

## Animation
- Message entrance: `opacity: 0, y: 10` → visible, `200ms`
- Streaming text: no animation (instant render for readability)
- Thinking block expand: spring `damping: 25`

## Empty State
- Centered, `text-muted-foreground`
- Suggested queries as `glass-hover` pills
