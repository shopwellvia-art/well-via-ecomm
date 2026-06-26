import { useState } from 'react';
import { Link } from 'react-router-dom';
import AccountLayout from '@/components/storefront/AccountLayout';
import WImage from '@/components/storefront/WImage';
import {
  HeartIcon,
  BagIcon,
  Check,
  LockIcon,
} from '@/components/storefront/Icons';
import {
  useWishlist,
  useRemoveFromWishlist,
} from '@/features/wishlist/hooks.js';
import { useAddToCart } from '@/features/cart/hooks.js';
import { useAuthStore } from '@/features/auth/store.js';
import { formatPrice } from '@/lib/utils.js';

// ── Skeleton card while loading ──────────────────────────────────────────────
function SkeletonCard() {
  return (
    <div className="bg-wcard border border-wline rounded-xl2 overflow-hidden animate-pulse">
      <div className="h-[200px] bg-wcanvas" />
      <div className="p-4 space-y-3">
        <div className="h-5 bg-wcanvas rounded-full w-3/4" />
        <div className="h-3 bg-wcanvas rounded-full w-1/2" />
        <div className="mt-4 flex items-center justify-between gap-3">
          <div className="h-6 bg-wcanvas rounded-full w-1/3" />
          <div className="h-9 bg-wcanvas rounded-full flex-1" />
        </div>
      </div>
    </div>
  );
}

// ── Individual wishlist card ─────────────────────────────────────────────────
function WishlistCard({ item }) {
  const remove = useRemoveFromWishlist();
  const add = useAddToCart();
  const added = add.isSuccess && add.variables?.productId === item.product_id;
  const [moveErr, setMoveErr] = useState(false);

  function handleMoveToCart() {
    setMoveErr(false);
    add.mutate(
      { productId: item.product_id, quantity: 1 },
      {
        onSuccess: () => {
          remove.mutate(item.product_id);
        },
        onError: () => {
          setMoveErr(true);
        },
      },
    );
  }

  return (
    <article className="bg-wcard border border-wline rounded-xl2 overflow-hidden flex flex-col transition-transform duration-200 hover:-translate-y-[4px] hover:shadow-[0_20px_44px_-24px_rgba(40,30,10,0.38)] animate-rise">
      {/* Image area */}
      <div
        className="relative"
        style={{ background: 'linear-gradient(160deg,#efe9df,#e4dccd)' }}
      >
        <Link
          to={`/products/${item.product_id}`}
          aria-label={item.name}
          tabIndex={-1}
        >
          <WImage
            src={item.image_url}
            alt={item.name}
            className="w-full h-[200px]"
          />
        </Link>

        {/* Heart button — removes item from wishlist */}
        <button
          type="button"
          onClick={() => remove.mutate(item.product_id)}
          disabled={remove.isPending}
          aria-label={`Remove ${item.name} from wishlist`}
          className="absolute top-3 right-3 w-8 h-8 rounded-full flex items-center justify-center border border-wline bg-wcard/80 backdrop-blur-sm text-red-500 transition-colors hover:bg-wcard disabled:opacity-40"
        >
          <HeartIcon size={15} filled />
        </button>
      </div>

      {/* Card body */}
      <div className="p-[18px] pb-5 flex flex-col flex-1">
        <Link
          to={`/products/${item.product_id}`}
          className="font-wserif font-semibold text-[21px] leading-[1.15] mb-[5px] no-underline text-wink hover:text-wgreen transition-colors"
        >
          {item.name}
        </Link>

        {item.brand ? (
          <p className="text-[12.5px] text-wmuted leading-[1.5] m-0 mb-3 font-light flex-1">
            {item.brand}
          </p>
        ) : (
          <div className="flex-1 min-h-[14px]" />
        )}

        {/* Price */}
        <div className="font-wserif text-[20px] text-wink mb-[13px]">
          {formatPrice(item.price)}
        </div>

        {/* Move-to-cart error */}
        {moveErr && (
          <p className="text-[11.5px] text-red-600 mb-2 leading-snug">
            Could not add to cart. Please try again.
          </p>
        )}

        {/* CTA — add to cart then remove from wishlist on success */}
        <button
          type="button"
          onClick={handleMoveToCart}
          disabled={add.isPending || remove.isPending || added}
          className="w-full bg-wgreen text-white border-0 rounded-full py-3 text-[12.5px] tracking-wide cursor-pointer hover:bg-wgreen-dark disabled:opacity-60 disabled:cursor-wait transition-colors flex items-center justify-center gap-2"
        >
          {added ? (
            <>
              <Check size={14} />
              Moved to Cart
            </>
          ) : add.isPending ? (
            'Adding…'
          ) : (
            <>
              <BagIcon size={14} />
              Add to Cart
            </>
          )}
        </button>
      </div>
    </article>
  );
}

// ── Empty wishlist ────────────────────────────────────────────────────────────
function EmptyWishlist() {
  return (
    <div className="flex flex-col items-center justify-center py-20 gap-5 text-center">
      <div className="w-[60px] h-[60px] rounded-full flex items-center justify-center bg-wcanvas border border-wline text-wmuted">
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

// ── Page ─────────────────────────────────────────────────────────────────────
export default function WishlistPage() {
  const user = useAuthStore((s) => s.user);
  const { data: items = [], isLoading, isError, error, refetch } = useWishlist();
  const status = error?.response?.status;

  // Not signed in — show sign-in prompt inside AccountLayout
  if (!user || status === 401) {
    return (
      <AccountLayout active="wishlist">
        <div className="flex flex-col items-center justify-center py-20 gap-5 text-center">
          <div className="w-[60px] h-[60px] rounded-full flex items-center justify-center bg-wcanvas border border-wline text-wmuted">
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
      </AccountLayout>
    );
  }

  // Error state
  if (isError) {
    return (
      <AccountLayout active="wishlist">
        <h1 className="font-wserif font-medium text-[clamp(28px,3.4vw,40px)] m-0 mb-1.5 text-wink">
          Wishlist
        </h1>
        <div className="flex flex-col items-center justify-center py-16 gap-5 text-center">
          <p className="font-wserif text-[20px] text-wink m-0 mb-1">
            Couldn't load your wishlist
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
      </AccountLayout>
    );
  }

  return (
    <AccountLayout active="wishlist">
      {/* Section heading */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="font-wserif font-medium text-[clamp(28px,3.4vw,40px)] m-0 mb-1.5 text-wink">
            Wishlist
          </h1>
          <p className="text-[14px] text-wmuted m-0 font-light flex items-center gap-2">
            Rituals you're saving for later.
            {!isLoading && items.length > 0 && (
              <span className="inline-flex items-center justify-center bg-wgreen text-white text-[10.5px] font-semibold rounded-full w-5 h-5 shrink-0">
                {items.length}
              </span>
            )}
          </p>
        </div>

        {!isLoading && items.length > 0 && (
          <Link
            to="/products"
            className="shrink-0 text-[12.5px] text-wgreen hover:text-wgreen-dark underline underline-offset-2 transition-colors self-center"
          >
            Continue shopping
          </Link>
        )}
      </div>

      {/* Loading — skeleton grid */}
      {isLoading ? (
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyWishlist />
      ) : (
        /* Wishlist card grid */
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          {items.map((item) => (
            <WishlistCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </AccountLayout>
  );
}
