# Cortex Design System

> **Source of Truth** for all Cortex projects.
> When building a specific page, check `design-system/pages/[page-name].md` first.
> If that file exists, its rules **override** this Master file.
> If not, follow the rules below exclusively.

---

**Project:** Cortex
**Category:** AI Knowledge Base / SaaS Tool
**Primary Theme:** Dark mode (default)
**Style:** Monochrome glassmorphism with single chromatic accent
**Generated:** 2026-03-19
**Stack:** Next.js 15, React 19, TypeScript, Tailwind CSS 3.4, Framer Motion

---

## Brand Identity

**Personality:** Bold, powerful, futuristic — a next-generation knowledge system. Mission control meets modern design tool.

**Emotional Goals:**
- **Confidence & control** — Users feel mastery over their knowledge base
- **Trust & reliability** — Transparent source attribution, consistent behavior

**Anti-references:** Generic SaaS dashboards, cluttered enterprise UIs, overly playful/casual aesthetics. Never look like a prototype or demo.

---

## Design Principles

1. **Accent with intent** — The accent color is the single chromatic color. Use it deliberately for CTAs, active states, and key data points. Every use draws the eye to something actionable. Overuse dilutes its power.

2. **Clarity over density** — Serve mixed audiences by making complex information scannable. Progressive disclosure: surface the essential, let users drill deeper.

3. **Motion as communication** — Animations convey state changes and spatial relationships, never decorate. Staggered entrances guide reading order. Remove any animation without communicative purpose.

4. **Glass and depth** — Glass morphism creates visual hierarchy through layering. Background content bleeds through subtly. Use blur and transparency to separate content planes.

5. **Trust through transparency** — Every AI answer shows sources. Every job shows status. Every result shows relevance signals. The system never hides how it arrived at a result.

---

## Color System

All colors use **OKLCh** color space for perceptual uniformity. The accent color is dynamically configurable via `NEXT_PUBLIC_ACCENT_COLOR` environment variable.

### Semantic Tokens

| Token | Light Mode | Dark Mode | Usage |
|-------|-----------|-----------|-------|
| `--background` | `oklch(1.0 0 0)` | `oklch(0.1448 0 0)` | Page background |
| `--foreground` | `oklch(0.1448 0 0)` | `oklch(0.9851 0 0)` | Primary text |
| `--card` | `oklch(1.0 0 0)` | `oklch(0.2134 0 0)` | Card/panel surfaces |
| `--card-foreground` | `oklch(0.1448 0 0)` | `oklch(0.9851 0 0)` | Card text |
| `--popover` | `oklch(1.0 0 0)` | `oklch(0.2686 0 0)` | Dropdown/popover background |
| `--popover-foreground` | `oklch(0.1448 0 0)` | `oklch(0.9851 0 0)` | Dropdown text |
| `--primary` | `oklch(0.2046 0 0)` | `oklch(0.9851 0 0)` | Primary buttons, text emphasis |
| `--primary-foreground` | `oklch(0.9851 0 0)` | `oklch(0.1448 0 0)` | Text on primary surfaces |
| `--secondary` | `oklch(0.9702 0 0)` | `oklch(0.2686 0 0)` | Secondary buttons, subtle backgrounds |
| `--secondary-foreground` | `oklch(0.2046 0 0)` | `oklch(0.9851 0 0)` | Text on secondary surfaces |
| `--muted` | `oklch(0.9702 0 0)` | `oklch(0.2686 0 0)` | Subdued backgrounds, hover states |
| `--muted-foreground` | `oklch(0.5486 0 0)` | `oklch(0.7090 0 0)` | Secondary/placeholder text |
| `--accent` | `oklch(0.79 0.18 70.67)` | `oklch(0.79 0.18 70.67)` | CTAs, active nav, progress, highlights |
| `--accent-foreground` | `oklch(0.1448 0 0)` | `oklch(0.1448 0 0)` | Text on accent surfaces |
| `--destructive` | `oklch(0.583 0.239 28.48)` | `oklch(0.702 0.189 22.23)` | Errors, delete actions |
| `--destructive-foreground` | `oklch(0.9702 0 0)` | `oklch(0.2686 0 0)` | Text on destructive surfaces |
| `--border` | `oklch(0.9219 0 0)` | `oklch(0.3407 0 0)` | Dividers, card borders |
| `--input` | `oklch(0.9219 0 0)` | `oklch(0.4386 0 0)` | Input borders |
| `--ring` | `oklch(0.7090 0 0)` | `oklch(0.5555 0 0)` | Focus rings, scrollbar thumbs |

### Status Colors

| Token | Light Mode | Dark Mode | Usage |
|-------|-----------|-----------|-------|
| `--success` | `oklch(0.5555 0 0)` | `oklch(0.6500 0 0)` | Success states |
| `--warning` | `oklch(0.6500 0 0)` | `oklch(0.7500 0 0)` | Warning states |
| `--info` | `oklch(0.5555 0 0)` | `oklch(0.5555 0 0)` | Informational states |

### Chart Colors (Monochrome Scale)

| Token | Light Mode | Dark Mode |
|-------|-----------|-----------|
| `--chart-1` | `oklch(0.5555 0 0)` | `oklch(0.5555 0 0)` |
| `--chart-2` | `oklch(0.4500 0 0)` | `oklch(0.4500 0 0)` |
| `--chart-3` | `oklch(0.3500 0 0)` | `oklch(0.6500 0 0)` |
| `--chart-4` | `oklch(0.6500 0 0)` | `oklch(0.7500 0 0)` |
| `--chart-5` | `oklch(0.7500 0 0)` | `oklch(0.3500 0 0)` |

### Sidebar Colors

| Token | Light Mode | Dark Mode |
|-------|-----------|-----------|
| `--sidebar` | `oklch(0.9851 0 0)` | `oklch(0.2046 0 0)` |
| `--sidebar-foreground` | `oklch(0.1448 0 0)` | `oklch(0.9851 0 0)` |
| `--sidebar-primary` | `oklch(0.2046 0 0)` | `oklch(0.9851 0 0)` |
| `--sidebar-accent` | `oklch(0.9702 0 0)` | `oklch(0.2686 0 0)` |
| `--sidebar-border` | `oklch(0.9219 0 0)` | `oklch(0.3407 0 0)` |
| `--sidebar-ring` | `oklch(0.7090 0 0)` | `oklch(0.4386 0 0)` |

### Accent Color Rules

- The accent (`oklch(0.79 0.18 70.67)`) is the **only** chromatic color in the entire system
- Same value in both light and dark mode
- Configurable via `NEXT_PUBLIC_ACCENT_COLOR` environment variable
- Use for: CTAs, active navigation states, progress indicators, key data highlights, interactive focus
- Never use for: backgrounds, large areas, body text, decorative borders
- Maximum 2-3 accent elements visible per viewport to maintain impact

---

## Typography

### Font Stack

| Role | Font | Variable | Fallback |
|------|------|----------|----------|
| Sans (UI) | Inter | `--font-geist-sans` | system-ui, sans-serif |
| Mono (Code) | JetBrains Mono | `--font-geist-mono` | monospace |

### Type Scale

| Role | Font | Weight | Size | Line Height | Usage |
|------|------|--------|------|-------------|-------|
| Display | Inter | 700 | 32px | 1.2 | Hero headings, page titles |
| H1 | Inter | 700 | 24px | 1.3 | Section headings |
| H2 | Inter | 600 | 20px | 1.4 | Card titles, subsections |
| H3 | Inter | 600 | 18px | 1.4 | Panel headings |
| Body | Inter | 400 | 14-16px | 1.5-1.75 | Paragraph text, descriptions |
| Label | Inter | 500 | 12-14px | 1.4 | Form labels, metadata |
| Caption | Inter | 400 | 12px | 1.4 | Timestamps, secondary info |
| Code | JetBrains Mono | 400 | 14px | 1.6 | Code blocks, technical data |

### Typography Rules

- Minimum body text: **16px on mobile** (prevents iOS auto-zoom)
- Line length: **60-75 characters** on desktop, **35-60** on mobile
- Use `font-display: swap` to prevent FOIT
- Heading tracking: tighter (-0.5 to -1.5) for display sizes
- Label tracking: wider (+0.5 to +1.2, uppercase) for metadata

### Text Gradient

```css
.text-gradient {
  background: linear-gradient(to right, var(--foreground), var(--muted-foreground));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
```

Use for: decorative headings and hero text only. Never for body text or interactive elements.

---

## Spacing

### Scale (Tailwind + 4px base grid)

| Token | Tailwind | Value | Usage |
|-------|----------|-------|-------|
| `--space-1` | `gap-1`, `p-1` | 4px | Tight icon gaps |
| `--space-2` | `gap-2`, `p-2` | 8px | Inline spacing, icon-text gaps |
| `--space-3` | `gap-3`, `p-3` | 12px | Compact padding |
| `--space-4` | `gap-4`, `p-4` | 16px | Standard content padding |
| `--space-5` | `gap-5`, `p-5` | 20px | Comfortable padding |
| `--space-6` | `gap-6`, `p-6` | 24px | Section padding, card padding |
| `--space-8` | `gap-8` | 32px | Large section gaps |
| `--space-12` | `mb-12` | 48px | Section separators |

### Layout Constants

| Element | Value | Tailwind |
|---------|-------|----------|
| Content max-width | 80rem (1280px) | `max-w-7xl` |
| Content horizontal padding | 24px | `px-6` |
| Content top padding | 24px | `pt-6` |
| Content bottom padding | 48px | `pb-12` |
| Card padding | 16-24px | `p-4` to `p-6` |
| Grid gap (cards) | 16-24px | `gap-4` to `gap-6` |

---

## Border Radius

| Token | Tailwind | Value | Usage |
|-------|----------|-------|-------|
| `--radius` | `rounded-lg` | 0.5rem (8px) | Default: cards, buttons, inputs |
| `--radius - 2px` | `rounded-md` | 6px | Inner elements, badges |
| `--radius - 4px` | `rounded-sm` | 4px | Tags, small chips |
| — | `rounded-full` | 50% | Avatars, circular buttons |

---

## Elevation & Glass

### Glass Morphism (Core Pattern)

```css
.glass {
  background-color: var(--card);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border-width: 1px;
  border-color: var(--border);
}

.glass-hover {
  transition: all 300ms;
}
.glass-hover:hover {
  background-color: var(--muted);
  border-color: var(--ring);
}
```

**Usage rules:**
- Glass is the primary surface treatment for cards, panels, inputs, and navigation pills
- Always pair with `1px` border for edge definition
- Blur radius: `24px` for standard glass, `4px` for modal scrims
- Do NOT use glass on elements over glass — avoid stacking blur effects

### Glow Effects

```css
.glow        { box-shadow: 0 0 20px oklch(0 0 0 / 0.1); }
.glow-accent { box-shadow: 0 0 20px oklch(0 0 0 / 0.15); }
```

Use sparingly — only on hero elements or focused cards.

### Shadow Scale

| Level | Value | Usage |
|-------|-------|-------|
| Subtle | `0 1px 2px oklch(0 0 0 / 0.05)` | Resting cards |
| Medium | `0 4px 6px oklch(0 0 0 / 0.1)` | Elevated cards, buttons |
| Large | `0 10px 15px oklch(0 0 0 / 0.1)` | Dropdowns, popovers |
| XL | `0 20px 25px oklch(0 0 0 / 0.15)` | Modals |

---

## Animation

### Motion Library: Framer Motion

All animation uses `transform` and `opacity` only — never animate `width`, `height`, `top`, or `left`.

### Timing Tokens

| Type | Duration | Easing | Usage |
|------|----------|--------|-------|
| Micro-interaction | 150-200ms | `ease-out` | Hover, focus, toggle |
| Standard transition | 300ms | `ease` | Glass hover, nav state |
| Entrance | 300-400ms | Spring or ease-out | Page/card entrance |
| Complex transition | 400ms max | Spring | Modal open, panel expand |
| Exit | 200-280ms | `ease-in` | Dismissals (60-70% of enter) |

### Entrance Pattern

```tsx
// Standard entrance
initial={{ opacity: 0, y: 20 }}
animate={{ opacity: 1, y: 0 }}
transition={{ duration: 0.3 }}

// Staggered list
staggerChildren: 0.05  // 50ms between items

// Spring modals
type: "spring", damping: 25, stiffness: 300
```

### Shimmer Loading

```css
@keyframes shimmer {
  0%   { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}
.shimmer {
  background: linear-gradient(90deg, transparent 0%, oklch(0.5 0 0 / 0.1) 50%, transparent 100%);
  background-size: 200% 100%;
  animation: shimmer 2s infinite;
}
```

### Animation Rules

- Show skeleton/shimmer for operations > 300ms
- Respect `prefers-reduced-motion` — disable all non-essential animation
- Maximum 1-2 animated elements per viewport
- Stagger list items at 30-50ms intervals
- Exit animations shorter than enter (responsive feel)
- Animations must be interruptible — never block user input
- Scale feedback: `0.97` on press, `1.0` on release for tappable elements

---

## Z-Index Scale

| Layer | Value | Usage |
|-------|-------|-------|
| Base | `0` | Default content |
| Raised | `10` | Sticky elements, cards |
| Dropdown | `20` | Dropdowns, tooltips |
| Sticky | `30` | Sticky headers |
| Header | `50` | Main navigation |
| Modal scrim | `100` | Modal backdrops |
| Modal | `110` | Modal content |
| Toast | `1000` | Notifications |

---

## Component Patterns

### Buttons

| Variant | Background | Text | Border | Usage |
|---------|-----------|------|--------|-------|
| Primary | `accent` | `accent-foreground` | none | CTAs, primary actions |
| Secondary | `secondary` | `secondary-foreground` | none | Cancel, back, secondary actions |
| Ghost | transparent | `foreground` | none | Toolbar, inline actions |
| Destructive | `destructive` | `destructive-foreground` | none | Delete, remove |
| Outline | transparent | `foreground` | `border` | Alternative secondary |

All buttons: `cursor-pointer`, `rounded-lg`, `px-4 py-2`, transition `150-200ms`, disabled `opacity-50`.

### Cards

- Surface: `glass` class (backdrop-blur + border)
- Padding: `p-4` to `p-6`
- Radius: `rounded-lg` (8px)
- Hover: `glass-hover` (background shifts to muted, border to ring)

### Inputs

- Height: minimum 44px (touch target compliance)
- Border: `1px solid var(--input)`
- Focus: `ring` color, `2-3px` ring width
- Radius: `rounded-lg`
- Font size: `16px` minimum (prevents iOS zoom)

### Modals

- Scrim: `rgba(0, 0, 0, 0.5)` + `backdrop-filter: blur(4px)`
- Surface: `card` background, `rounded-xl` (16px), `p-6` to `p-8`
- Shadow: XL level
- Animation: spring entrance (`damping: 25, stiffness: 300`)
- Max width: `500px`, width `90%`
- Must have visible close/dismiss affordance

### Drop Zone

```css
.drop-zone {
  border: 2px dashed var(--border);
  border-radius: 0.5rem;
  padding: 2rem;
  transition: all 300ms ease-out;
}
.drop-zone.active {
  border-color: var(--foreground);
  background-color: var(--muted);
}
```

### Custom Scrollbar

```css
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--muted); border-radius: 4px; }
::-webkit-scrollbar-thumb { background: var(--ring); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--muted-foreground); }
```

---

## Icon System

- **Library:** Lucide React (consistent stroke width, 24px default)
- **Style:** Outline only (never mix filled and outline at same hierarchy)
- **Stroke:** 1.5px or 2px consistently
- **Sizes:** `16px` (inline), `20px` (buttons), `24px` (navigation), `32px+` (feature)
- **No emojis** as structural icons — SVG only
- **Contrast:** minimum 3:1 against background (4.5:1 for small icons)

---

## Chart Guidelines

- **Library:** Recharts
- **Color scheme:** Monochrome `--chart-1` through `--chart-5`, use accent for highlighted data
- **Grid lines:** Low contrast (`--border`), don't compete with data
- **Legends:** Always visible, positioned near chart
- **Tooltips:** On hover, show exact values
- **Accessibility:** Don't rely on color alone — use line styles (solid/dashed/dotted) for series differentiation
- **Empty state:** Show helpful message + action, never a blank chart frame
- **Loading:** Use shimmer skeleton, not empty axes
- **Large datasets (1000+):** Aggregate or sample; provide drill-down

---

## Responsive Breakpoints

| Name | Width | Tailwind | Usage |
|------|-------|----------|-------|
| Mobile | < 640px | default | Single column, stacked |
| SM | 640px | `sm:` | Small tablet |
| MD | 768px | `md:` | Tablet |
| LG | 1024px | `lg:` | Desktop |
| XL | 1280px | `xl:` | Wide desktop |
| 2XL | 1536px | `2xl:` | Ultrawide |

### Responsive Rules

- **Mobile-first** — design for mobile, scale up
- Grid: `grid-cols-1` → `sm:grid-cols-2` → `lg:grid-cols-4`
- No horizontal scroll on any breakpoint
- `min-h-dvh` over `100vh` for mobile viewport
- Content max-width: `max-w-7xl` with `mx-auto`
- Readable line length: constrain with `max-w-prose` for long text

---

## Dark Mode Implementation

- **Mechanism:** `darkMode: ["class"]` in Tailwind, `.dark` class on `<html>`
- **Default:** Dark mode enabled via `className="dark"` on root
- **Accent persists** identically across themes
- **All colors** defined in both `:root` (light) and `.dark` (dark) scopes
- **Destructive** uses lighter variant in dark mode for contrast
- Dark mode is the **primary design surface** — test dark first, then verify light

---

## Accessibility Checklist

### Critical (Must Pass)

- [ ] Text contrast >= 4.5:1 in both light and dark mode
- [ ] Large text contrast >= 3:1
- [ ] Focus rings visible on all interactive elements (2-4px)
- [ ] All icon-only buttons have `aria-label`
- [ ] Tab order matches visual order
- [ ] Form fields have visible labels (not placeholder-only)
- [ ] Color is never the sole indicator of meaning (add icon/text)
- [ ] `prefers-reduced-motion` respected — disable/reduce animations
- [ ] All touch targets >= 44x44px
- [ ] Touch target spacing >= 8px

### Important

- [ ] Skip-to-content link for keyboard users
- [ ] Sequential heading hierarchy (h1 > h2 > h3, no skips)
- [ ] Error messages near the related field with clear recovery path
- [ ] Toasts: `aria-live="polite"`, auto-dismiss 3-5s, don't steal focus
- [ ] Destructive actions use confirmation dialog
- [ ] Modals have visible close affordance and Escape key support
- [ ] Form errors use `aria-live` or `role="alert"`

---

## Component Stack

| Category | Library | Version |
|----------|---------|---------|
| Icons | Lucide React | ^0.469 |
| Animation | Framer Motion | ^11.15 |
| Graph viz | react-force-graph-2d | ^1.29 |
| Charts | Recharts | ^3.7 |
| Markdown | react-markdown + remark-gfm | ^10.1 |
| Code highlighting | react-syntax-highlighter (Prism) | ^16.1 |
| Class merging | clsx + tailwind-merge | ^2.1 / ^2.6 |
| CSS | Tailwind CSS | ^3.4 |
| Fonts | Inter + JetBrains Mono (Google Fonts) | — |

---

## Anti-Patterns (Never Do)

- Emojis as structural icons — use Lucide SVGs
- Hardcoded hex/RGB in components — use semantic CSS variables
- Missing `cursor-pointer` on clickable elements
- Layout-shifting hover transforms — use `transform: scale()` only
- Instant state changes — always use transitions (150-300ms)
- Invisible focus states — focus rings must be visible
- Stacking glass on glass — avoid nested blur
- Pure `#000000` backgrounds — causes OLED smear; use `oklch(0.1448 0 0)`
- Placeholder-only labels on inputs
- Horizontal scroll on mobile
- Animations that block user input
- Decorative-only animations (no communicative purpose)
- `100vh` on mobile — use `min-h-dvh`
- Loading states without feedback (frozen UI)

---

## Portable Setup Guide

To use this design system in a new project:

### 1. Install Dependencies

```bash
npm install tailwindcss@^3.4 framer-motion lucide-react clsx tailwind-merge
```

### 2. Tailwind Config

```typescript
// tailwind.config.ts
import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        background: "var(--background)",
        foreground: "var(--foreground)",
        card: { DEFAULT: "var(--card)", foreground: "var(--card-foreground)" },
        popover: { DEFAULT: "var(--popover)", foreground: "var(--popover-foreground)" },
        primary: { DEFAULT: "var(--primary)", foreground: "var(--primary-foreground)" },
        secondary: { DEFAULT: "var(--secondary)", foreground: "var(--secondary-foreground)" },
        muted: { DEFAULT: "var(--muted)", foreground: "var(--muted-foreground)" },
        accent: { DEFAULT: "var(--accent)", foreground: "var(--accent-foreground)" },
        destructive: { DEFAULT: "var(--destructive)", foreground: "var(--destructive-foreground)" },
        success: { DEFAULT: "var(--success)", foreground: "var(--success-foreground)" },
        warning: { DEFAULT: "var(--warning)", foreground: "var(--warning-foreground)" },
        info: { DEFAULT: "var(--info)", foreground: "var(--info-foreground)" },
        border: "var(--border)",
        input: "var(--input)",
        ring: "var(--ring)",
        chart: { 1: "var(--chart-1)", 2: "var(--chart-2)", 3: "var(--chart-3)", 4: "var(--chart-4)", 5: "var(--chart-5)" },
        sidebar: {
          DEFAULT: "var(--sidebar)", foreground: "var(--sidebar-foreground)",
          primary: "var(--sidebar-primary)", "primary-foreground": "var(--sidebar-primary-foreground)",
          accent: "var(--sidebar-accent)", "accent-foreground": "var(--sidebar-accent-foreground)",
          border: "var(--sidebar-border)", ring: "var(--sidebar-ring)",
        },
      },
      fontFamily: {
        sans: ["var(--font-geist-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "monospace"],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
};
export default config;
```

### 3. CSS Variables

Copy the full `:root` and `.dark` blocks from the Color System section above into your `globals.css` inside `@layer base { }`.

### 4. Utility Function

```typescript
// lib/utils.ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

### 5. Font Setup (Next.js)

```typescript
import { Inter, JetBrains_Mono } from "next/font/google";

const inter = Inter({ subsets: ["latin"], variable: "--font-geist-sans", display: "swap" });
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });

// Apply on <html>:
<html className={`dark ${inter.variable} ${jetbrainsMono.variable}`}>
```

### 6. Dynamic Accent (Optional)

```typescript
// In layout.tsx — inject accent color from env
const accentColor = process.env.NEXT_PUBLIC_ACCENT_COLOR || "oklch(0.79 0.18 70.67)";
// Set via <style> tag or CSS variable override
```
