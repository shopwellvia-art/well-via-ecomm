import { useSearchParams } from 'react-router-dom';
import { Link } from 'react-router-dom';
import {
  Smartphone,
  Laptop,
  Headphones,
  Watch,
  Home,
  Shirt,
  Sparkles,
  Gamepad2,
  Gift,
  Camera,
  Speaker,
  Lightbulb,
  Coffee,
  Footprints,
  Monitor,
  Tag,
  ChevronDown,
} from 'lucide-react';
import { cn } from '@/lib/utils.js';
import { useCategories } from '@/features/categories/hooks.js';

/**
 * Tier-2 category navigation that sits directly under the blue header.
 *
 * - Desktop (md+): a horizontal, scrollable row of category links. The active
 *   category (matched against the current `?category=` / `?category_id=` query)
 *   gets the Flipkart underline + accent treatment. Categories that expose a
 *   `children` array render a hover mega-dropdown of sub-links.
 * - Mobile (<md): a horizontally-scrollable strip of circular icon chips.
 *
 * Data is the real catalogue (`useCategories`); icons are matched from the
 * category name with a sensible generic fallback so no backend change is needed.
 */

// Keyword → lucide icon. First match wins; falls back to a tag glyph.
const ICON_RULES = [
  [/phone|mobile/i, Smartphone],
  [/laptop|computer|electronic/i, Laptop],
  [/monitor|display|screen/i, Monitor],
  [/audio|headphone|earbud|sound/i, Headphones],
  [/speaker/i, Speaker],
  [/watch|wearable|band/i, Watch],
  [/home|furnitur|kitchen|decor/i, Home],
  [/light|lamp/i, Lightbulb],
  [/appliance|coffee|brew/i, Coffee],
  [/fashion|apparel|cloth|jacket|shirt/i, Shirt],
  [/shoe|footwear|sneaker/i, Footprints],
  [/beauty|skincare|grooming|cosmetic/i, Sparkles],
  [/gaming|game|console/i, Gamepad2],
  [/gift|card/i, Gift],
  [/camera|photo/i, Camera],
];

function iconFor(name = '') {
  for (const [re, Icon] of ICON_RULES) {
    if (re.test(name)) return Icon;
  }
  return Tag;
}

function CategoryItem({ category, active }) {
  const Icon = iconFor(category.name);
  const to = `/products?category=${encodeURIComponent(category.slug)}`;
  const children = Array.isArray(category.children) ? category.children : [];

  return (
    <div className="group relative shrink-0">
      <Link
        to={to}
        aria-current={active ? 'page' : undefined}
        className={cn(
          'flex h-12 items-center gap-2 whitespace-nowrap border-b-2 px-1 text-sm font-medium transition-colors focus-visible:focus-ring',
          active
            ? 'border-accent text-accent'
            : 'border-transparent text-ink-primary hover:text-accent',
        )}
      >
        <Icon
          className={cn(
            'size-[18px] transition-colors',
            active ? 'text-accent' : 'text-ink-secondary group-hover:text-accent',
          )}
          aria-hidden="true"
        />
        {category.name}
        {children.length > 0 && (
          <ChevronDown className="size-3 text-ink-tertiary" aria-hidden="true" />
        )}
      </Link>

      {children.length > 0 && (
        <div className="invisible absolute left-0 top-full z-40 min-w-[200px] translate-y-1 rounded-sm border border-line-subtle bg-bg-elevated py-1.5 opacity-0 shadow-lg transition-all duration-150 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100">
          {children.map((sub) => (
            <Link
              key={sub.id ?? sub.slug}
              to={`/products?category=${encodeURIComponent(sub.slug)}`}
              className="block px-4 py-2 text-sm text-ink-secondary transition-colors hover:bg-accent/5 hover:text-accent"
            >
              {sub.name}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function CategoryChip({ category, active }) {
  const Icon = iconFor(category.name);
  return (
    <Link
      to={`/products?category=${encodeURIComponent(category.slug)}`}
      aria-current={active ? 'page' : undefined}
      className="flex w-[58px] shrink-0 flex-col items-center gap-1 text-center focus-visible:focus-ring"
    >
      <span
        className={cn(
          'grid size-12 place-items-center rounded-full transition-colors',
          active ? 'bg-accent text-white' : 'bg-accent/[0.08] text-accent',
        )}
      >
        <Icon className="size-6" aria-hidden="true" />
      </span>
      <span
        className={cn(
          'w-full truncate text-[11px] font-medium leading-tight',
          active ? 'text-accent' : 'text-ink-secondary',
        )}
      >
        {category.name}
      </span>
    </Link>
  );
}

export default function CategoryNav() {
  const { data: categories = [] } = useCategories();
  const [params] = useSearchParams();
  const activeSlug = params.get('category');
  const activeId = params.get('category_id');

  if (!categories.length) return null;

  const isActive = (c) =>
    (activeSlug && c.slug === activeSlug) || (activeId && String(c.id) === activeId);

  return (
    <>
      {/* Desktop mega-nav row */}
      <div className="hidden border-b border-line-subtle bg-bg-elevated shadow-sm md:block">
        <nav
          aria-label="Shop by category"
          className="rail mx-auto flex h-12 max-w-content items-center gap-6 overflow-x-auto px-6"
        >
          {categories.map((c) => (
            <CategoryItem key={c.id} category={c} active={isActive(c)} />
          ))}
        </nav>
      </div>

      {/* Mobile circular chip strip */}
      <div className="border-b border-line-subtle bg-bg-elevated shadow-sm md:hidden">
        <nav
          aria-label="Shop by category"
          className="rail flex gap-4 overflow-x-auto px-3 py-2.5"
        >
          {categories.map((c) => (
            <CategoryChip key={c.id} category={c} active={isActive(c)} />
          ))}
        </nav>
      </div>
    </>
  );
}
