/**
 * Presentation rules for cart / checkout money summaries.
 *
 * Both rules exist because the same cart was being described two different ways
 * on screen, which reads as a pricing error to a customer:
 *
 *  - Counting: the header badge summed quantities while the cart page counted
 *    lines, so a 3-unit cart showed "3" in the header and "Price (1 item)" next
 *    to an amount covering all 3 units.
 *  - Line amounts: the checkout summary printed the tax-inclusive `line_total`
 *    while listing Tax as its own row, so tax appeared twice and the item lines
 *    no longer summed to Subtotal.
 */

/**
 * Total units in the cart, not the number of distinct lines.
 *
 * @param {Array<{quantity?: number|string}>} items
 * @returns {number}
 */
export function cartUnitCount(items) {
  if (!Array.isArray(items)) return 0;
  return items.reduce((sum, i) => {
    const q = Number(i?.quantity ?? 0);
    return sum + (Number.isFinite(q) ? q : 0);
  }, 0);
}

/**
 * The amount to print on an item row when Tax is broken out separately.
 *
 * Prefers the server's pre-tax `line_subtotal`. Falls back to `line_total`
 * (then `unit_price`) so a payload without the field still renders something
 * rather than a blank cell.
 *
 * @param {{line_subtotal?: number|string, line_total?: number|string, unit_price?: number|string}} item
 * @returns {number|string|undefined}
 */
export function lineDisplayAmount(item) {
  if (!item) return undefined;
  return item.line_subtotal ?? item.line_total ?? item.unit_price;
}
