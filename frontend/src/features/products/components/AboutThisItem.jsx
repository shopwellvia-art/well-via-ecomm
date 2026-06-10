import { motion } from 'framer-motion';
import { Sparkles } from 'lucide-react';
import { listStagger, fadeUp } from '@/lib/motion.js';

/**
 * "About this item" bullets.
 *
 * Derives an honest list from data we have: the description (split into
 * sentences) plus factual auto-bullets (category, stock, SKU). Skipped
 * if the product has no description.
 */
export function AboutThisItem({ product, categoryName }) {
  const bullets = extractBullets(product, categoryName);
  if (bullets.length === 0) return null;

  return (
    <section className="mt-8" aria-labelledby="about-heading">
      <h2 id="about-heading" className="text-h3 tracking-tight text-ink-primary">
        About this item
      </h2>

      <motion.ul
        className="mt-4 flex flex-col gap-3"
        variants={listStagger(0.05)}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, amount: 0.2 }}
      >
        {bullets.map((b, i) => (
          <motion.li
            key={i}
            variants={fadeUp}
            className="flex items-start gap-3 text-sm text-ink-secondary"
          >
            <span
              className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-sm bg-accent-soft text-accent"
              aria-hidden="true"
            >
              <Sparkles className="size-3" />
            </span>
            <span className="leading-relaxed">{b}</span>
          </motion.li>
        ))}
      </motion.ul>
    </section>
  );
}

function extractBullets(product, categoryName) {
  const raw = (product.description || '').trim();
  // Split on sentence end OR explicit newlines. Filter empties + dedupe.
  const sentenceParts = raw
    .split(/(?<=[.!?])\s+|\n+/g)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

  const bullets = [...new Set(sentenceParts)];

  // Add factual auto-bullets that we know are true from the product record.
  if (categoryName) bullets.push(`Curated under ${categoryName}.`);
  if (product.stock > 0) {
    bullets.push(
      product.stock <= 5
        ? `Limited availability — only ${product.stock} left in stock.`
        : `Ready to ship — currently in stock.`,
    );
  }
  bullets.push(`SKU: ${product.sku}`);

  return bullets;
}
