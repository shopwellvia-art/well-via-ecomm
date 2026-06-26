import { Sparkles, Zap, ShieldCheck, Package, Award, Leaf } from 'lucide-react';

const ICONS = [Sparkles, Zap, ShieldCheck, Package, Award, Leaf];

/**
 * "Highlights" — flat icon feature cards, Flipkart/Amazon style.
 * Derives bullets from product's own description; never fabricates data.
 * Hidden when fewer than 2 usable highlights exist.
 */
export function KeyFeatures({ product }) {
  const highlights = extractHighlights(product.description);
  if (highlights.length < 2) return null;

  return (
    <section aria-labelledby="highlights-heading" className="mt-10">
      <h2
        id="highlights-heading"
        className="mb-4 text-base font-semibold text-wink"
      >
        Product highlights
      </h2>

      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {highlights.map((text, i) => {
          const Icon = ICONS[i % ICONS.length];
          return (
            <li
              key={text.slice(0, 40)}
              className="flex items-start gap-3 rounded-sm border border-wline bg-wcard p-4 transition-shadow hover:shadow-md"
            >
              <span
                className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-sm bg-wgreen/10 text-wgreen"
                aria-hidden="true"
              >
                <Icon className="size-4" />
              </span>
              <p className="text-xs leading-relaxed text-wmuted">{text}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function extractHighlights(description) {
  const raw = (description || '').trim();
  if (!raw) return [];
  return [
    ...new Set(
      raw
        .split(/(?<=[.!?])\s+|\n+/g)
        .map((s) => s.trim().replace(/\s+/g, ' '))
        .filter((s) => s.length >= 12 && s.length <= 140),
    ),
  ].slice(0, 4);
}
