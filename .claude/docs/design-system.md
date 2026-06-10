# Design System — 2026 Ecommerce UI
*(Last updated: 2026-06-10 — Premium upgrade pass)*

---

## Vision

Build a premium modern ecommerce experience inspired by:
- Apple, Stripe, Linear, Framer, Shopify Horizon, Vercel

The UI should feel: immersive, premium, lightweight, fast, modern, cinematic.

NOT: cluttered, outdated, dashboard-heavy, template-looking.

---

## §1  UI Principles

### 1. Minimalism
Large spacing · clear typography · fewer borders · soft shadows · layered depth

### 2. Motion Design
Use: subtle parallax, hover animations, smooth page transitions, skeleton loaders, micro-interactions.
Avoid: heavy animations, laggy effects, excessive motion.

### 3. 3D / Depth
Glassmorphism · depth layering · gradient lighting · floating cards.
Avoid WebGL-heavy or performance-heavy rendering.

### 4. Mobile First
Design mobile-first always.

### 5. Performance
Lazy loading · optimized images · code splitting · no large bundles.

---

## §2  Color Tokens  (`tailwind.config.js`)

All surface and ink tokens are CSS-variable-backed so dark/light parity is automatic.

| Token | Usage |
|---|---|
| `bg-bg-base` | Page background |
| `bg-bg-elevated` | Cards, panels |
| `bg-bg-sunken` | Input backgrounds, code blocks |
| `text-ink-primary` | Body text, headings |
| `text-ink-secondary` | Labels, subtitles |
| `text-ink-tertiary` | Placeholders, disabled text, hints |
| `text-ink-inverse` | Text on accent fill |
| `border-line-subtle` | Default border |
| `border-line-strong` | Active/hover border |
| `bg-fill` | Hover backgrounds (ghost buttons) |
| `bg-fill-strong` | Neutral badge backgrounds |
| `glass` | Glass surface background |

### Brand / status

| Token | Value | Soft tint |
|---|---|---|
| `accent` | `#6366F1` | `accent/12` |
| `accent-hover` | `#7C7FF5` | — |
| `accent-press` | `#5457D6` | — |
| `accent-mid` | `#818CF8` | gradient midpoint |
| `success` | `#22C55E` | `success/12` |
| `warning` | `#F59E0B` | `warning/12` |
| `danger` | `#EF4444` | `danger/12` |
| `info` | `#38BDF8` | `info/12` |

### Shadows

| Token | Usage |
|---|---|
| `shadow-sm` | Subtle lift for small elements |
| `shadow-md` | Default card elevation |
| `shadow-lg` | Overlays, dropdowns |
| `shadow-lift` | Hover-lift elevation (stronger than md) |
| `shadow-glow` | Accent glow ring |
| `shadow-glow-sm` | Softer accent ring |
| `shadow-glow-success` | Success feedback glow |
| `shadow-glow-danger` | Error/destructive glow |

---

## §3  Typography (`tailwind.config.js` + Inter font)

| Class | Size | Use |
|---|---|---|
| `text-display` | clamp(2.5–3.5rem) | Hero headlines |
| `text-h1` | clamp(2–2.5rem) | Page titles |
| `text-h2` | 1.875rem | Section headings |
| `text-h3` | 1.375rem | Card headings, modals |
| `text-body` | 1rem | Body copy |
| `text-sm` | 0.875rem | Labels, helper text |
| `text-xs` | 0.75rem | Captions, badges |

---

## §4  Motion tokens  (`src/lib/motion.js`)

Import named exports. All are Framer Motion variant objects or `whileHover`/`whileTap` values.
Framer Motion respects `prefers-reduced-motion` via `useReducedMotion()`.

### Easing curves (`ease.*`)
| Key | Curve | Use |
|---|---|---|
| `standard` | `[0.22,1,0.36,1]` | General transitions |
| `entrance` | `[0.16,1,0.3,1]` | Elements entering |
| `exit` | `[0.4,0,1,1]` | Elements leaving |
| `spring` | `[0.34,1.56,0.64,1]` | Scale / lift interactions |

### Duration scale (`duration.*`)
`instant` 0.12s · `fast` 0.2s · `base` 0.32s · `slow` 0.5s

### Variant presets
| Export | Kind | Use |
|---|---|---|
| `fadeUp` | variant | Default reveal for sections and list items |
| `fadeIn` | variant | Reveal without vertical motion |
| `slideInRight` | variant | Drawers, slide-over panels |
| `scaleIn` | variant | Modals, popovers, dropdowns |
| `pageEnter` | initial/animate | Page wrapper entrance |
| `staggerContainer(n)` | container variant | Grid / section stagger |
| `listStagger(n)` | container variant | Tight list / table stagger |
| `heroContainer` | container variant | Hero heading + CTA reveal |

### Interaction presets (use on `whileHover` / `whileTap`)
| Export | Use |
|---|---|
| `hoverLift` | Card hover: y-4 + scale 1.01 |
| `tapPress` | Card tap: scale 0.98 + y-1 |
| `buttonPress` | Icon button tap: scale 0.96 |
| `attentionPulse` | One-shot pulse for count change / success |
| `iconSpin` | Spinner / refresh icon |

---

## §5  CSS component classes  (`src/styles/global.css`)

### Surfaces
| Class | Use |
|---|---|
| `.glass` | Translucent blurred surface (navbar, flyout, hero cards) |
| `.gradient-border` | 1px gradient border via pseudo-element — add to any rounded container |
| `.accent-halo` | Radial glow behind element on hover/focus — hero CTAs, featured tiles |

### Elevation / interaction
| Class | Use |
|---|---|
| `.hover-lift` | CSS-only lift: `translateY(-4px) scale(1.01)` on hover. Use when Framer Motion is not available. |
| `.card-interactive` | `.hover-lift` + border brighten + cursor-pointer. Apply on clickable `<Card>` wrappers. |

### Text / layout
| Class | Use |
|---|---|
| `.text-gradient` | Accent gradient applied to text (`-webkit-text-fill-color: transparent`). Hero callouts only. |
| `.surface-gradient` | Radial accent bloom over bg-base. Hero sections and feature panels. |
| `.focus-ring` | `ring-2 ring-accent/70 ring-offset-2` — all interactive elements. |
| `.text-balance` | `text-wrap: balance` — headings. |
| `.nums` | `font-variant-numeric: tabular-nums` — prices, counts. |
| `.grid-dots` | Subtle dot-grid texture — hero sections only. |
| `.skeleton-shimmer` | Apply shimmer to non-Skeleton arbitrary elements. |

### CSS custom properties (new in this pass)
| Property | Default (dark) | Use |
|---|---|---|
| `--shadow-lift` | Stronger drop shadow | Used by `.hover-lift`, `shadow-lift` |
| `--accent-glow` | `0 0 0 3px rgba(99,102,241,0.18)` | Used by Input/Select/Textarea focus glow |
| `--gradient-accent-from/via/to` | Indigo → violet ramp | Used by `.text-gradient`, `.gradient-border` |

---

## §6  Component API changes (additive only)

### `<Button>` (`src/components/ui/Button.jsx`)
New `variant` values:
- `outline` — accent-bordered, lower emphasis than `secondary`

New `iconOnly` prop (boolean):
- Squares the button (`w-9`/`w-11`/`w-[52px]`) for icon-only buttons

Loading state polish:
- Children are `opacity-0` (not removed) while `loading=true`, preserving button width

### `<Card>` (`src/components/ui/Card.jsx`)
New props:
- `interactive` — applies `.card-interactive` (hover lift + border brighten)
- `flat` — removes `shadow-md` (for nested cards)

New export: `<CardHeader title action>` — consistent bordered header row

### `<Input>` / `<Select>` / `<Textarea>`
- `required` prop — adds visible `*` to label, sets `aria-required`
- `suffix` prop on `<Input>` — appended icon slot (e.g. eye icon, clear button)
- `maxRows` prop on `<Textarea>` — CSS max-height clamp
- Error `<p>` now gets `role="alert"` for screen-reader live announcement
- Focus style upgraded: accent border + `--accent-glow` box-shadow (no more ring-2 which bled outside rounded corners)

### `<Badge>` (`src/components/ui/Badge.jsx`)
New `tone` values: `info`
New props:
- `outline` — ring-1 border-only variant (no fill)
- `dot` — prepends a colored dot indicator
- `size` — `sm` (default) | `md`

`badgeVariants` is now exported for use with `cn()`.

### `<Skeleton>` (`src/components/ui/Skeleton.jsx`)
New `variant` prop: `'default'` | `'text'` | `'circle'`
New `lines` prop: stacks N text-line skeletons with natural short-last-line treatment

### `<EmptyState>` (`src/components/feedback/EmptyState.jsx`)
New props:
- `iconTone` — `neutral` | `accent` | `success` | `warning` | `danger`
- `size` — `default` | `sm` (compact, less padding, smaller icon)
- `bordered` — boolean (default `true`); set `false` for inline/frameless states

### `<PageFallback>` (`src/components/feedback/PageFallback.jsx`)
New `layout` prop: `'grid'` (default, storefront) | `'admin'` (KPI + table skeleton)

---

## §7  Keyframes / animations  (`tailwind.config.js`)

| Class | Duration | Use |
|---|---|---|
| `animate-shimmer` | 1.6s ease-in-out ∞ | Skeleton shimmer |
| `animate-marquee` | 60s linear ∞ | Scrolling brand strip |
| `animate-fadeUp` | 0.32s entrance | CSS-only reveal (no Framer) |
| `animate-scaleIn` | 0.2s spring | CSS-only modal/popover open |
| `animate-pulseRing` | 1.4s ∞ | Live / attention ring indicator |

---

## §8  Conventions for page teams

1. **Gradients** — use `.text-gradient` on hero headings and callout spans only. Never on body text (accessibility).
2. **Lift** — prefer `.card-interactive` on `<Card interactive>` for product cards. Use `whileHover={hoverLift}` (JS preset) only when you need to coordinate with other motion values on the same element.
3. **Stagger** — use `listStagger` for rows/tables, `staggerContainer` for section grids, `heroContainer` for hero copy blocks.
4. **Forms** — always pass `required` prop (not just HTML `required` attribute) so the `*` label indicator renders consistently.
5. **Status colors** — use `tone` on `<Badge>`, never raw `bg-success/15 text-success` inline. Consistent opacity is baked in.
6. **Info tone** — KPICard `tone="info"` now resolves through the token. Use `text-info` / `bg-info/12` for inline info states.
7. **Admin skeletons** — use `<PageFallback layout="admin" />` in admin Suspense boundaries.
8. **Reduced motion** — `.hover-lift` and `.card-interactive` transforms are disabled automatically under `prefers-reduced-motion`. Framer Motion presets similarly collapse when `useReducedMotion()` returns true.
