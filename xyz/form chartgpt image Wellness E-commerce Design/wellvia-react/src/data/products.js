// Central product catalogue + helpers for the WELLVIA store.

export const formatINR = (n) =>
  '\u20b9' + Number(n).toLocaleString('en-IN');

export const PRODUCTS = [
  { id: 1, slug: 'womens-wellness-gummies', name: "Women's Wellness Gummies", sub: 'Balance hormones, regulate cycles & glow', price: 999, flavor: 'Blueberry Flavour', rating: 4.8, reviews: 2140, cat: 'Gummies', tag: 'Bestseller' },
  { id: 2, slug: 'gut-health-gummies', name: 'Gut Health Gummies', sub: 'For lightness, comfort & easy digestion', price: 899, flavor: 'Apple Flavour', rating: 4.7, reviews: 1310, cat: 'Gummies', tag: 'Popular' },
  { id: 3, slug: 'immune-elixir', name: 'Immune Elixir', sub: 'Daily resilience & immunity support', price: 1199, flavor: 'Citrus Tonic', rating: 4.6, reviews: 980, cat: 'Elixirs', tag: 'New' },
  { id: 4, slug: 'stress-complex', name: 'Stress Complex', sub: 'Calm focus & everyday balance', price: 1099, flavor: 'Adaptogen Blend', rating: 4.7, reviews: 870, cat: 'Supplements', tag: 'Calm' },
  { id: 5, slug: 'sleep-support-gummies', name: 'Sleep Support Gummies', sub: 'Restful nights, naturally', price: 949, flavor: 'Berry Night', rating: 4.8, reviews: 1605, cat: 'Gummies', tag: 'Loved' },
  { id: 6, slug: 'daily-wellness-bundle', name: 'Daily Wellness Bundle', sub: 'A complete daily ritual, curated', price: 2499, flavor: '3-Product Set', rating: 4.9, reviews: 540, cat: 'Bundles', tag: 'Best Value' },
  { id: 7, slug: 'glow-skin-support', name: 'Glow & Skin Support', sub: 'Radiance from within', price: 1049, flavor: 'Rosehip + C', rating: 4.6, reviews: 720, cat: 'Supplements', tag: 'Glow' },
  { id: 8, slug: 'hormonal-balance-gummies', name: 'Hormonal Balance Gummies', sub: 'Gentle, science-led hormonal support', price: 999, flavor: 'Mixed Berry', rating: 4.7, reviews: 990, cat: 'Gummies', tag: 'Bestseller' },
];

export const CATEGORIES = ['All', 'Gummies', 'Elixirs', 'Supplements', 'Bundles'];

export const getProductBySlug = (slug) =>
  PRODUCTS.find((p) => p.slug === slug) || PRODUCTS[0];

export const getProductById = (id) =>
  PRODUCTS.find((p) => p.id === Number(id));

// Featured product (detail page hero) extras
export const PRODUCT_BENEFITS = [
  { icon: '\u2640', title: 'Hormonal Balance', desc: 'Helps reduce hormonal imbalance, mood swings & irritability.' },
  { icon: '\u25d1', title: 'Cycle Comfort', desc: 'Supports regular cycles and gentle period comfort.' },
  { icon: '\u2728', title: 'Vegan & Gentle', desc: 'Clean plant-based formula, easy on the stomach.' },
  { icon: '\u2727', title: 'Clear Skin Support', desc: 'Balances skin from within for a natural glow.' },
];

export const PRODUCT_INGREDIENTS = [
  { name: 'Myo-Inositol', desc: 'Supports hormonal balance and regular cycles.' },
  { name: 'Vitamin D3 + B-Complex', desc: 'Daily energy and mood support.' },
  { name: 'Folate & Iron', desc: "Essential nutrients for women's wellness." },
  { name: 'Blueberry Extract', desc: 'Antioxidant-rich, delicious natural flavour.' },
  { name: 'No Refined Sugar', desc: 'Sweetened cleanly \u2014 never a sugar spike.' },
];

export const REVIEWS = [
  { quote: 'My cycles are regular and my skin has never looked better. A gentle ritual that works.', name: 'Aditi K.', role: 'Verified Buyer' },
  { quote: 'Finally a supplement that feels luxurious, not clinical. The taste is genuinely lovely.', name: 'Sara M.', role: 'Verified Buyer' },
  { quote: 'Two months in and my energy through the day is steadier. Beautifully simple to keep up.', name: 'Priya R.', role: 'Verified Buyer' },
];

export const ORDERS = [
  { id: 'WV-10428', date: '12 Jun 2026', status: 'Delivered', total: 1998, items: 2 },
  { id: 'WV-10391', date: '28 May 2026', status: 'Shipped', total: 999, items: 1 },
  { id: 'WV-10333', date: '09 May 2026', status: 'Processing', total: 2499, items: 3 },
];

export const ADDRESSES = [
  { label: 'Home', name: 'Aditi Kapoor', line: '14 Rose Lane, Bandra West', cityline: 'Mumbai 400050, India', phone: '+91 98•••• ••21', isDefault: true },
  { label: 'Office', name: 'Aditi Kapoor', line: 'Tower B, Lotus Park, Andheri E', cityline: 'Mumbai 400059, India', phone: '+91 98•••• ••21', isDefault: false },
];
