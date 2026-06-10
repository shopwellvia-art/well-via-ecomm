import { motion } from 'framer-motion';
import { listStagger, fadeUp } from '@/lib/motion.js';
import { formatPrice } from '@/lib/utils.js';

/**
 * Compact spec table — two-column key/value strip derived entirely from real
 * product fields. No fake data introduced.
 */
export function SpecTable({ product, categoryName }) {
  const rows = [
    ['Brand', categoryName || '—'],
    ['SKU', product.sku],
    ['Unit price', formatPrice(product.price)],
    ['Availability', product.stock > 0 ? `In stock (${product.stock})` : 'Out of stock'],
    ['Listed', new Date(product.created_at).toLocaleDateString()],
  ];

  return (
    <section className="mt-6" aria-label="Product specifications">
      <motion.div
        className="overflow-hidden rounded-md border border-line-subtle shadow-sm"
        variants={listStagger(0.04)}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, amount: 0.2 }}
      >
        <table className="w-full text-sm">
          <tbody>
            {rows.map(([k, v], i) => (
              <motion.tr
                key={k}
                variants={fadeUp}
                className={i % 2 === 0 ? 'bg-bg-elevated' : 'bg-bg-sunken'}
              >
                <th
                  scope="row"
                  className="w-1/3 border-r border-line-subtle px-4 py-3 text-left font-medium text-ink-secondary"
                >
                  {k}
                </th>
                <td className="px-4 py-3 font-normal text-ink-primary">
                  <span className={k === 'Unit price' ? 'nums' : undefined}>{v}</span>
                </td>
              </motion.tr>
            ))}
          </tbody>
        </table>
      </motion.div>
    </section>
  );
}
