# Handoff: Flipkart-style Storefront Upgrade (ShopWell)

## Overview
This package upgrades the ShopWell storefront to a polished, Flipkart-style marketplace
experience: a two-tier header with category mega-nav, an auto-rotating hero carousel,
"Deals of the Day" with a live countdown, category-showcase grids, bestseller rails,
promo banners, a richer Product Listing (PLP) and Product Detail (PDP), Cart, and Login —
all fully **mobile responsive** and tuned for a soft, smooth, production feel.

## About the Design Files
The files in this bundle (`ShopWell *.html` + `assets/`) are **design references created in
plain HTML + Tailwind (Play CDN) + vanilla JS**. They are NOT production code to copy
verbatim. The task is to **recreate these designs inside the existing React app** at
`frontend/` (React 18 + Vite + React Router + Tailwind + Framer Motion + lucide-react +
TanStack Query), reusing its established components, hooks, and data layer.

The HTML mock was built deliberately on **the same design tokens** already defined in the
app (`frontend/tailwind.config.js` and `frontend/src/styles/global.css`), so most of the
visual language already exists — this is mostly a layout/structure/markup upgrade, not a
re-theme. Where the mock introduced new token values (softer radii + shadows), they are
listed under **Design Tokens → Deltas** below; apply those to the real config.

## Fidelity
**High-fidelity (hifi).** Final colors, typography, spacing, radii, shadows, and
interactions are intended as shown. Recreate pixel-faithfully using the codebase's existing
component primitives (`src/components/ui/*`) and patterns. Product imagery in the mock uses
intentional placeholder tiles (a tinted radial + a lucide-style product glyph) — in the real
app, wire the existing product `image_url` and keep the placeholder only as the `onError`/no-image
fallback.

---

## Target file map (mock → real React file)
| Mock (this bundle) | Real file to create/edit |
|---|---|
| Header + search + cart badge (in `assets/chrome.js → header()`) | `src/components/layout/Navbar.jsx` |
| Category mega-nav strip + dropdowns + **mobile category strip** | new `src/components/layout/CategoryNav.jsx`, render in `src/components/layout/Layout.jsx` under the navbar |
| Mobile hamburger drawer | extend the existing drawer in `src/components/layout/Navbar.jsx` (it already has a focus-trapped mobile drawer) |
| Hero carousel (`ShopWell Home.html`) | `src/components/marketing/Hero.jsx` (already a carousel — restyle slides + stabilize height) |
| Perk strip | `src/components/marketing/` (exists in `Hero.jsx` perks / add a small `PerkStrip.jsx`) |
| Deals of the Day + countdown | `src/components/marketing/DealsBanner.jsx` + `src/components/marketing/SaleCountdown.jsx` |
| Category showcase grids | new `src/components/marketing/CategoryShowcase.jsx` |
| Bestsellers rail | `src/components/marketing/BestsellersSection.jsx` |
| Product card (grid/deal/mini variants) | new `src/features/products/components/ProductCard.jsx` (+ `DealCard`, `MiniCell`) |
| Recommended grid | `src/pages/HomePage.jsx` |
| PLP (filters drawer, sort, pagination) | `src/pages/ProductListPage.jsx` |
| PDP (gallery, offers, specs, sticky mobile CTA) | `src/pages/ProductDetailPage.jsx` |
| Cart (live price details) | `src/pages/CartPage.jsx` |
| Login (two-panel) | `src/pages/LoginPage.jsx` |
| Footer | `src/components/layout/Footer.jsx` |

---

## Screens / Views

### 1. Header (sticky, two-tier)
- **Tier 1 — blue bar** (`bg-accent` `#2874F0`), height `56px` (`h-14`), `max-w-content` centered, `gap` 10–20px:
  - **Mobile only (`<md`)**: hamburger button (lucide `Menu`, 22px, white) → opens drawer.
  - **Logo**: "ShopWell" bold italic, `text-lg` mobile / `text-xl` desktop, white; sub-line "Explore **Plus** ✦" italic `11px` white/85 with a yellow (`#FFE11B`) star — sub-line hidden `<sm`.
  - **Search**: flex-1, `max-w-[560px]` at `sm+`, white field `h-9`, `rounded-sm`, placeholder "Search for products, brands and more", trailing accent search-icon button. Submits → `/products?q=`.
  - **Login**: white pill button `h-8` `px-8`, `text-accent`, `font-semibold`; hidden `<sm` (lives in drawer on mobile).
  - **Become a Seller** / **More ▾**: text links, desktop-only (`lg`).
  - **Cart**: lucide `ShoppingCart` 21px white + "Cart" label (hidden `<sm`) + count badge (orange `bg-cta`, `9–10px` bold white, top-right).
- **Tier 2 — category nav** (white `bg-bg-elevated`, `border-b`, `shadow-sm`):
  - **Desktop (`md+`)**: horizontal row, `h-12`, 10 categories. Each = lucide icon (18px) + label + chevron; on hover a dropdown panel (`min-w-[200px]`, white, `rounded-sm`, `shadow-lg`) fades/rises in with sub-links. Active category gets `border-b-2 border-accent text-accent`.
  - **Mobile (`<md`)**: horizontally-scrollable strip of circular icon chips (`size-12` circle, `bg-accent/8`, accent icon) + 11px label under each.
- **Categories**: Mobiles, Electronics, Audio, Wearables, Home, Appliances, Fashion, Beauty, Gaming, Gift Cards (see `assets/chrome.js → CATS` for sub-items).

### 2. Mobile drawer
- Left slide-in, `w-[84%] max-w-xs`, white. Blue `56px` header with "ShopWell" + close (X) button.
- Body: "Login / Sign up" row (accent), primary nav (Home, Shop, Wishlist, Orders, Cart), then "Shop by category" list (icon + label per category).
- Backdrop `bg-black/45`, click-to-close; locks body scroll while open. (The existing Navbar drawer already implements focus-trap + Esc — keep that.)

### 3. Home (`HomePage.jsx`)
Order of sections, each `mt-3`, `max-w-content`:
1. **Hero carousel** — `rounded-sm`, `shadow-sm`, `min-h-[224px]` mobile / `min-h-[300px]` desktop (keeps slides from jumping). 3 slides, each a 2-column (copy left, product glyph right hidden `<sm`) with a diagonal gradient bg. Auto-advance 5s, pause on hover; arrows (left/right circular white) + dot indicators (active dot `w-6 bg-accent`). Slide CTAs use `bg-white` (on blue), `bg-cta` (on dark), etc.
2. **Perk strip** — 2-col mobile / 4-col desktop; icon (accent) + title + subtitle. Items: Free delivery / Secure checkout / Easy returns / Best prices.
3. **Deals of the Day** — orange gradient header bar (`from-cta to-#ff8a4d`) with flame title, **live countdown** (HH:MM:SS in `bg-white/20` chips, tabular-nums), "View all" pill. Body = horizontal **rail** of compact deal cards.
4. **Category showcase** — 1-col mobile / 2-col desktop grid of 4 panels. Each panel: header (title + subtitle + "View all ›") then a 2-col mobile / 4-col grid of mini product cells.
5. **Bestsellers** — header + horizontal rail of full product cards (200px wide).
6. **Promo banners** — 2-up gradient cards (Gaming Week / Beauty Bestsellers) with glyph + CTA.
7. **Recommended for you** — product grid: 2-col mobile / 3-col `sm` / 5-col `lg`.
8. **Newsletter** — white card, mail icon + copy + email field + Subscribe (accent) button.

### 4. Product Listing (`ProductListPage.jsx`)
- Breadcrumb row.
- 2-column at `lg`: **left filter sidebar** (`w-64`, sticky `top-[120px]`), **right results**.
- **Filter sidebar card**: "Filters" + Clear all; sections — Category (button list; active = `bg-accent/10 text-accent`), Price (checkboxes), Customer rating, Offers.
- **Mobile (`<lg`)**: sidebar becomes a **left slide-in drawer** over a `bg-black/45` backdrop, opened by a "Filters" button in the sort bar, with a sticky "Show results" button at the bottom and a close (X). Backdrop + button both close it.
- **Sort bar**: results count (hidden `<sm`) + horizontally-scrollable sort chips (Relevance / Price ↑ / Price ↓ / Newest); active chip = `bg-accent text-white`.
- **Grid**: 2-col mobile / 3-col `sm` / 4-col `xl`.
- **Pagination**: prev / numbered / next, active page `bg-accent text-white`.

### 5. Product Detail (`ProductDetailPage.jsx`)
- Breadcrumb.
- White card, `lg` 2-column `[minmax(0,440px)_1fr]`:
  - **Left (sticky `top-[120px]`)**: thumbnail column (4 chips, active border accent) + main square image (`rounded-lg`, border). Below: **Add to Cart** (amber `bg-cart`) + **Buy Now** (orange `bg-cta`) buttons — `hidden lg:grid` (mobile uses the sticky bottom bar instead).
  - **Right**: brand eyebrow, title, rating pill + count + "In stock", price block (`text-3xl` price + struck MRP + green % off), "Inclusive of all taxes", **Available offers** card (tag icon rows), Delivery + Quantity stepper, a 3-up trust row (`bg-bg-sunken`), and a **Product details** spec table.
- **You may also like**: rail of related product cards.
- **Mobile**: fixed bottom action bar (`lg:hidden`) with Add to Cart + Buy Now, plus a `h-16` spacer so the footer clears it.

### 6. Cart (`CartPage.jsx`)
- 2-column at `lg`: items list (left) + sticky **Price details** summary (right).
- **Item row**: thumb + brand/name/seller + rating pill + price/MRP/%off + quantity stepper + "Save for later" + "Remove".
- **Summary**: Price (n items), Discount (green), Delivery (FREE green), dashed divider, Total (bold). "You save ₹X" line. **Place order** = orange `bg-cta` full-width. Live-recomputes on qty change / removal. Empty state with "Continue shopping ›".

### 7. Login (`LoginPage.jsx`)
- Centered card `max-w-3xl`, `rounded-xl`, `shadow-lg`, 2-column `[40%_1fr]` at `sm`:
  - **Left**: blue gradient panel, "Login" heading + benefits checklist (yellow checks).
  - **Right**: email/mobile + password fields, terms line, **Login** (orange) button, OR divider, "Request OTP" outline button, "Create an account" link.

---

## Interactions & Behavior
- **Hero**: `setInterval` 5000ms auto-advance; `clearInterval` on `mouseenter`, restart on `mouseleave`; manual prev/next + dots restart the timer; translateX track transition `500ms ease-out`. Honor `prefers-reduced-motion`.
- **Rails**: native horizontal scroll (`overflow-x-auto`, hidden scrollbar, `scroll-snap`); desktop arrow buttons call `scrollBy({left: ±clientWidth*0.8, behavior:'smooth'})`; arrows **hidden `<md`** (touch swipe).
- **Add to Cart**: optimistic — bump cart-count badge + show a bottom toast "Added to cart" (auto-dismiss ~2.2s). In the real app, call the existing cart mutation (`src/features/cart/hooks.js`).
- **Wishlist heart**: toggles fill + accent color + toast (wire to `src/features/wishlist`).
- **Quantity steppers**: clamp at min 1.
- **Filters/Sort (PLP)**: category filter + price sort recompute the grid client-side in the mock; in the app, drive the existing `useProducts` query params.
- **Countdown**: ticks each second to a target ~8h out; format `HH:MM:SS`, `font-variant-numeric: tabular-nums`.
- **Toast**: single shared element, `position: fixed; bottom: 28px; center`, dark `#212121`, slide-up + fade.

## Responsive behavior
- Breakpoints are Tailwind defaults: `sm 640`, `md 768`, `lg 1024`, `xl 1280`. Content max width `1248px` (`max-w-content`).
- `<md`: hamburger + drawer, mobile category strip, no mega-nav, no rail arrows.
- `<sm`: logo tagline hidden, Login moves to drawer, Cart label hidden, grids drop to 2-col.
- `<lg`: PLP filters become a drawer; PDP collapses to one column with a sticky bottom CTA bar.

## State Management
Map to existing hooks/stores — do not introduce new state libraries:
- Auth: `src/features/auth/store.js`
- Cart: `src/features/cart/hooks.js` (count badge in Navbar already reads this)
- Wishlist: `src/features/wishlist/hooks.js`
- Products / bestsellers / categories: `src/features/products/hooks.js`, `categories/hooks.js`
- Hero slides + countdown target: `src/features/hero-slides/hooks.js`
- Local UI state (drawer open, active sort, qty, carousel index): component `useState`.

## Design Tokens
All already exist in `frontend/tailwind.config.js` + `src/styles/global.css`. Brand:
- `accent` (Flipkart blue) `#2874F0`, hover `#1F63D6`, press `#1A55BA`, mid `#5C97F5`
- `cta` (Buy Now orange) `#FB641E`, hover `#E85610`
- `cart` (Add to Cart amber) `#FF9F00`, hover `#F59300`
- `rating` / `success` green `#388E3C`
- Yellow accent on blue chrome: `#FFE11B`
- Footer bg: `#172337`
- Surfaces (light): page `#F1F3F6`, cards/bars `#FFFFFF`, wells `#F7F8FA`
- Ink: primary `#212121`, secondary `#787878`, tertiary `#9E9E9E`, inverse white
- Font: **Roboto** (400/500/700/900 + italics)
- Type scale, shadows, `max-w-content 1200px` → mock uses 1248px (optional)

### Deltas introduced by this upgrade (apply to the real config)
The mock softened the look slightly for a more premium feel — port these into
`tailwind.config.js` / `global.css` if you want the exact mock appearance:
- **Border radius** (softer than the current near-square scale):
  `xs 3px, sm 7px, md 9px, lg 12px, xl 16px` (current is `2/4/6/8/12`). Product cards use `rounded-lg` (12px).
- **Shadows** (softer, more diffuse): 
  - `sm: 0 1px 2px rgba(17,24,39,.05), 0 1px 3px rgba(17,24,39,.05)`
  - `md: 0 2px 8px rgba(17,24,39,.06), 0 8px 24px rgba(17,24,39,.05)`
  - `lg: 0 12px 40px rgba(17,24,39,.13), 0 4px 12px rgba(17,24,39,.06)`
  - `lift (hover): 0 14px 34px rgba(17,24,39,.13), 0 3px 10px rgba(17,24,39,.06)`
- **Card hover-lift**: `translateY(-4px)` + `shadow-lift`, transition `.28s cubic-bezier(.22,1,.36,1)`; product glyph scales `1.06` on card hover.
- **Focus ring**: `box-shadow: 0 0 0 3px rgba(40,116,240,.28)`.
- These are optional polish — if you prefer to keep the existing square Flipkart radii, only adopt the layout/markup and skip the radius/shadow deltas.

## Assets
- **Icons**: lucide-react (already a dependency) — `Menu, X, Search, ShoppingCart, Heart, User, Store, Gift, ChevronDown, ArrowRight, Flame, Zap/Bolt, Shield, RotateCcw, TrendingUp`, and product-category glyphs (Headphones, Watch, Laptop, Smartphone, Speaker, Lamp, Camera, Monitor, Keyboard, Mouse, Shirt, Footprints, ShoppingBag, Coffee, Gamepad2, Sparkles). The mock inlines equivalent SVG paths in `assets/data.js → ICONS` / `chrome.js → icon()` — replace with the lucide components in React.
- **Product images**: use real `product.image_url`; the tinted-radial + glyph tile is the no-image fallback only.
- **No raster assets** are required by this design.

## Files in this bundle
- `ShopWell Home.html` — home page (hero, deals, showcases, rails, grid)
- `ShopWell Products.html` — PLP (filters drawer, sort, pagination)
- `ShopWell Product.html` — PDP (gallery, offers, specs, sticky mobile CTA)
- `ShopWell Cart.html` — cart with live price details
- `ShopWell Login.html` — two-panel login
- `assets/config.js` — Tailwind token extension (mirrors `tailwind.config.js`, incl. soft-radius deltas)
- `assets/theme.css` — base tokens (mirrors `.light` in `global.css`), placeholder/thumb styles, hover-lift, toast, responsive rail rules
- `assets/chrome.js` — header, category mega-nav + mobile strip, drawer, footer
- `assets/data.js` — sample catalogue + card render helpers + interactions (carousel, rails, countdown, toast)

## Suggested prompt for Claude Code
> "Implement the designs in `design_handoff_flipkart_storefront/` inside the existing React
> app under `frontend/`. These HTML files are visual references — recreate them as React
> components using the app's existing stack (React Router, Tailwind, Framer Motion,
> lucide-react, TanStack Query) and the file map in the README. Reuse existing tokens in
> `tailwind.config.js`/`global.css` and apply the radius/shadow deltas listed in the README.
> Wire data to the existing feature hooks (products, cart, wishlist, hero-slides) — do not
> hardcode the sample catalogue. Keep it fully responsive per the README. Start with the
> shared chrome (Navbar + new CategoryNav + drawer), then Home, then PLP, PDP, Cart, Login."
