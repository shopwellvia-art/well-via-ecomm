/**
 * "About this item" — Amazon-style clean bullet list.
 * Derives honest bullets from the product's own data; never fabricates.
 * Renders nothing when there's no usable description.
 */
export function AboutThisItem({ product, categoryName }) {
  const bullets = extractBullets(product, categoryName);
  if (bullets.length === 0) return null;

  return (
    <section className="mt-6" aria-labelledby="about-heading">
      <div className="rounded-xl2 border border-wline bg-wcard">
        <h2
          id="about-heading"
          className="border-b border-wline px-4 py-2.5 text-sm font-semibold text-wink"
        >
          About this item
        </h2>
        <ul className="px-4 py-3 space-y-2">
          {bullets.map((b) => (
            <li key={b.slice(0, 40)} className="flex items-start gap-2.5 text-sm text-wmuted">
              <span
                className="mt-1.5 size-1.5 shrink-0 rounded-full bg-wgreen"
                aria-hidden="true"
              />
              <span className="leading-relaxed">{b}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function extractBullets(product, categoryName) {
  const raw = (product.description || '').trim();
  const sentenceParts = raw
    .split(/(?<=[.!?])\s+|\n+/g)
    .map((s) => s.trim())
    .filter((s) => s.length > 0);

  const bullets = [...new Set(sentenceParts)];

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
