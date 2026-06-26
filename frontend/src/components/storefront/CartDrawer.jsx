import { useNavigate } from 'react-router-dom';
import { useCart, useUpdateCartQuantity, useRemoveFromCart } from '@/features/cart/hooks';
import { useCartDrawer } from '@/features/cart/drawerStore';
import { useAuthStore } from '@/features/auth/store';
import { formatPrice, cn } from '@/lib/utils';
import { LeafMark } from './Logo';
import { CloseIcon, LockIcon } from './Icons';
import WImage from './WImage';

/**
 * CartDrawer — slide-in cart panel.
 *
 * Always mounted in Layout; renders null when closed so the CSS entry
 * animations (animate-dim / animate-slidein) fire fresh on each open.
 *
 * Wiring:
 *   open/close  → useCartDrawer()
 *   items       → useCart().data.items  (disabled when signed out)
 *   qty ±       → useUpdateCartQuantity() — quantity=0 removes the line
 *   remove btn  → useRemoveFromCart()
 *   View Cart   → /cart
 *   Checkout    → /checkout
 */
export default function CartDrawer() {
  const navigate = useNavigate();
  const { isOpen, closeDrawer } = useCartDrawer();
  const token = useAuthStore((s) => s.accessToken);
  const { data: cartData, isLoading } = useCart();
  const updateQty = useUpdateCartQuantity();
  const removeItem = useRemoveFromCart();

  // Not open → nothing in the DOM.
  if (!isOpen) return null;

  const items = cartData?.items ?? [];
  const subtotal = cartData?.subtotal ?? 0;

  // Qty helpers — useUpdateCartQuantity with quantity=0 acts as remove.
  function handleDec(item) {
    if (item.quantity <= 1) {
      removeItem.mutate(item.product_id);
    } else {
      updateQty.mutate({ productId: item.product_id, quantity: item.quantity - 1 });
    }
  }
  function handleInc(item) {
    updateQty.mutate({ productId: item.product_id, quantity: item.quantity + 1 });
  }
  function handleRemove(item) {
    removeItem.mutate(item.product_id);
  }

  function goCheckout() {
    closeDrawer();
    navigate('/checkout');
  }
  function goCart() {
    closeDrawer();
    navigate('/cart');
  }
  function goShop() {
    closeDrawer();
    navigate('/products');
  }
  function goLogin() {
    closeDrawer();
    navigate('/login');
  }

  const showFooter = token && items.length > 0;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={closeDrawer}
        className="fixed inset-0 bg-wink/30 z-[90] animate-dim"
        aria-hidden="true"
      />

      {/* Panel */}
      <aside
        className="fixed top-0 right-0 h-full w-full sm:w-[420px] max-w-full bg-wpaper z-[91] flex flex-col shadow-[-20px_0_60px_-20px_rgba(40,30,10,0.4)] animate-slidein"
        role="dialog"
        aria-modal="true"
        aria-label="Shopping cart"
      >
        {/* Header */}
        <div className="px-6 py-[22px] border-b border-wline flex items-center justify-between shrink-0">
          <div className="flex items-center gap-2.5">
            <LeafMark size={22} dot={false} />
            <span className="font-display tracking-[0.18em] text-[15px] text-wgreen">
              YOUR CART
            </span>
          </div>
          <button
            onClick={closeDrawer}
            className="bg-transparent border-0 p-0 cursor-pointer text-wmuted hover:text-wink transition-colors"
            aria-label="Close cart"
          >
            <CloseIcon size={22} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* Signed-out state */}
          {!token && (
            <div className="text-center text-wmuted py-[60px] text-[14px]">
              <p className="mb-4">Sign in to view your cart.</p>
              <button
                onClick={goLogin}
                className="bg-wgreen text-white border-0 rounded-full px-[26px] py-3 text-[13px] cursor-pointer hover:bg-wgreen-dark transition-colors"
              >
                Sign In
              </button>
            </div>
          )}

          {/* Signed-in, empty cart */}
          {token && !isLoading && items.length === 0 && (
            <div className="text-center text-wmuted py-[60px] text-[14px]">
              <p className="mb-4">Your cart is empty.</p>
              <button
                onClick={goShop}
                className="bg-wgreen text-white border-0 rounded-full px-[26px] py-3 text-[13px] cursor-pointer hover:bg-wgreen-dark transition-colors"
              >
                Shop Now
              </button>
            </div>
          )}

          {/* Loading skeleton */}
          {token && isLoading && (
            <div className="flex flex-col gap-4 pt-2">
              {[1, 2].map((n) => (
                <div key={n} className="flex gap-3 animate-pulse">
                  <div className="w-[74px] h-[88px] shrink-0 rounded-xl2 bg-wline/50" />
                  <div className="flex-1 space-y-2 pt-1">
                    <div className="h-4 bg-wline/50 rounded-full w-3/4" />
                    <div className="h-3 bg-wline/50 rounded-full w-1/2" />
                    <div className="h-8 bg-wline/50 rounded-full w-2/3 mt-3" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Item list */}
          {token && !isLoading && items.length > 0 && (
            <div className="flex flex-col gap-[18px]">
              {items.map((item) => (
                <div key={item.product_id} className="flex gap-3.5 pb-[18px] border-b border-wline last:border-b-0">
                  <WImage
                    src={item.image_url}
                    alt={item.name}
                    shape="rounded"
                    className="w-[74px] h-[88px] shrink-0 border border-wline"
                  />
                  <div className="flex-1 flex flex-col min-w-0">
                    <div className="flex justify-between gap-2">
                      <span className="font-wserif text-[17px] leading-tight text-wink">
                        {item.name}
                      </span>
                      <button
                        onClick={() => handleRemove(item)}
                        className="bg-transparent border-0 p-0 cursor-pointer text-wmuted hover:text-wink shrink-0 transition-colors"
                        aria-label={`Remove ${item.name} from cart`}
                      >
                        <CloseIcon size={16} stroke="#6F6A60" />
                      </button>
                    </div>
                    {item.brand && (
                      <span className="text-[12px] text-wmuted mt-0.5">{item.brand}</span>
                    )}
                    <div className="flex items-center justify-between mt-auto pt-3">
                      {/* Qty stepper */}
                      <div className="flex items-center border border-wline rounded-full overflow-hidden">
                        <button
                          onClick={() => handleDec(item)}
                          className="bg-transparent border-0 px-[13px] py-[7px] text-[15px] cursor-pointer text-wink hover:text-wgreen transition-colors leading-none"
                          aria-label="Decrease quantity"
                        >
                          −
                        </button>
                        <span className="text-[13px] min-w-[20px] text-center select-none">
                          {item.quantity}
                        </span>
                        <button
                          onClick={() => handleInc(item)}
                          className="bg-transparent border-0 px-[13px] py-[7px] text-[15px] cursor-pointer text-wink hover:text-wgreen transition-colors leading-none"
                          aria-label="Increase quantity"
                        >
                          +
                        </button>
                      </div>
                      {/* Line total */}
                      <span className="font-wserif text-[18px] text-wink">
                        {formatPrice(item.line_total)}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer — subtotal + CTAs */}
        {showFooter && (
          <div className="px-6 py-5 border-t border-wline bg-wcard shrink-0">
            <div className="flex items-center justify-between mb-4">
              <span className="text-[15px] text-wink">Subtotal</span>
              <span className="font-wserif text-[24px] text-wink">
                {formatPrice(subtotal)}
              </span>
            </div>
            <div className="flex gap-3 mb-3">
              <button
                onClick={goCart}
                className="flex-1 bg-transparent border border-wgreen text-wgreen rounded-full py-[13px] text-[13px] cursor-pointer hover:bg-wgreen hover:text-white transition-colors"
              >
                View Cart
              </button>
              <button
                onClick={goCheckout}
                className="flex-1 bg-wgreen text-white border-0 rounded-full py-[13px] text-[13px] cursor-pointer hover:bg-wgreen-dark transition-colors"
              >
                Checkout
              </button>
            </div>
            <div className="flex items-center justify-center gap-1.5 text-[12px] text-wmuted">
              <LockIcon size={13} stroke="#B49A63" />
              Checkout is encrypted &amp; secure
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
