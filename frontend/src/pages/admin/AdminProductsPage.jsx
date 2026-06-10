import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Plus, Pencil, Trash2, PackageX, AlertTriangle, Search, Package } from 'lucide-react';
import { AdminPage } from '@/components/admin/AdminPage.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Badge } from '@/components/ui/Badge.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import { Input } from '@/components/ui/Input.jsx';
import { useProducts } from '@/features/products/hooks.js';
import { useDeleteProduct } from '@/features/admin/hooks.js';
import { formatPrice, stockLabel } from '@/lib/utils.js';
import { listStagger, fadeUp } from '@/lib/motion.js';

const STOCK_TONE = {
  'In stock':   'success',
  'Low stock':  'warning',
  'Out of stock': 'danger',
};

function ProductRow({ product }) {
  const [confirming, setConfirming] = useState(false);
  const del = useDeleteProduct();
  const stock = stockLabel(product.stock);
  const tone = STOCK_TONE[stock.text] ?? 'neutral';

  return (
    <motion.tr
      variants={fadeUp}
      className="group border-t border-line-subtle transition-colors duration-150 hover:bg-fill/50"
    >
      <td className="px-5 py-3.5">
        <div className="flex items-center gap-3">
          {product.thumbnail_url ? (
            <img
              src={product.thumbnail_url}
              alt=""
              className="size-9 shrink-0 rounded-md border border-line-subtle object-cover"
            />
          ) : (
            <span className="grid size-9 shrink-0 place-items-center rounded-md border border-line-subtle bg-bg-sunken text-ink-tertiary">
              <Package className="size-4" aria-hidden="true" />
            </span>
          )}
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-ink-primary">{product.name}</p>
            <p className="text-xs text-ink-tertiary">{product.sku}</p>
          </div>
        </div>
      </td>
      <td className="px-5 py-3.5">
        <span className="nums text-sm font-semibold text-ink-primary">
          {formatPrice(product.price)}
        </span>
        {product.compare_at_price && (
          <p className="nums text-xs text-ink-tertiary line-through">
            {formatPrice(product.compare_at_price)}
          </p>
        )}
      </td>
      <td className="px-5 py-3.5 nums text-sm text-ink-secondary">
        {product.stock}
      </td>
      <td className="px-5 py-3.5">
        <Badge tone={tone} dot>{stock.text}</Badge>
      </td>
      <td className="px-5 py-3.5">
        {confirming ? (
          <div className="flex items-center justify-end gap-2">
            <span className="text-xs text-ink-secondary">Delete?</span>
            <Button
              variant="destructive"
              size="sm"
              loading={del.isPending}
              onClick={() => del.mutate(product.id)}
            >
              Confirm
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={del.isPending}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </Button>
          </div>
        ) : (
          <div className="flex items-center justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
            <Link
              to={`/admin/products/${product.id}/edit`}
              aria-label={`Edit ${product.name}`}
              className="grid size-8 place-items-center rounded-sm text-ink-tertiary transition-colors hover:bg-fill hover:text-ink-primary focus-visible:focus-ring"
            >
              <Pencil className="size-3.5" />
            </Link>
            <button
              type="button"
              aria-label={`Delete ${product.name}`}
              onClick={() => setConfirming(true)}
              className="grid size-8 place-items-center rounded-sm text-ink-tertiary transition-colors hover:bg-danger/10 hover:text-danger focus-visible:focus-ring"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        )}
      </td>
    </motion.tr>
  );
}

export default function AdminProductsPage() {
  const [search, setSearch] = useState('');
  const { data, isLoading, isError, refetch } = useProducts({ page: 1, page_size: 100 });
  const products = data?.items ?? [];

  const filtered = search.trim()
    ? products.filter(
        (p) =>
          p.name.toLowerCase().includes(search.toLowerCase()) ||
          p.sku.toLowerCase().includes(search.toLowerCase()),
      )
    : products;

  const total = data?.total ?? 0;

  return (
    <AdminPage
      title="Products"
      description={
        isLoading
          ? 'Loading catalog…'
          : `${total.toLocaleString()} product${total === 1 ? '' : 's'} in the catalog.`
      }
      action={
        <Link to="/admin/products/new">
          <Button size="sm">
            <Plus className="size-4" aria-hidden="true" />
            New product
          </Button>
        </Link>
      }
    >
      {/* Toolbar */}
      {!isError && (
        <div className="mb-5 flex items-center gap-3">
          <div className="w-72">
            <Input
              icon={Search}
              placeholder="Search name or SKU…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
        </div>
      )}

      {isError ? (
        <EmptyState
          icon={AlertTriangle}
          iconTone="danger"
          title="We couldn't load products"
          description="Something went wrong on our end. Please try again."
          action={
            <Button size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : isLoading ? (
        <div className="overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated">
          <div className="border-b border-line-subtle px-5 py-3">
            <Skeleton variant="text" lines={1} className="w-32" />
          </div>
          <div className="flex flex-col divide-y divide-line-subtle">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-5 py-3.5">
                <Skeleton variant="circle" className="size-9 shrink-0" />
                <div className="flex-1">
                  <Skeleton variant="text" lines={2} />
                </div>
                <Skeleton className="h-4 w-16" />
                <Skeleton className="h-4 w-10" />
                <Skeleton className="h-6 w-20 rounded-full" />
              </div>
            ))}
          </div>
        </div>
      ) : filtered.length === 0 ? (
        search ? (
          <EmptyState
            icon={Search}
            title="No products match"
            description={`No products found for "${search}". Try a different name or SKU.`}
            size="sm"
          />
        ) : (
          <EmptyState
            icon={PackageX}
            title="No products yet"
            description="Add your first product to start building the catalog."
            action={
              <Link to="/admin/products/new">
                <Button size="sm">
                  <Plus className="size-4" aria-hidden="true" />
                  New product
                </Button>
              </Link>
            }
          />
        )
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line-subtle bg-bg-elevated shadow-md">
          <table className="w-full min-w-[640px]">
            <thead>
              <tr className="border-b border-line-subtle bg-bg-sunken text-left">
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                  Product
                </th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                  Price
                </th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                  Stock
                </th>
                <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                  Status
                </th>
                <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wide text-ink-tertiary">
                  Actions
                </th>
              </tr>
            </thead>
            <motion.tbody
              variants={listStagger(0.04)}
              initial="hidden"
              animate="show"
            >
              {filtered.map((p) => (
                <ProductRow key={p.id} product={p} />
              ))}
            </motion.tbody>
          </table>
          {filtered.length < total && (
            <div className="border-t border-line-subtle px-5 py-2.5 text-center text-xs text-ink-tertiary">
              Showing {filtered.length} of {total} — add more filters to narrow results.
            </div>
          )}
        </div>
      )}
    </AdminPage>
  );
}
