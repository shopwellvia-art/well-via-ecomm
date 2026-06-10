import { ProductRail } from '@/features/products/components/ProductRail.jsx';
import { useProductsByIds } from '@/features/products/hooks.js';
import { useHistoryIds } from './store.js';

/**
 * Self-contained rail: reads ids from the local history store, hydrates them
 * via /products/by-ids/batch, and renders nothing when there's nothing to show.
 *
 * Pass the current product id as `excludeId` so it doesn't appear in the rail
 * looking at it.
 */
export function BrowsingHistoryRail({ excludeId, title = 'Recently viewed' }) {
  const ids = useHistoryIds({ excludeId, limit: 12 });
  const { data: products = [], isLoading } = useProductsByIds(ids);

  if (ids.length === 0) return null;
  return (
    <ProductRail title={title} products={products} isLoading={isLoading} />
  );
}
