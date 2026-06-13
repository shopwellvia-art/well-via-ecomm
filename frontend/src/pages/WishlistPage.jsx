import { Link } from 'react-router-dom';
import { Heart, ShoppingBag, Trash2, ArrowRight, Lock, AlertTriangle, Check } from 'lucide-react';
import { Page } from '@/components/layout/Page.jsx';
import { Breadcrumbs } from '@/components/layout/Breadcrumbs.jsx';
import { Button } from '@/components/ui/Button.jsx';
import { Skeleton } from '@/components/ui/Skeleton.jsx';
import { EmptyState } from '@/components/feedback/EmptyState.jsx';
import {
  useWishlist,
  useRemoveFromWishlist,
} from '@/features/wishlist/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice } from '@/lib/utils.js';

function WishlistItem({ item }) {
  const remove = useRemoveFromWishlist();
  const add = useAddToCart();
  const added = add.isSuccess && add.variables?.productId === item.product_id;

  function handleMoveToCart() {
    add.mutate(
      { productId: item.product_id, quantity: 1 },
      {
        onSuccess: () => {
          remove.mutate(item.product_id);
        },
      },
    );
  }

  return (
    <li className="flex items-center gap-4 border-b border-line-subtle px-4 py-4 last:border-b-0 hover:bg-bg-sunken/40 transition-colors">
      {/* Thumbnail */}
      <Link
        to={`/products/${item.product_id}`}
        className="flex size-20 shrink-0 items-center justify-center overflow-hidden rounded-sm border border-line-subtle bg-bg-sunken focus-visible:focus-ring"
      >
        {item.image_url ? (
          <img
            src={item.image_url}
            alt=""
            loading="lazy"
            className="size-full object-contain"
          />
        ) : (
          <span className="text-xl font-semibold text-ink-primary/20">
            {(item.name || '?').charAt(0).toUpperCase()}
          </span>
        )}
      </Link>

      {/* Info */}
      <div className="min-w-0 flex-1">
        <Link
          to={`/products/${item.product_id}`}
          className="block text-sm font-medium text-ink-primary hover:text-accent transition-colors focus-visible:focus-ring"
        >
          {item.name}
        </Link>
        <p className="nums mt-1 text-base font-semibold text-ink-primary">
          {formatPrice(item.price)}
        </p>
      </div>

      {/* Actions */}
      <div className="flex shrink-0 flex-col items-end gap-2 sm:flex-row sm:items-center">
        <Button
          size="sm"
          variant="cart"
          onClick={handleMoveToCart}
          loading={add.isPending}
          disabled={remove.isPending || added}
        >
          {added ? (
            <>
              <Check className="size-4" aria-hidden="true" />
              Moved
            </>
          ) : (
            <>
              <ShoppingBag className="size-4" aria-hidden="true" />
              Add to cart
            </>
          )}
        </Button>
        <button
          type="button"
          aria-label={`Remove ${item.name} from wishlist`}
          disabled={remove.isPending}
          onClick={() => remove.mutate(item.product_id)}
          className="grid size-8 place-items-center rounded-sm border border-transparent text-ink-tertiary transition-colors hover:border-danger/25 hover:bg-danger/8 hover:text-danger focus-visible:focus-ring disabled:opacity-40"
        >
          <Trash2 className="size-4" />
        </button>
      </div>
    </li>
  );
}

export default function WishlistPage() {
  const user = useAuthStore((s) => s.user);
  const { data: items = [], isLoading, isError, error, refetch } = useWishlist();
  const status = error?.response?.status;

  if (!user || status === 401) {
    return (
      <Page>
        <h1 className="text-lg font-semibold text-ink-primary">My Wishlist</h1>
        <div className="mt-8">
          <EmptyState
            icon={Lock}
            title="Sign in to view your wishlist"
            description="Saved items follow you across devices once you sign in."
            action={
              <Link to="/login?next=/wishlist">
                <Button size="sm">Sign in</Button>
              </Link>
            }
          />
        </div>
      </Page>
    );
  }

  if (isError) {
    return (
      <Page>
        <h1 className="text-lg font-semibold text-ink-primary">My Wishlist</h1>
        <div className="mt-8">
          <EmptyState
            icon={AlertTriangle}
            iconTone="danger"
            title="We couldn't load your wishlist"
            description="Something went wrong on our end. Please try again."
            action={
              <Button size="sm" onClick={() => refetch()}>
                Retry
              </Button>
            }
          />
        </div>
      </Page>
    );
  }

  return (
    <Page>
      <Breadcrumbs current="Wishlist" className="mb-4" />

      {/* Header row */}
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Heart className="size-5 text-danger" aria-hidden="true" />
          <h1 className="text-lg font-semibold text-ink-primary">My Wishlist</h1>
          {!isLoading && items.length > 0 && (
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-xs font-semibold text-accent">
              {items.length}
            </span>
          )}
        </div>
        {!isLoading && items.length > 0 && (
          <Link
            to="/products"
            className="inline-flex items-center gap-1 text-xs text-accent hover:text-accent-hover transition-colors focus-visible:focus-ring"
          >
            Continue shopping
            <ArrowRight className="size-3" aria-hidden="true" />
          </Link>
        )}
      </div>

      {isLoading ? (
        <div className="rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="border-b border-line-subtle px-4 py-4 last:border-b-0">
              <Skeleton className="h-16 rounded-sm" />
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={Heart}
          title="Nothing saved yet"
          description="Tap the heart on any product to save it for later."
          action={
            <Link to="/products">
              <Button size="sm">
                Browse products
                <ArrowRight className="size-4" aria-hidden="true" />
              </Button>
            </Link>
          }
        />
      ) : (
        <div className="rounded-sm border border-line-subtle bg-bg-elevated shadow-sm">
          <ul>
            {items.map((item) => (
              <WishlistItem key={item.id} item={item} />
            ))}
          </ul>
        </div>
      )}
    </Page>
  );
}
