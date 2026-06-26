# WELLVIA — Premium Wellness E-commerce (React + Tailwind)

A production-ready, component-based storefront UI for the **WELLVIA** wellness brand.
Luxury, calm, minimal — warm textured paper, dark forest-green CTAs, editorial serif
headings, gold accents. Built with **React 18 + Vite + Tailwind CSS + React Router**.

---

## Quick start

```bash
cd wellvia-react
npm install
npm run dev      # http://localhost:5173
```

Build for production:

```bash
npm run build && npm run preview
```

---

## What's included

### Reusable components (`src/components/`)
| Component | Purpose |
|---|---|
| `Header` | Centered logo, nav (Shop All / Rituals / Our Story / Account), search, wishlist, account, cart. Responsive (desktop + mobile bars). |
| `Footer` | Centered logo, link columns, leaf-accented newsletter, mini-cart card, trust badges, botanical corners. |
| `HeroSection` | Editorial headline, dual CTAs, rating chip, trust badges. |
| `ProductCard` / `ProductGrid` | Product card with image, rating, price, "Build This Ritual" CTA; responsive 2/3/4-up grid. |
| `ProductDetail` | Full PDP: benefits, ingredients, science section, review. |
| `CartDrawer` | Slide-in cart with qty steppers, subtotal, secure-checkout note. |
| `CheckoutForm` | 3-step form: Shipping → Payment (Card / UPI / Net Banking) → Review. |
| `StepIndicator` | Checkout progress row. |
| `TrustBadges` | Clinically Reviewed · Vegan & Clean · No Added Sugar · FSSAI Compliant. |
| `AccountLayout` | Sidebar shell shared by all `/account/*` pages. |
| `ImageSlot` | Drag-and-drop image placeholder (persists to localStorage). Swap for real `<img>`. |
| `Logo`, `Icons` | Original leaf wordmark + dependency-free icon set. |

### Pages (`src/pages/`)
Store: `HomePage`, `ProductListPage`, `ProductDetailPage`, `CartPage`, `CheckoutPage`
Auth: `LoginPage`, `ForgotPasswordPage`, `AuthCallbackPage`
Account: `AccountSecurityPage`, `AddressesPage`, `OrdersPage`, `OrderDetailPage`, `WishlistPage`, `RewardsPage`
Payments: `PaymentMockPage`, `PaymentReturnPage`
Company: `AboutPage`, `CareersPage`, `CorporatePage`, `ContactPage`, `PressPage`, `StorePage`
Misc: `NotFoundPage`

### Routes (`src/App.jsx`)
```
/                         Home
/shop                     Product list (filter + sort)
/product/:slug            Product detail
/cart                     Cart page
/checkout                 Checkout (3 steps)
/login  /forgot-password  /auth/callback
/account                  Account & Security
/account/addresses
/account/orders  /account/orders/:id
/account/wishlist  /account/rewards
/payment  /payment/return
/about /careers /corporate /contact /press /stores
*                         404
```

---

## Design tokens (`tailwind.config.js`)

| Token | Hex | Use |
|---|---|---|
| `page` | `#DED7C9` | Outer canvas |
| `bg` | `#ECE8DE` | Paper background |
| `card` | `#FFFDF8` | Cards / surfaces |
| `green` | `#183A2E` | Primary buttons |
| `greenh` | `#10291F` | Button hover |
| `ink` | `#1E1E1A` | Main text |
| `muted` | `#6F6A60` | Secondary text |
| `line` | `#D8D0C4` | Borders |
| `gold` | `#B49A63` | Accent |

Fonts (loaded in `index.html`): **Cinzel** (wordmark), **Cormorant Garamond** (headings),
**Jost** (body). The warm paper texture is `public/paper-texture.png`, applied via the
`.paper` class in `index.css`.

---

## Cart state

A small `CartContext` (`src/context/CartContext.jsx`) holds the cart and powers the
header badge, drawer, cart page, and checkout. Replace the in-memory state with your API
when you wire up a backend.

## Wiring in real data & images

- **Products** live in `src/data/products.js` — replace with your catalogue / API.
- **Images** use `<ImageSlot>` placeholders. Swap each for a real `<img src={...} />`
  (or your CMS component) once you have photography. The `id` keys persist drag-dropped
  images to localStorage for quick mockups.
- **Auth / payments** are mocked screens (`AuthCallbackPage`, `PaymentMockPage`,
  `PaymentReturnPage`) — drop your provider's SDK calls into the marked `TODO`s.

---

© 2026 Wellvia. Design system is original; not affiliated with any referenced brand.
