/* ShopWell mockup — shared chrome: top header, category nav strip, footer.
   Injects into [data-header], [data-catnav], [data-footer] placeholders. */
(function () {
  const CATS = [
    { label: 'Mobiles', icon: 'phone', items: ['Smartphones', '5G Phones', 'Refurbished', 'Cases & Covers', 'Power Banks'] },
    { label: 'Electronics', icon: 'laptop', items: ['Laptops', 'Monitors', 'Keyboards & Mice', 'Storage', 'Cameras'] },
    { label: 'Audio', icon: 'headphones', items: ['Headphones', 'Earbuds', 'Bluetooth Speakers', 'Soundbars', 'Home Theatre'] },
    { label: 'Wearables', icon: 'watch', items: ['Smartwatches', 'Fitness Bands', 'Smart Rings', 'Watch Straps'] },
    { label: 'Home', icon: 'home', items: ['Lighting', 'Decor', 'Furniture', 'Storage', 'Cleaning'] },
    { label: 'Appliances', icon: 'blender', items: ['Kitchen', 'Air Purifiers', 'Vacuum Cleaners', 'Irons', 'Fans'] },
    { label: 'Fashion', icon: 'shirt', items: ['Footwear', 'Jackets', 'Bags', 'Watches', 'Sunglasses'] },
    { label: 'Beauty', icon: 'sparkle', items: ['Skincare', 'Makeup', 'Fragrance', 'Hair Care', 'Grooming'] },
    { label: 'Gaming', icon: 'gamepad', items: ['Consoles', 'Controllers', 'Headsets', 'Games', 'Accessories'] },
    { label: 'Gift Cards', icon: 'gift', items: ['eGift Cards', 'Birthday', 'Corporate', 'Festive'] },
  ];

  const icon = (n, w) => `<svg viewBox="0 0 24 24" width="${w || 18}" height="${w || 18}" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${({
    cart: '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
    heart: '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    store: '<path d="M3 9 4 4h16l1 5M4 9v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V9M4 9h16"/>',
    bolt: '<path d="M13 2 3 14h8l-1 8 10-12h-8z"/>',
  })[n] || ''}</svg>`;

  function header(active) {
    const link = (href, label) => `<a href="${href}" class="hidden whitespace-nowrap rounded-sm px-2 py-1.5 text-sm font-medium text-white/95 transition-colors hover:text-white lg:inline-block">${label}</a>`;
    return `<header class="sticky top-0 z-50">
      <div class="bg-accent shadow-sm">
        <div class="mx-auto flex h-14 max-w-content items-center gap-2.5 px-3 sm:gap-5 sm:px-6">
          <button type="button" aria-label="Open menu" onclick="SWChrome.openMenu()" class="grid size-9 shrink-0 place-items-center rounded-sm text-white transition-colors hover:bg-white/10 md:hidden">${icon('menu', 22)}</button>
          <a href="ShopWell Home.html" class="flex shrink-0 flex-col leading-none">
            <span class="text-lg font-bold italic tracking-tight text-white sm:text-xl">ShopWell</span>
            <span class="hidden items-center gap-1 text-[11px] italic text-white/85 sm:flex">Explore <span class="font-semibold text-[#FFE11B]">Plus</span>
              <svg viewBox="0 0 24 24" width="9" height="9" fill="#FFE11B"><path d="M12 2l2.9 6.3 6.9.6-5.2 4.5 1.6 6.7L12 17.2 5.8 20.6l1.6-6.7L2.2 9.4l6.9-.6z"/></svg>
            </span>
          </a>
          <form class="relative min-w-0 flex-1 sm:max-w-[560px]" onsubmit="event.preventDefault();window.location.href='ShopWell Products.html'">
            <input type="search" placeholder="Search for products, brands and more" class="h-9 w-full rounded-sm border-0 bg-white pl-3 pr-10 text-sm text-ink-primary shadow-sm outline-none placeholder:text-ink-tertiary" />
            <button type="submit" aria-label="Search" class="absolute right-0 top-0 grid h-9 w-10 place-items-center text-accent">${icon('search', 18)}</button>
          </form>
          <a href="ShopWell Login.html" class="hidden h-8 items-center rounded-sm bg-white px-8 text-sm font-semibold text-accent shadow-sm transition-colors hover:bg-white/90 sm:inline-flex">Login</a>
          ${link('#', 'Become a Seller')}
          <div class="hidden items-center gap-1 text-sm font-medium text-white/95 lg:flex">More ${icon('chevron', 14)}</div>
          <a href="ShopWell Cart.html" class="relative ml-1 flex items-center gap-1.5 rounded-sm px-1.5 py-1.5 text-sm font-medium text-white transition-colors hover:text-white">
            <span class="relative">${icon('cart', 21)}<span data-cart-count class="absolute -right-2 -top-1.5 hidden min-w-[16px] rounded-full bg-cta px-1 text-center text-[10px] font-bold leading-4 text-white">0</span></span>
            <span class="hidden sm:inline">Cart</span>
          </a>
        </div>
      </div>
      ${catnav(active)}
      ${drawer()}
    </header>`;
  }

  function drawer() {
    const nav = [['Home', 'ShopWell Home.html'], ['Shop', 'ShopWell Products.html'], ['Wishlist', 'ShopWell Products.html'], ['Orders', 'ShopWell Products.html'], ['Cart', 'ShopWell Cart.html']];
    return `<div id="sw-drawer" class="fixed inset-0 z-[80] hidden md:!hidden">
      <div onclick="SWChrome.closeMenu()" class="absolute inset-0 bg-black/45"></div>
      <div class="absolute inset-y-0 left-0 flex w-[84%] max-w-xs translate-x-0 flex-col bg-bg-elevated shadow-lg">
        <div class="flex items-center justify-between px-4" style="background:#2874F0;height:56px">
          <span class="text-lg font-bold italic text-white">ShopWell</span>
          <button aria-label="Close menu" onclick="SWChrome.closeMenu()" class="grid size-9 place-items-center rounded-sm text-white hover:bg-white/10">${icon('close', 22)}</button>
        </div>
        <div class="flex-1 overflow-y-auto">
          <a href="ShopWell Login.html" class="flex items-center gap-2.5 border-b border-line-subtle px-4 py-4 text-sm font-semibold text-accent">${icon('user', 18)} Login / Sign up</a>
          <nav class="border-b border-line-subtle py-1">
            ${nav.map(([l, h]) => `<a href="${h}" class="block px-4 py-2.5 text-sm font-medium text-ink-primary transition-colors hover:bg-fill">${l}</a>`).join('')}
          </nav>
          <p class="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-ink-tertiary">Shop by category</p>
          <nav class="pb-4">
            ${CATS.map((c) => `<a href="ShopWell Products.html" class="flex items-center gap-3 px-4 py-2.5 text-sm text-ink-secondary transition-colors hover:bg-fill"><span class="text-accent [&>svg]:size-[18px]">${SW.svg(c.icon)}</span>${c.label}</a>`).join('')}
          </nav>
        </div>
      </div>
    </div>`;
  }

  function catnav(active) {
    const cells = CATS.map((c) => {
      const isOn = active === c.label;
      return `<div class="group relative">
        <a href="ShopWell Products.html" class="flex h-full items-center gap-2 whitespace-nowrap border-b-2 px-1 text-sm font-medium transition-colors ${isOn ? 'border-accent text-accent' : 'border-transparent text-ink-primary hover:text-accent'}">
          <span class="text-ink-secondary group-hover:text-accent [&>svg]:size-[18px]">${SW.svg(c.icon)}</span>
          ${c.label}
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" class="text-ink-tertiary"><path d="m6 9 6 6 6-6"/></svg>
        </a>
        <div class="invisible absolute left-0 top-full z-40 min-w-[200px] translate-y-1 rounded-sm border border-line-subtle bg-bg-elevated py-1.5 opacity-0 shadow-lg transition-all duration-150 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100">
          ${c.items.map((it) => `<a href="ShopWell Products.html" class="block px-4 py-2 text-sm text-ink-secondary transition-colors hover:bg-accent/5 hover:text-accent">${it}</a>`).join('')}
        </div>
      </div>`;
    }).join('');
    const mcells = CATS.map((c) => `<a href="ShopWell Products.html" class="flex w-[58px] shrink-0 flex-col items-center gap-1 text-center">
        <span class="grid size-12 place-items-center rounded-full bg-accent/[0.08] text-accent [&>svg]:size-6">${SW.svg(c.icon)}</span>
        <span class="w-full truncate text-[11px] font-medium leading-tight text-ink-secondary">${c.label}</span>
      </a>`).join('');
    return `<div class="hidden border-b border-line-subtle bg-bg-elevated shadow-sm md:block">
      <nav class="mx-auto flex h-12 max-w-content items-center gap-6 overflow-x-auto px-6 rail">${cells}</nav>
    </div>
    <div class="border-b border-line-subtle bg-bg-elevated shadow-sm md:hidden">
      <nav class="flex gap-4 overflow-x-auto px-3 py-2.5 rail">${mcells}</nav>
    </div>`;
  }

  function footer() {
    const cols = [
      ['ABOUT', ['Contact Us', 'About Us', 'Careers', 'ShopWell Stories', 'Press', 'Corporate Info']],
      ['HELP', ['Payments', 'Shipping', 'Cancellation & Returns', 'FAQ', 'Report Infringement']],
      ['POLICY', ['Return Policy', 'Terms of Use', 'Security', 'Privacy', 'Sitemap', 'EPR Compliance']],
      ['SOCIAL', ['Facebook', 'Twitter', 'YouTube', 'Instagram']],
    ];
    const pay = ['VISA', 'MC', 'AMEX', 'UPI', 'RUPAY', 'NET BANKING', 'COD', 'EMI', 'PHONEPE'];
    return `<footer class="mt-12 bg-[#172337] text-white/70">
      <div class="mx-auto grid max-w-content grid-cols-2 gap-x-8 gap-y-9 px-6 py-12 sm:grid-cols-3 lg:grid-cols-5">
        ${cols.map(([h, items]) => `<div>
          <h4 class="mb-3 text-[11px] font-semibold uppercase tracking-wider text-white/40">${h}</h4>
          <ul class="space-y-2 text-[13px]">${items.map((i) => `<li><a href="#" class="transition-colors hover:text-white">${i}</a></li>`).join('')}</ul>
        </div>`).join('')}
        <div class="col-span-2 sm:col-span-1">
          <h4 class="mb-3 text-[11px] font-semibold uppercase tracking-wider text-white/40">Mail Us</h4>
          <p class="text-[13px] leading-relaxed">ShopWell Internet Pvt. Ltd.,<br/>Buildings Alyssa, Begonia &amp; Clove, Outer Ring Road, Bengaluru, 560103, Karnataka, India</p>
        </div>
      </div>
      <div class="border-t border-white/10">
        <div class="mx-auto flex max-w-content flex-col items-center justify-between gap-4 px-6 py-5 text-[12px] sm:flex-row">
          <div class="flex flex-wrap items-center gap-3">
            <span class="flex items-center gap-1.5 text-[#FFE11B]">${icon('store', 15)} <span class="text-white/80">Sell on ShopWell</span></span>
            <span class="flex items-center gap-1.5 text-[#FFE11B]">${icon('gift', 15)} <span class="text-white/80">Gift Cards</span></span>
            <span class="flex items-center gap-1.5 text-[#FFE11B]">${icon('heart', 15)} <span class="text-white/80">Help Center</span></span>
          </div>
          <div class="flex flex-wrap items-center justify-center gap-1.5">
            ${pay.map((p) => `<span class="rounded-xs border border-white/15 bg-white/5 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-white/60">${p}</span>`).join('')}
          </div>
          <p class="text-white/40">© 2022–2026 ShopWell.com</p>
        </div>
      </div>
    </footer>`;
  }

  window.SWChrome = {
    header, footer, catnav, CATS,
    openMenu() { const d = document.getElementById('sw-drawer'); if (d) { d.classList.remove('hidden'); document.body.style.overflow = 'hidden'; } },
    closeMenu() { const d = document.getElementById('sw-drawer'); if (d) { d.classList.add('hidden'); document.body.style.overflow = ''; } },
  };
})();
