import { Trash2 } from 'lucide-react';
import WImage from '@/components/storefront/WImage.jsx';
import { useUpdateCartQuantity, useRemoveFromCart } from '@/features/cart/hooks.js';
import { formatPrice } from '@/lib/utils.js';

/**
 * CartItemRow — one cart line as a white card (mockup: image, name, price
 * chip, qty stepper, trash). Shared by the CartDrawer and CartPage; owns its
 * own quantity/remove mutations so callers just pass the item.
 *
 * `item` is a server CartItemRead (guest carts compose the same shape and
 * add image_url).
 */
export default function CartItemRow({ item }) {
  const updateQty = useUpdateCartQuantity();
  const removeItem = useRemoveFromCart();

  const dec = () => {
    if (item.quantity <= 1) removeItem.mutate(item.product_id);
    else updateQty.mutate({ productId: item.product_id, quantity: item.quantity - 1 });
  };
  const inc = () =>
    updateQty.mutate({ productId: item.product_id, quantity: item.quantity + 1 });

  return (
    <div className="flex gap-3.5 rounded-xl2 border border-wline bg-wcard p-3.5 shadow-sm">
      <WImage
        src={item.image}
        alt={item.name}
        shape="rounded"
        className="w-[72px] h-[84px] shrink-0 border border-wline"
      />
      <div className="flex-1 flex flex-col min-w-0">
        <p className="font-medium text-[15px] leading-tight text-wink m-0 truncate">
          {item.name}
        </p>
        {item.pack_label && (
          <p className="text-[12px] text-wmuted m-0 mt-0.5">{item.pack_label}</p>
        )}

        <div className="flex items-center justify-between gap-2 mt-auto pt-3">
          {/* Price chip */}
          <span className="rounded-lg bg-[#08112C] text-white text-[13px] px-3 py-1.5 leading-none">
            {formatPrice(item.unit_price)}
          </span>

          <div className="flex items-center gap-2">
            {/* Qty stepper */}
            <div className="flex items-center border border-wline rounded-lg overflow-hidden bg-wpaper">
              <button
                onClick={dec}
                className="bg-transparent border-0 px-3 py-[6px] text-[15px] cursor-pointer text-wink hover:text-wgreen transition-colors leading-none"
                aria-label={`Decrease quantity of ${item.name}`}
              >
                −
              </button>
              <span className="text-[13px] min-w-[20px] text-center select-none">
                {item.quantity}
              </span>
              <button
                onClick={inc}
                className="bg-transparent border-0 px-3 py-[6px] text-[15px] cursor-pointer text-wink hover:text-wgreen transition-colors leading-none"
                aria-label={`Increase quantity of ${item.name}`}
              >
                +
              </button>
            </div>

            {/* Remove */}
            <button
              onClick={() => removeItem.mutate(item.product_id)}
              className="grid place-items-center size-8 rounded-lg border border-red-200 bg-red-50 text-red-500 cursor-pointer hover:bg-red-100 transition-colors"
              aria-label={`Remove ${item.name} from cart`}
            >
              <Trash2 className="size-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
