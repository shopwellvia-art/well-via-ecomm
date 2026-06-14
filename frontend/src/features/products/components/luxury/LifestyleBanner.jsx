import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';

/**
 * Editorial product banner — a large image with a scrim overlay and CTA.
 * Flat style: no cinematic Framer Motion scale, no glassmorphism. The image
 * itself provides visual richness. Renders nothing when no image is available.
 * All logic (finding the hero image URL) unchanged.
 *
 * Text content is derived solely from real product data (name + first sentence
 * of description) — never fabricated. This also fixes heading order: the <h2>
 * reflects the actual product name rather than hardcoded marketing copy.
 */
export function LifestyleBanner({ product }) {
  const hero =
    product.image_url ||
    [...(product.images || [])].sort(
      (a, b) => Number(b.is_primary) - Number(a.is_primary) || a.position - b.position,
    )[0]?.url;

  if (!hero) return null;

  // Derive the first sentence of the description for the sub-headline.
  // Falls back to undefined (renders nothing) when description is absent.
  const firstSentence = product.description
    ? product.description.split(/(?<=[.!?])\s+/)[0]
    : undefined;

  return (
    <section className="mt-10">
      <div className="relative isolate overflow-hidden rounded-sm">
        <img
          src={hero}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 -z-10 size-full object-cover"
        />
        {/* Scrim */}
        <div
          className="absolute inset-0 -z-10 bg-gradient-to-r from-black/75 via-black/50 to-transparent"
          aria-hidden="true"
        />
        <div className="flex min-h-[220px] flex-col justify-center gap-3 p-8 sm:min-h-[280px] sm:p-12">
          <h2 className="max-w-md text-2xl font-semibold text-white sm:text-3xl">
            {product.name}
          </h2>
          {firstSentence && (
            <p className="max-w-sm text-sm font-medium text-white/80">{firstSentence}</p>
          )}
          <div className="mt-2">
            <Link
              to="/products"
              className="inline-flex h-10 items-center gap-2 rounded-sm bg-white px-5 text-sm font-semibold text-ink-primary shadow-sm transition-[filter] hover:brightness-95 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
            >
              Explore collection
              <ArrowRight className="size-4" aria-hidden="true" />
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
