// Regression: the PDP hover-zoom magnifier painted over the buy rail.
//
// LuxuryGallery's magnifier used to be an Amazon-style side panel positioned
// at `left-[calc(100%+1.25rem)]` with a hardcoded `xl:w-[360px]`/`2xl:w-[460px]`
// width. That offset was sized as if the gallery had the rest of the viewport
// to spread into, but ProductDetailPage lays the page out as
// `grid-cols-[minmax(0,7fr)_minmax(0,5fr)] gap-12` inside a 1200px container:
// the gallery column is ~644px and the buy rail starts ~48px later. So the
// panel had ~68px of real gutter and needed 360-460px, and since no ancestor
// clips overflow it spilled straight over the product title, the Add to cart
// and Buy now buttons, the benefit icons and the pincode checker — made worse
// by `z-40`, which painted it above that content.
//
// The fix renders the magnifier in place, `absolute inset-0` inside the stage,
// so it is mathematically incapable of reaching a sibling column. These tests
// lock that in: they read the component source and assert the magnifier stays
// contained. They fail against the old side-panel implementation.
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(here, '../LuxuryGallery.jsx'), 'utf8');

// The magnifier is the only element that paints the zoomed bitmap, so the
// `backgroundPosition` style is a stable handle on it regardless of how the
// surrounding JSX is refactored.
function magnifierElement() {
  const marker = source.indexOf('backgroundPosition:');
  expect(marker, 'magnifier element (backgroundPosition style) not found').toBeGreaterThan(-1);
  const start = source.lastIndexOf('<div', marker);
  const end = source.indexOf('/>', marker);
  return source.slice(start, end + 2);
}

function classNameOf(element) {
  const m = element.match(/className="([^"]*)"/);
  expect(m, 'magnifier element has no static className').not.toBeNull();
  return m[1];
}

describe('PDP gallery magnifier containment', () => {
  it('does not offset itself outside its positioning parent', () => {
    const cls = classNameOf(magnifierElement());

    // `calc(100% + ...)`, `left-full` and negative right offsets all push an
    // absolutely positioned box past its parent's edge and into the buy rail.
    expect(cls).not.toMatch(/calc\(100%/);
    expect(cls).not.toMatch(/\b(left|right)-full\b/);
    expect(cls).not.toMatch(/-(left|right)-\[/);
  });

  it('is pinned to the stage it magnifies', () => {
    const cls = classNameOf(magnifierElement());

    expect(cls).toContain('absolute');
    expect(cls).toContain('inset-0');
    // Hovering must not become un-hoverable: the overlay sits under the cursor
    // and would otherwise swallow the mousemove that drives it.
    expect(cls).toContain('pointer-events-none');
  });

  it('carries no hardcoded width that could exceed the gallery column', () => {
    const cls = classNameOf(magnifierElement());

    expect(cls).not.toMatch(/\bw-\[\d+px\]/);
    expect(cls).not.toMatch(/:w-\[\d+px\]/);
  });

  it('does not stack above the buy rail', () => {
    const cls = classNameOf(magnifierElement());

    // z-40/z-50 are overlay tiers (drawers, modals). A magnifier that only
    // covers its own stage never needs to outrank page chrome.
    expect(cls).not.toMatch(/\bz-(30|40|50)\b/);
    expect(cls).not.toMatch(/\bz-\[(?:[3-9]\d|\d{3,})\]/);
  });

  it('keeps the stage clipping its own overflow', () => {
    // The stage is the positioning parent for `inset-0`. If it stops clipping,
    // any future absolutely positioned child can escape again.
    expect(source).toMatch(/aspect-square w-full select-none overflow-hidden/);
  });

  it('only arms the zoom for real pointers', () => {
    // A touch tap synthesizes a mousemove with no mouseleave to follow, which
    // would strand the magnifier on-screen over the product image.
    expect(source).toContain('(hover: hover) and (pointer: fine)');
    expect(source).toMatch(/if \(currentIsVideo \|\| !canHover\) return;/);
  });
});
