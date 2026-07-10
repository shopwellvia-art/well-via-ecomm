import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import WImage from '@/components/storefront/WImage';
import {
  HeartIcon,
  CloseIcon,
  Check,
  LockIcon,
  BagIcon,
  Stars,
} from '@/components/storefront/Icons';
import {
  useWishlist,
  useRemoveFromWishlist,
} from '@/features/wishlist/hooks.js';
import { useProductsByIds } from '@/features/products/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice } from '@/lib/utils.js';

const PAGE_SIZE = 6;

/* ── Page shell (header row + content) ───────────────────────────────────── */
function Shell({ children, count }) {
  const navigate = useNavigate();
  return (
    <main className="bg-wcanvas min-h-[70vh] px-5 sm:px-10 lg:px-14 py-8 lg:py-12">
      <div className="max-w-[1060px] mx-auto">
        <div className="flex items-center gap-4 mb-2">
          <button
            type="button"
            onClick={() => navigate(-1)}
            aria-label="Go back"
            className="w-10 h-10 rounded-full border border-wink/40 bg-transparent text-wink grid place-items-center cursor-pointer hover:bg-wcard transition-colors"
          >
            <span aria-hidden="true" className="text-[16px] leading-none">←</span>
          </button>
          <h1 className="font-wserif font-semibold text-[clamp(28px,3.2vw,38px)] text-wink m-0">
            My Wishlist
          </h1>
        </div>
        {count != null && (
          <p className="font-wserif text-[clamp(16px,1.6vw,21px)] text-wmuted text-right m-0 mb-6">
            Showing {count} Saved {count === 1 ? 'Product' : 'Products'}
          </p>
        )}
        {children}
      </div>
    </main>
  );
}

/* ── Loading skeleton row ────────────────────────────────────────────────── */
function SkeletonRow() {
  return (
    <div className="bg-wcard border border-wline rounded-xl2 p-4 sm:p-5 flex items-center gap-5 animate-pulse">
      <div className="w-[120px] h-[120px] rounded-lg bg-wcanvas shrink-0" />
      <div className="flex-1 space-y-3">
        <div className="h-5 bg-wcanvas rounded w-1/3" />
        <div className="h-3 bg-wcanvas rounded w-1/2" />
        <div className="h-4 bg-wcanvas rounded w-1/4" />
      </div>
      <div className="h-11 w-36 bg-wcanvas rounded-lg shrink-0 hidden sm:block" />
    </div>
  );
}

/* ── One wishlist row — image | name/stars/snippet | price | CTA | remove ── */
function WishlistRow({ item, product }) {
  const remove = useRemoveFromWishlist();
  const add = useAddToCart();
  const [addErr, setAddErr] = useState(false);
  const added = add.isSuccess && add.variables?.productId === item.product_id;

  // Enriched fields (from /products/by-ids) with wishlist-item fallbacks.
  const price = product?.price ?? item.price;
  const compareAt = product?.compare_at_price;
  const isDiscounted = compareAt != null && Number(compareAt) > Number(price);
  const snippet = product?.short_description || null;
  const ratingCount = product?.rating_count ?? 0;
  const ratingAvg = Number(product?.rating_avg ?? 0);
  const outOfStock = product != null && product.stock <= 0;

  function handleAddToCart() {
    setAddErr(false);
    add.mutate(
      { productId: item.product_id, quantity: 1 },
      { onError: () => setAddErr(true) },
    );
  }

  return (
    <article className="relative bg-wcard border border-wline rounded-xl2 p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center gap-5 animate-rise">
      {/* Remove — circular × pinned top-right */}
      <button
        type="button"
        onClick={() => remove.mutate(item.product_id)}
        disabled={remove.isPending}
        aria-label={`Remove ${item.name} from wishlist`}
        className="absolute top-4 right-4 w-8 h-8 rounded-full border border-wink/35 bg-transparent text-wink grid place-items-center cursor-pointer hover:bg-wcanvas disabled:opacity-40 transition-colors"
      >
        <CloseIcon size={13} />
      </button>

      {/* Framed product image */}
      <Link
        to={`/products/${item.product_id}`}
        aria-label={item.name}
        className="shrink-0 border border-wline rounded-lg bg-white p-1.5 self-start sm:self-auto"
      >
        <WImage
          src={item.image_url}
          alt={item.name}
          className="w-[120px] h-[120px] rounded-md"
        />
      </Link>

      {/* Name + stars + snippet + price */}
      <div className="flex-1 min-w-0 pr-8 sm:pr-0">
        <Link
          to={`/products/${item.product_id}`}
          className="block text-[20px] font-medium leading-snug text-wink no-underline hover:text-wgreen transition-colors truncate"
        >
          {item.name}
        </Link>

        {ratingCount > 0 && (
          <div className="mt-0.5">
            <Stars
              count={Math.round(ratingAvg)}
              className="text-[17px] text-[#E8B33C]"
            />
          </div>
        )}

        {snippet && (
          <p className="font-wserif text-[16px] text-wink/70 leading-[1.45] m-0 mt-1 line-clamp-1">
            {snippet}
          </p>
        )}

        <div className="flex items-baseline gap-2.5 mt-2">
          <span className="text-[19px] font-medium text-wink">
            {formatPrice(price)}
          </span>
          {isDiscounted && (
            <span className="text-[14px] text-wmuted line-through">
              {formatPrice(compareAt)}
            </span>
          )}
        </div>

        {addErr && (
          <p className="text-[12px] text-red-600 m-0 mt-1.5">
            Could not add to cart. Please try again.
          </p>
        )}
      </div>

      {/* Add to cart */}
      <div className="shrink-0 sm:self-end">
        <button
          type="button"
          onClick={handleAddToCart}
          disabled={add.isPending || added || outOfStock}
          className="min-w-[176px] bg-wgreen text-white border-0 rounded-lg px-8 py-3 font-wserif text-[19px] tracking-wide cursor-pointer hover:bg-wgreen-dark disabled:opacity-60 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
        >
          {outOfStock ? (
            'Out of stock'
          ) : added ? (
            <>
              <Check size={15} />
              Added
            </>
          ) : add.isPending ? (
            'Adding…'
          ) : (
            <>
              <BagIcon size={15} />
              Add to cart
            </>
          )}
        </button>
      </div>
    </article>
  );
}

/* ── Empty wishlist ──────────────────────────────────────────────────────── */
function EmptyWishlist() {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-5 text-center">
      <div className="w-[60px] h-[60px] rounded-full flex items-center justify-center bg-wcard border border-wline text-wmuted">
        <HeartIcon size={26} />
      </div>
      <div>
        <p className="font-wserif text-[22px] text-wink m-0 mb-1">
          Nothing saved yet
        </p>
        <p className="text-[13px] text-wmuted m-0">
          Tap the heart on any product to save it here.
        </p>
      </div>
      <Link
        to="/products"
        className="bg-wgreen text-white rounded-full px-7 py-[11px] text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors no-underline"
      >
        Browse Products
      </Link>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────────────── */
export default function WishlistPage() {
  const user = useAuthStore((s) => s.user);
  const { data: items = [], isLoading, isError, error, refetch } = useWishlist();
  const status = error?.response?.status;

  // Enrich rows with rating / snippet / compare-at price from the catalog.
  const ids = items.map((i) => i.product_id);
  const { data: enriched = [] } = useProductsByIds(ids);
  const productById = new Map(enriched.map((p) => [p.id, p]));

  // "Show more" — client-side, 6 rows at a time.
  const [visible, setVisible] = useState(PAGE_SIZE);

  // Not signed in — sign-in prompt
  if (!user || status === 401) {
    return (
      <Shell>
        <div className="flex flex-col items-center justify-center py-20 gap-5 text-center">
          <div className="w-[60px] h-[60px] rounded-full flex items-center justify-center bg-wcard border border-wline text-wmuted">
            <LockIcon size={26} />
          </div>
          <div>
            <p className="font-wserif text-[22px] text-wink m-0 mb-1">
              Sign in to view your wishlist
            </p>
            <p className="text-[13px] text-wmuted m-0">
              Saved items follow you across devices once you sign in.
            </p>
          </div>
          <Link
            to="/login?next=/wishlist"
            className="bg-wgreen text-white rounded-full px-7 py-[11px] text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors no-underline"
          >
            Sign In
          </Link>
        </div>
      </Shell>
    );
  }

  // Error state
  if (isError) {
    return (
      <Shell>
        <div className="flex flex-col items-center justify-center py-16 gap-5 text-center">
          <p className="font-wserif text-[20px] text-wink m-0 mb-1">
            Couldn&apos;t load your wishlist
          </p>
          <p className="text-[13px] text-wmuted m-0">
            Something went wrong on our end. Please try again.
          </p>
          <button
            type="button"
            onClick={() => refetch()}
            className="bg-wgreen text-white rounded-full px-7 py-[11px] text-[13px] tracking-wide hover:bg-wgreen-dark transition-colors border-0 cursor-pointer"
          >
            Retry
          </button>
        </div>
      </Shell>
    );
  }

  if (isLoading) {
    return (
      <Shell>
        <div className="flex flex-col gap-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonRow key={i} />
          ))}
        </div>
      </Shell>
    );
  }

  if (items.length === 0) {
    return (
      <Shell count={0}>
        <EmptyWishlist />
      </Shell>
    );
  }

  const shown = items.slice(0, visible);

  return (
    <Shell count={items.length}>
      <div className="flex flex-col gap-5">
        {shown.map((item) => (
          <WishlistRow
            key={item.id}
            item={item}
            product={productById.get(item.product_id)}
          />
        ))}
      </div>

      {items.length > visible && (
        <div className="text-center mt-10">
          <button
            type="button"
            onClick={() => setVisible((v) => v + PAGE_SIZE)}
            className="bg-transparent border-0 cursor-pointer font-wserif text-[19px] text-wink underline underline-offset-4 hover:text-wgreen transition-colors"
          >
            Show more
          </button>
        </div>
      )}
    </Shell>
  );
}
