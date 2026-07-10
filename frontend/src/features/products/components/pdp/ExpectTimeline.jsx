import WImage from '@/components/storefront/WImage.jsx';
import { Reveal } from '../luxury/luxe.jsx';

/**
 * "What to expect" — vertical timeline of `product.usage_steps`
 * (JSON array of { label, text }): purple dots joined by a connecting line,
 * bold label + supporting text per step, product image beside on desktop.
 *
 * Renders null when the product has no usage steps.
 */

// Mockup deep-purple timeline accent — no Tailwind token exists for it.
const PDP_PURPLE_DEEP = '#4a2b5c';

/** Coerce a JSON column that may arrive as an array or a JSON string. */
function toArray(value) {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }
  return [];
}

export function ExpectTimeline({ steps, product }) {
  const items = toArray(steps).filter((s) => s && (s.label || s.text));
  if (items.length === 0) return null;

  const imageUrl =
    product?.images?.[2]?.url || product?.images?.[0]?.url || product?.image_url || null;

  return (
    <Reveal as="section" className="mt-10 lg:mt-14">
      <div
        aria-labelledby="pdp-expect-heading"
        className="rounded-xl3 border border-wline bg-wcard px-6 py-8 lg:px-12 lg:py-12"
      >
        <h2
          id="pdp-expect-heading"
          className="text-center font-wserif text-[clamp(24px,3vw,34px)] font-medium text-wink"
        >
          What to expect
        </h2>

        <div
          className={`mt-8 grid items-center gap-8 lg:gap-12 ${
            imageUrl ? 'lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]' : ''
          }`}
        >
          {/* Timeline */}
          <ol className="flex flex-col">
            {items.map((step, i) => {
              const isFirst = i === 0;
              const isLast = i === items.length - 1;
              return (
                <li key={`${step.label || step.text}-${i}`} className="relative flex gap-5 pb-8 last:pb-0">
                  {/* Connecting line down to the next dot */}
                  {!isLast && (
                    <span
                      aria-hidden="true"
                      className="absolute left-2 top-5 w-0.5"
                      style={{ backgroundColor: PDP_PURPLE_DEEP, bottom: '-6px' }}
                    />
                  )}
                  {/* Dot — first is an open ring, the rest are filled */}
                  <span
                    aria-hidden="true"
                    className="relative z-[1] mt-0.5 size-[18px] shrink-0 rounded-full"
                    style={
                      isFirst
                        ? { border: `4px solid ${PDP_PURPLE_DEEP}`, backgroundColor: '#FFFDF8' }
                        : { backgroundColor: PDP_PURPLE_DEEP }
                    }
                  />
                  <div className="min-w-0">
                    {step.label && (
                      <h3 className="text-[16px] font-semibold leading-snug text-wink">
                        {step.label}
                      </h3>
                    )}
                    {step.text && (
                      <p className="mt-1 text-[13.5px] leading-[1.6] text-wmuted">{step.text}</p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>

          {/* Product image */}
          {imageUrl && (
            <WImage
              src={imageUrl}
              alt={`${product?.name || 'Product'} in use`}
              shape="rounded"
              className="aspect-[4/3] w-full"
            />
          )}
        </div>
      </div>
    </Reveal>
  );
}
