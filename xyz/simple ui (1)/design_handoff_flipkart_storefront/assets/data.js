/* ShopWell mockup — product data, render helpers, and interactions.
   Exposes window.SW. Pages call SW.* to render rails/grids and wire UI. */
(function () {
  // ── Iconography (lucide-style, used for intentional product placeholders) ──
  const ICONS = {
    headphones: '<path d="M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3"/>',
    earbuds: '<path d="M6 3v11"/><circle cx="6" cy="18" r="3"/><path d="M18 3v11"/><circle cx="18" cy="18" r="3"/><path d="M6 6h12"/>',
    watch: '<circle cx="12" cy="12" r="6"/><polyline points="12 10 12 12 13.5 12.5"/><path d="M16.51 17.35l-.35 3.83a2 2 0 0 1-2 1.82H9.83a2 2 0 0 1-2-1.82l-.35-3.83m.01-10.7l.35-3.83A2 2 0 0 1 9.83 1h4.35a2 2 0 0 1 2 1.82l.35 3.83"/>',
    laptop: '<path d="M20 16V7a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v9m16 0H4m16 0 1.28 2.55a1 1 0 0 1-.9 1.45H3.62a1 1 0 0 1-.9-1.45L4 16"/>',
    phone: '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
    speaker: '<rect x="4" y="2" width="16" height="20" rx="2"/><circle cx="12" cy="14" r="4"/><path d="M12 6h.01"/>',
    lamp: '<path d="M9 18h6"/><path d="M10 22h4"/><path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0 0 18 8 6 6 0 0 0 6 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 0 1 8.91 14"/>',
    camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    keyboard: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 16h10"/>',
    mouse: '<rect x="5" y="2" width="14" height="20" rx="7"/><path d="M12 6v4"/>',
    shirt: '<path d="M20.38 3.46 16 2a4 4 0 0 1-8 0L3.62 3.46a2 2 0 0 0-1.34 2.23l.58 3.47a1 1 0 0 0 .99.84H6v10c0 1.1.9 2 2 2h8a2 2 0 0 0 2-2V10h2.15a1 1 0 0 0 .99-.84l.58-3.47a2 2 0 0 0-1.34-2.23z"/>',
    shoe: '<path d="M2 18h16l3.5-2.5a1.5 1.5 0 0 0-.5-2.7L13 11l-4-4-3 1v6H2z"/><path d="M2 14h6"/>',
    bag: '<path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4z"/><path d="M3 6h18"/><path d="M16 10a4 4 0 0 1-8 0"/>',
    gift: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
    coffee: '<path d="M10 2v2M14 2v2M6 2v2"/><path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 0 1 0 8h-1"/>',
    gamepad: '<path d="M6 12h4m-2-2v4"/><path d="M15 11h.01M18 13h.01"/><rect x="2" y="6" width="20" height="12" rx="4"/>',
    book: '<path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/>',
    sparkle: '<path d="M9.94 14.06A2 2 0 0 0 8.5 12.6l-5.1-1.3 5.1-1.32A2 2 0 0 0 9.94 8.5l1.32-5.1 1.3 5.1a2 2 0 0 0 1.44 1.45l5.1 1.31-5.1 1.32a2 2 0 0 0-1.44 1.44l-1.3 5.1z"/>',
    tag: '<path d="M12.59 2.59A2 2 0 0 0 11.17 2H4a2 2 0 0 0-2 2v7.17a2 2 0 0 0 .59 1.42l8.83 8.83a2 2 0 0 0 2.82 0l7.17-7.17a2 2 0 0 0 0-2.82z"/><circle cx="7.5" cy="7.5" r="1.2"/>',
    home: '<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
    blender: '<path d="M6 2h12l-1 9H7L6 2z"/><path d="M9 16h6v4a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2z"/><path d="M8 11h8"/>',
  };

  function svg(name) {
    const inner = ICONS[name] || ICONS.bag;
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' + inner + '</svg>';
  }

  // Tints keyed loosely by category for the placeholder radial.
  const TINTS = {
    Audio: 'rgba(40,116,240,0.13)',
    Wearables: 'rgba(56,142,60,0.13)',
    Computers: 'rgba(123,97,255,0.13)',
    Mobiles: 'rgba(40,116,240,0.13)',
    Home: 'rgba(255,159,0,0.14)',
    Lighting: 'rgba(255,159,0,0.14)',
    Fashion: 'rgba(236,64,122,0.13)',
    Cameras: 'rgba(0,150,136,0.13)',
    Beauty: 'rgba(236,64,122,0.13)',
    Gaming: 'rgba(123,97,255,0.13)',
    Kitchen: 'rgba(251,100,30,0.13)',
  };

  // ── Catalogue ──────────────────────────────────────────────────────────────
  const P = (id, name, brand, cat, icon, price, mrp, rating, count) => ({
    id, name, brand, cat, icon, price, mrp, rating, count,
    off: mrp > price ? Math.round((1 - price / mrp) * 100) : 0,
  });

  const PRODUCTS = [
    P('aura-headphones', 'Aura Wireless Headphones', 'Aura', 'Audio', 'headphones', 2999, 5999, 4.4, 12840),
    P('pulse-earbuds', 'Pulse Earbuds Pro (ANC)', 'Pulse', 'Audio', 'earbuds', 1799, 3499, 4.3, 9210),
    P('lumen-watch', 'Lumen Smartwatch GPS', 'Lumen', 'Wearables', 'watch', 4499, 9999, 4.2, 5320),
    P('monolith-laptop', 'Monolith 14" Laptop Stand', 'Monolith', 'Computers', 'laptop', 1199, 1999, 4.5, 2110),
    P('eclipse-lamp', 'Eclipse LED Floor Lamp', 'Eclipse', 'Lighting', 'lamp', 2290, 2990, 4.1, 1340),
    P('resonance-speaker', 'Resonance Bookshelf Speaker', 'Resonance', 'Audio', 'speaker', 4490, 6990, 4.6, 3870),
    P('lumen-desk-lamp', 'Lumen Desk Lamp (Dimmable)', 'Lumen', 'Lighting', 'lamp', 1390, 1790, 4.3, 980),
    P('moon-ring', 'Moon Light Ring 18"', 'Moon', 'Lighting', 'sparkle', 4990, 8990, 4.0, 760),
    P('vertex-keyboard', 'Vertex Mechanical Keyboard', 'Vertex', 'Computers', 'keyboard', 3499, 5499, 4.7, 6420),
    P('glide-mouse', 'Glide Wireless Mouse', 'Glide', 'Computers', 'mouse', 899, 1499, 4.4, 8830),
    P('nova-monitor', 'Nova 27" 4K Monitor', 'Nova', 'Computers', 'monitor', 21999, 29999, 4.6, 1920),
    P('orbit-phone', 'Orbit 5G Smartphone 128GB', 'Orbit', 'Mobiles', 'phone', 17999, 22999, 4.3, 14560),
    P('aperture-camera', 'Aperture Mirrorless Camera', 'Aperture', 'Cameras', 'camera', 48999, 59999, 4.8, 870),
    P('stride-sneakers', 'Stride Running Sneakers', 'Stride', 'Fashion', 'shoe', 2499, 4999, 4.2, 11200),
    P('weave-jacket', 'Weave All-Weather Jacket', 'Weave', 'Fashion', 'shirt', 3299, 5999, 4.1, 3410),
    P('haven-tote', 'Haven Canvas Tote Bag', 'Haven', 'Fashion', 'bag', 1299, 1999, 4.5, 2240),
    P('brew-grinder', 'Brew Coffee Grinder', 'Brew', 'Kitchen', 'coffee', 2799, 3999, 4.4, 1660),
    P('whisk-blender', 'Whisk Personal Blender', 'Whisk', 'Kitchen', 'blender', 1999, 2999, 4.2, 2980),
    P('pixel-console', 'Pixel Handheld Console', 'Pixel', 'Gaming', 'gamepad', 12999, 15999, 4.7, 5410),
    P('bloom-serum', 'Bloom Vitamin-C Serum', 'Bloom', 'Beauty', 'sparkle', 699, 1199, 4.3, 18900),
  ];

  const byId = {};
  PRODUCTS.forEach((p) => (byId[p.id] = p));

  const inr = (n) => '₹' + n.toLocaleString('en-IN');

  // ── Thumbnail ────────────────────────────────────────────────────────────
  function thumb(p, cls) {
    const tint = TINTS[p.cat] || 'rgba(40,116,240,0.10)';
    return `<div class="thumb ${cls || ''}" style="--tint:${tint}">${svg(p.icon)}</div>`;
  }

  const stars = (r) => `${r.toFixed(1)} <svg viewBox="0 0 24 24" width="9" height="9" fill="currentColor"><path d="M12 2l2.9 6.3 6.9.6-5.2 4.5 1.6 6.7L12 17.2 5.8 20.6l1.6-6.7L2.2 9.4l6.9-.6z"/></svg>`;

  // ── Card variants ──────────────────────────────────────────────────────────
  // Deal card (compact, for the orange Deals rail)
  function dealCard(p) {
    return `<a href="ShopWell Product.html?id=${p.id}" class="group hover-lift flex w-[180px] shrink-0 snap-start flex-col rounded-lg border border-transparent bg-bg-elevated p-3 text-center hover:border-line-subtle">
      <div class="aspect-square overflow-hidden rounded-lg">${thumb(p)}</div>
      <p class="mt-3 truncate text-sm font-medium text-ink-primary">${p.name}</p>
      <div class="mt-1 flex items-center justify-center gap-2">
        <span class="text-base font-bold text-ink-primary">${inr(p.price)}</span>
        ${p.off ? `<span class="text-xs font-semibold text-rating">${p.off}% off</span>` : ''}
      </div>
      <p class="mt-0.5 text-xs text-ink-tertiary line-through">${p.off ? inr(p.mrp) : '&nbsp;'}</p>
    </a>`;
  }

  // Full grid card (PLP / featured) with wishlist + rating + add to cart
  function gridCard(p) {
    return `<div class="group hover-lift relative flex flex-col overflow-hidden rounded-lg border border-line-subtle bg-bg-elevated">
      ${p.off ? `<span class="absolute left-0 top-3 z-10 rounded-r-md bg-rating px-2 py-0.5 text-[11px] font-bold text-white">${p.off}% OFF</span>` : ''}
      <button class="wish absolute right-2.5 top-2.5 z-10 grid size-8 place-items-center rounded-full bg-bg-elevated/90 text-ink-tertiary shadow-sm transition-colors hover:text-cta" aria-label="Save to wishlist" data-id="${p.id}">
        <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/></svg>
      </button>
      <a href="ShopWell Product.html?id=${p.id}" class="block aspect-square overflow-hidden">${thumb(p)}</a>
      <div class="flex flex-1 flex-col p-3">
        <p class="text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">${p.brand}</p>
        <a href="ShopWell Product.html?id=${p.id}" class="mt-0.5 line-clamp-2 min-h-[2.5rem] text-sm text-ink-primary hover:text-accent">${p.name}</a>
        <div class="mt-1.5 flex items-center gap-2">
          <span class="rating-pill">${stars(p.rating)}</span>
          <span class="text-xs text-ink-tertiary">(${p.count.toLocaleString('en-IN')})</span>
        </div>
        <div class="mt-2 flex items-baseline gap-2">
          <span class="text-lg font-bold text-ink-primary">${inr(p.price)}</span>
          ${p.off ? `<span class="text-sm text-ink-tertiary line-through">${inr(p.mrp)}</span><span class="text-sm font-semibold text-rating">${p.off}% off</span>` : ''}
        </div>
        <button class="addcart mt-3 flex h-9 items-center justify-center gap-1.5 rounded-lg bg-cart text-sm font-semibold text-white transition-all hover:bg-cart-hover active:scale-[0.98]" data-id="${p.id}">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/></svg>
          ADD TO CART
        </button>
      </div>
    </div>`;
  }

  // Mini cell for the category-showcase grid
  function miniCell(p) {
    return `<a href="ShopWell Product.html?id=${p.id}" class="group flex flex-col">
      <div class="aspect-square overflow-hidden rounded-lg border border-line-subtle">${thumb(p, 'sm')}</div>
      <p class="mt-2 line-clamp-1 text-sm text-ink-primary group-hover:text-accent">${p.name}</p>
      <p class="text-sm font-semibold text-rating">From ${inr(p.price)}</p>
      <p class="text-xs text-ink-tertiary">${p.brand}</p>
    </a>`;
  }

  // ── Rail nav buttons ─────────────────────────────────────────────────────
  function wireRail(rail) {
    const prev = rail.parentElement.querySelector('[data-rail-prev]');
    const next = rail.parentElement.querySelector('[data-rail-next]');
    const step = () => Math.max(rail.clientWidth * 0.8, 320);
    prev && prev.addEventListener('click', () => rail.scrollBy({ left: -step(), behavior: 'smooth' }));
    next && next.addEventListener('click', () => rail.scrollBy({ left: step(), behavior: 'smooth' }));
  }

  // ── Toast + cart count ───────────────────────────────────────────────────
  let cartCount = 0;
  function bumpCart(label) {
    cartCount += 1;
    document.querySelectorAll('[data-cart-count]').forEach((el) => {
      el.textContent = cartCount;
      el.classList.remove('hidden');
    });
    toast(label || 'Added to cart');
  }
  let toastTimer;
  function toast(msg) {
    let t = document.getElementById('toast');
    if (!t) {
      t = document.createElement('div');
      t.id = 'toast';
      document.body.appendChild(t);
    }
    t.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#388E3C" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg><span>${msg}</span>`;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), 2200);
  }

  // Delegated clicks for add-to-cart / wishlist
  document.addEventListener('click', (e) => {
    const add = e.target.closest('.addcart');
    if (add) { e.preventDefault(); bumpCart('Added to cart'); return; }
    const wish = e.target.closest('.wish');
    if (wish) {
      e.preventDefault();
      const on = wish.classList.toggle('text-cta');
      const path = wish.querySelector('path');
      if (path) path.setAttribute('fill', on ? 'currentColor' : 'none');
      toast(on ? 'Saved to wishlist' : 'Removed from wishlist');
    }
  });

  // ── Hero carousel ──────────────────────────────────────────────────────────
  function wireCarousel(root) {
    const track = root.querySelector('[data-track]');
    const slides = Array.from(track.children);
    const dotsWrap = root.querySelector('[data-dots]');
    let i = 0, timer;
    slides.forEach((_, idx) => {
      const d = document.createElement('button');
      d.className = 'h-1.5 rounded-full transition-all duration-300';
      d.setAttribute('aria-label', 'Go to slide ' + (idx + 1));
      d.addEventListener('click', () => { go(idx); restart(); });
      dotsWrap.appendChild(d);
    });
    const dots = Array.from(dotsWrap.children);
    function go(n) {
      i = (n + slides.length) % slides.length;
      track.style.transform = `translateX(-${i * 100}%)`;
      dots.forEach((d, k) => {
        d.className = 'h-1.5 rounded-full transition-all duration-300 ' + (k === i ? 'w-6 bg-accent' : 'w-1.5 bg-ink-tertiary/40 hover:bg-ink-tertiary/70');
      });
    }
    function restart() { clearInterval(timer); timer = setInterval(() => go(i + 1), 5000); }
    root.querySelector('[data-prev]').addEventListener('click', () => { go(i - 1); restart(); });
    root.querySelector('[data-next]').addEventListener('click', () => { go(i + 1); restart(); });
    root.addEventListener('mouseenter', () => clearInterval(timer));
    root.addEventListener('mouseleave', restart);
    go(0); restart();
  }

  // ── Countdown ──────────────────────────────────────────────────────────────
  function wireCountdown(el) {
    const end = Date.now() + 1000 * 60 * 60 * 8 + 1000 * 53; // ~8h
    function tick() {
      let s = Math.max(0, Math.floor((end - Date.now()) / 1000));
      const h = String(Math.floor(s / 3600)).padStart(2, '0');
      const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
      const sec = String(s % 60).padStart(2, '0');
      el.querySelector('[data-h]').textContent = h;
      el.querySelector('[data-m]').textContent = m;
      el.querySelector('[data-s]').textContent = sec;
    }
    tick();
    setInterval(tick, 1000);
  }

  // ── Render entry points ──────────────────────────────────────────────────
  function fill(sel, html) { const el = document.querySelector(sel); if (el) el.innerHTML = html; }

  window.SW = {
    PRODUCTS, byId, inr, svg, thumb, dealCard, gridCard, miniCell, stars,
    bumpCart, toast,
    pick: (ids) => ids.map((id) => byId[id]).filter(Boolean),
    init() {
      document.querySelectorAll('.rail').forEach(wireRail);
      document.querySelectorAll('[data-carousel]').forEach(wireCarousel);
      document.querySelectorAll('[data-countdown]').forEach(wireCountdown);
    },
    fill,
  };
})();
