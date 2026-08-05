import { describe, it, expect } from 'vitest';
import { focusedCardIndex } from '@/pages/HomePage.jsx';

/**
 * The bestsellers rail is `snap-x snap-mandatory` with `snap-start` cards, so a
 * scroll always settles with one card on the snap line. `focusedCardIndex`
 * decides which one that is — the product the shopper just scrolled to, which
 * the rail then lifts forward.
 *
 * Coordinates below are viewport x of each card's left edge. A 280px card with a
 * 24px gap steps by 304px, which is the desktop geometry.
 */
const CARDS = [100, 404, 708, 1012, 1316];

describe('focusedCardIndex', () => {
  it('focuses the first card when the rail is parked at the start', () => {
    expect(focusedCardIndex(CARDS, 100)).toBe(0);
  });

  it('follows the scroll: each step of one card advances the focus by one', () => {
    // Scrolling right moves cards left, so the anchor stays put and the lefts
    // shift — simulated here by moving the anchor instead, which is equivalent.
    expect(focusedCardIndex(CARDS, 404)).toBe(1);
    expect(focusedCardIndex(CARDS, 708)).toBe(2);
    expect(focusedCardIndex(CARDS, 1012)).toBe(3);
    expect(focusedCardIndex(CARDS, 1316)).toBe(4);
  });

  it('snaps focus to the nearer card mid-scroll rather than waiting', () => {
    expect(focusedCardIndex(CARDS, 480)).toBe(1); // 76px past card 1
    expect(focusedCardIndex(CARDS, 640)).toBe(2); // 68px short of card 2
  });

  // An exact midpoint must resolve deterministically, or the highlight would
  // flicker between two cards while a slow scroll crosses the boundary.
  it('gives an exact tie to the earlier card', () => {
    expect(focusedCardIndex(CARDS, 252)).toBe(0); // dead centre of 100 and 404
  });

  it('clamps to the ends instead of going out of range', () => {
    expect(focusedCardIndex(CARDS, -5000)).toBe(0);
    expect(focusedCardIndex(CARDS, 99999)).toBe(4);
  });

  // The anchor is the rail's left edge PLUS its scroll padding (px-5 → 20px on
  // mobile). Measuring from the raw edge would report the previous card as
  // focused for the width of that padding.
  it('respects a padded snap line', () => {
    const mobile = [20, 240, 460];
    expect(focusedCardIndex(mobile, 20)).toBe(0);
    expect(focusedCardIndex(mobile, 240)).toBe(1);
  });

  it('is safe on an empty rail', () => {
    expect(focusedCardIndex([], 0)).toBe(0);
  });

  it('focuses the only card when there is one', () => {
    expect(focusedCardIndex([100], 5000)).toBe(0);
  });
});
