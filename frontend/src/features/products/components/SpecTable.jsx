import { formatPrice } from '@/lib/utils.js';

/**
 * Product specifications — Amazon-style striped key/value table.
 * Flat, no motion, clean border. All data derived from real product fields.
 */
export function SpecTable({ product, categoryName }) {
  const rows = [
    ['Brand / Category', categoryName || '—'],
    ['SKU', product.sku],
    ['Price', formatPrice(product.price)],
    ['Availability', product.stock > 0 ? `In stock (${product.stock} units)` : 'Out of stock'],
    ['Listed', new Date(product.created_at).toLocaleDateString()],
  ];

  return (
    <section className="mt-6" aria-label="Product specifications">
      <div className="overflow-hidden rounded-sm border border-line-subtle">
        <p className="border-b border-line-subtle bg-bg-elevated px-4 py-2.5 text-sm font-semibold text-ink-primary">
          Product details
        </p>
        <table className="w-full text-sm">
          <tbody>
            {rows.map(([k, v], i) => (
              <tr
                key={k}
                className={i % 2 === 0 ? 'bg-bg-elevated' : 'bg-bg-sunken'}
              >
                <th
                  scope="row"
                  className="w-2/5 border-r border-line-subtle px-4 py-2.5 text-left text-xs font-medium text-ink-secondary"
                >
                  {k}
                </th>
                <td className="px-4 py-2.5 text-xs text-ink-primary">
                  <span className={k === 'Price' ? 'nums font-semibold' : undefined}>{v}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
