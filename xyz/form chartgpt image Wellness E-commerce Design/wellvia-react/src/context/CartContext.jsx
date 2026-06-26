import { createContext, useContext, useMemo, useState } from 'react';
import { PRODUCTS, getProductById } from '../data/products';

const CartContext = createContext(null);

export function CartProvider({ children }) {
  // { [productId]: qty }
  const [items, setItems] = useState({ 1: 1 });
  const [isOpen, setIsOpen] = useState(false);

  const add = (id, qty = 1) =>
    setItems((c) => ({ ...c, [id]: (c[id] || 0) + qty }));
  const inc = (id) => setItems((c) => ({ ...c, [id]: (c[id] || 0) + 1 }));
  const dec = (id) =>
    setItems((c) => {
      const next = { ...c, [id]: (c[id] || 0) - 1 };
      if (next[id] <= 0) delete next[id];
      return next;
    });
  const remove = (id) =>
    setItems((c) => {
      const next = { ...c };
      delete next[id];
      return next;
    });

  const open = () => setIsOpen(true);
  const close = () => setIsOpen(false);
  const addAndOpen = (id, qty = 1) => {
    add(id, qty);
    open();
  };

  const lines = useMemo(
    () =>
      Object.keys(items).map((id) => {
        const product = getProductById(id);
        const qty = items[id];
        return { ...product, qty, lineTotal: product.price * qty };
      }),
    [items]
  );

  const subtotal = lines.reduce((t, l) => t + l.lineTotal, 0);
  const count = lines.reduce((t, l) => t + l.qty, 0);

  const value = {
    items, lines, subtotal, count, isOpen,
    add, inc, dec, remove, open, close, addAndOpen,
    products: PRODUCTS,
  };

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export const useCart = () => {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error('useCart must be used within CartProvider');
  return ctx;
};
