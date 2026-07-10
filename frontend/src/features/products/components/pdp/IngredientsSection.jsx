import { ShoppingBasket, Leaf, Sparkles, Droplets, FlaskConical } from 'lucide-react';
import WImage from '@/components/storefront/WImage.jsx';
import { Reveal } from '../luxury/luxe.jsx';

/**
 * "Clean & Effective Ingredients" — ingredient rows parsed from the
 * `product.ingredients` text column (one item per line, "Name — note" or
 * plain lines), with a product image beside the list on desktop.
 *
 * Renders null when the product has no ingredients text.
 */

// Mockup lavender accent — no Tailwind token exists for the PDP purple.
const PDP_PURPLE = '#7c4a8c';
const PDP_LAVENDER = '#ede4f3';

const ROW_ICONS = [ShoppingBasket, Leaf, Sparkles, Droplets, FlaskConical];

/**
 * Parse the ingredients text: one item per line; each line is either
 * "Name — note" (em/en dash or spaced hyphen) or a plain name.
 */
function parseIngredients(text) {
  if (typeof text !== 'string') return [];
  return text
    .split(/\r?\n/)
    .map((line) => line.trim().replace(/^[-•*]\s+/, ''))
    .filter(Boolean)
    .map((line) => {
      // Em/en dash first, then a hyphen surrounded by spaces (so hyphenated
      // names like "L-Theanine" are never split apart).
      const match = line.match(/^(.+?)\s*[—–]\s*(.+)$/) || line.match(/^(.+?)\s+-\s+(.+)$/);
      return match
        ? { name: match[1].trim(), note: match[2].trim() }
        : { name: line, note: '' };
    });
}

export function IngredientsSection({ product }) {
  const items = parseIngredients(product?.ingredients);
  if (items.length === 0) return null;

  const imageUrl =
    product?.images?.[1]?.url || product?.images?.[0]?.url || product?.image_url || null;

  return (
    <Reveal as="section" className="mt-10 lg:mt-14">
      <div
        aria-labelledby="pdp-ingredients-heading"
        className="rounded-xl3 border border-wline bg-wcard px-6 py-8 lg:px-12 lg:py-12"
      >
        <h2
          id="pdp-ingredients-heading"
          className="text-center font-wserif text-[clamp(24px,3vw,34px)] font-medium text-wink"
        >
          Clean &amp; Effective Ingredients
        </h2>

        <div className={`mt-8 grid items-center gap-8 lg:gap-12 ${imageUrl ? 'lg:grid-cols-2' : ''}`}>
          {/* Ingredient rows */}
          <ul className="flex flex-col gap-6">
            {items.map((item, i) => {
              const Icon = ROW_ICONS[i % ROW_ICONS.length];
              return (
                <li key={`${item.name}-${i}`} className="flex items-start gap-4">
                  <span
                    className="grid size-12 shrink-0 place-items-center rounded-full"
                    style={{ backgroundColor: PDP_LAVENDER }}
                    aria-hidden="true"
                  >
                    <Icon className="size-5" strokeWidth={1.6} style={{ color: PDP_PURPLE }} />
                  </span>
                  <div className="min-w-0 pt-0.5">
                    <p className="text-[15px] font-semibold leading-snug text-wink">{item.name}</p>
                    {item.note && (
                      <p className="mt-1 text-[13px] leading-[1.6] text-wmuted">{item.note}</p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>

          {/* Product image */}
          {imageUrl && (
            <WImage
              src={imageUrl}
              alt={`${product?.name || 'Product'} ingredients`}
              shape="rounded"
              className="aspect-[4/3] w-full"
            />
          )}
        </div>
      </div>
    </Reveal>
  );
}
