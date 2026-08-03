/**
 * schema.org builders for the storefront.
 *
 * The one rule that matters commercially: an Offer is emitted ONLY when the
 * product carries a real price. The catalogue currently sits at price 0 while
 * pricing is decided, and publishing `"price": "0"` would tell Google the
 * product is free — it renders as ₹0.00 in search results and invites orders
 * the business cannot honour. No offer block is the honest signal for
 * "not yet purchasable", and Google simply omits the price row.
 */

import { absoluteUrl, clampDescription } from './pageMeta.js';

const SITE_NAME = 'Wellvia';

/** Availability follows stock, not intent. */
function availabilityFor(product) {
  return Number(product?.stock) > 0
    ? 'https://schema.org/InStock'
    : 'https://schema.org/OutOfStock';
}

function offersFor(product, canonical, currency = 'INR') {
  const price = Number(product?.price);
  // 0 / NaN / negative all mean "no price has been set yet".
  if (!Number.isFinite(price) || price <= 0) return undefined;
  return {
    '@type': 'Offer',
    url: canonical,
    priceCurrency: currency,
    price: price.toFixed(2),
    availability: availabilityFor(product),
    itemCondition: 'https://schema.org/NewCondition',
    seller: { '@type': 'Organization', name: SITE_NAME },
  };
}

/** Product schema. Optional keys are dropped rather than emitted empty. */
export function buildProductSchema(product, { canonical, image } = {}) {
  if (!product) return null;
  const url = canonical || absoluteUrl(`/products/${product.id}`);
  const description = clampDescription(
    product.short_description || product.description,
    5000,
  );

  const schema = {
    '@context': 'https://schema.org',
    '@type': 'Product',
    name: product.name,
    url,
    brand: { '@type': 'Brand', name: product.brand || SITE_NAME },
  };

  if (description) schema.description = description;
  if (product.sku) schema.sku = product.sku;
  if (image) schema.image = [image];
  if (product.flavour) {
    schema.additionalProperty = [
      { '@type': 'PropertyValue', name: 'Flavour', value: product.flavour },
    ];
  }

  const offers = offersFor(product, url);
  if (offers) schema.offers = offers;

  // Ratings are only emitted when reviews actually exist. An aggregateRating
  // with reviewCount 0 is a structured-data error in Search Console.
  const count = Number(product.rating_count);
  const avg = Number(product.rating_avg);
  if (Number.isFinite(count) && count > 0 && Number.isFinite(avg) && avg > 0) {
    schema.aggregateRating = {
      '@type': 'AggregateRating',
      ratingValue: avg.toFixed(1),
      reviewCount: count,
    };
  }

  return schema;
}

/** BreadcrumbList mirroring the visible breadcrumb trail. */
export function buildBreadcrumbSchema(trail) {
  const items = (trail || []).filter((t) => t && t.name);
  if (items.length === 0) return null;
  return {
    '@context': 'https://schema.org',
    '@type': 'BreadcrumbList',
    itemListElement: items.map((item, i) => ({
      '@type': 'ListItem',
      position: i + 1,
      name: item.name,
      ...(item.path ? { item: absoluteUrl(item.path) } : {}),
    })),
  };
}

/** Organisation + site search, for the home page. */
export function buildOrganizationSchema({ logo } = {}) {
  const origin = absoluteUrl('/');
  return {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    name: SITE_NAME,
    url: origin,
    ...(logo ? { logo: absoluteUrl(logo) } : {}),
  };
}

export function buildWebSiteSchema() {
  const origin = absoluteUrl('/');
  return {
    '@context': 'https://schema.org',
    '@type': 'WebSite',
    name: SITE_NAME,
    url: origin,
    potentialAction: {
      '@type': 'SearchAction',
      target: {
        '@type': 'EntryPoint',
        urlTemplate: `${origin}products?q={search_term_string}`,
      },
      'query-input': 'required name=search_term_string',
    },
  };
}
