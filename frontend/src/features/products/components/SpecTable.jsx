import { formatPrice } from '@/lib/utils.js';

/**
 * Product specifications — Flipkart-style spec table.
 * Each row has `border-b border-line-subtle`; first cell is `w-40 text-ink-tertiary`,
 * second is `text-ink-primary`. No alternating stripe, clean white background.
 */
export function SpecTable({ product, categoryName }) {
  const rows = [
    ['Brand / Category', categoryName || '—'],
    ['SKU', product.sku],
    ['Price', formatPrice(product.price)],
    ['Availability', product.stock > 0 ? `In stock (${product.stock} units)` : 'Out of stock'],
    ['Listed', product.created_at ? new Date(product.created_at).toLocaleDateString() : '—'],
  ];

  return (
    <section className="mt-6" aria-label="Product specifications">
      <h2 className="text-sm font-bold text-ink-primary">Product details</h2>
      <table className="mt-2 w-full text-sm">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td className="w-40 border-b border-line-subtle py-2.5 text-ink-tertiary align-top">
                {k}
              </td>
              <td className="border-b border-line-subtle py-2.5 pl-3 text-ink-primary">
                <span className={k === 'Price' ? 'nums font-semibold' : undefined}>{v}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
