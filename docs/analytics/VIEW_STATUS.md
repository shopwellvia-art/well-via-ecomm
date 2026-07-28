<!-- GENERATED FILE — DO NOT EDIT BY HAND -->

# Analytics — view status matrix

> **⚠️ THIS FILE IS GENERATED. Do not edit it by hand.**
> It is produced from `backend/app/services/analytics/registry.py` by `backend/scripts/gen_view_status.py`.
> Any manual edit is lost on the next regeneration — change the registry instead.

> **`VIEW_STATUS.md` is the contract referred to throughout the analytics
> implementation plan.** Every one of the 73 views appears below exactly once,
> with an honest state. A view that cannot show real data says so here and in
> the UI; it is never quietly rendered as an empty chart or a false zero.

Regenerate with:

```bash
python backend/scripts/gen_view_status.py
```

## Summary by state

| State | Views | Share | Meaning |
| --- | ---: | ---: | --- |
| **LIVE** | 34 | 47% | Real data, documented formula, source + freshness shown. |
| **PARTIAL** | 17 | 23% | Real data with a stated limitation. |
| **INTEGRATION_REQUIRED** | 15 | 21% | Needs an external source that is not connected. |
| **FEATURE_REQUIRED** | 6 | 8% | Needs a business capability this system does not have. |
| **BLOCKED_BY_MISSING_SOURCE** | 0 | 0% | Source exists in principle but is unusable here. |
| **NOT_APPLICABLE** | 1 | 1% | The concept does not apply to this deployment. |
| **Total** | **73** | 100% | |

## Modules

| # | Module | Slug | Views | Permission | Default view |
| ---: | --- | --- | ---: | --- | --- |
| 1 | Executive & Business Health | `executive` | 6 | `analytics.executive.view` | `executive-overview` |
| 2 | Sales, Revenue & Finance | `sales-finance` | 8 | `analytics.sales.view` | `revenue-and-profitability` |
| 3 | Products & Merchandising | `products` | 6 | `analytics.products.view` | `product-performance` |
| 4 | Customers & Retention | `customers` | 9 | `analytics.customers.view` | `customer-analytics` |
| 5 | Marketing & Attribution | `marketing` | 8 | `analytics.marketing.view` | `marketing-channel-performance` |
| 6 | Website & Conversion | `website` | 8 | `analytics.website.view` | `conversion-funnel` |
| 7 | Inventory & Supply Chain | `inventory` | 7 | `analytics.inventory.view` | `inventory-analytics` |
| 8 | Orders & Logistics | `orders` | 5 | `analytics.orders.view` | `shipping-and-delivery` |
| 9 | Payments & Risk | `payments` | 5 | `analytics.payments.view` | `payment-analytics` |
| 10 | Marketplace, Stores & B2B | `marketplace` | 3 | `analytics.marketplace.view` | `marketplace-performance` |
| 11 | Customer Experience & UX | `customer-experience` | 4 | `analytics.cx.view` | `customer-support-and-complaint` |
| 12 | Analytics Control Centre | `control-centre` | 4 | `analytics.control_centre.view` | `analytics-tracking-health` |

## Column conventions

- **#** — the stable 1..73 view number from the product brief. It never changes,
  even if a view is renamed or moves module. This is what every discussion,
  ticket and test refers to.
- **Route** — `/admin/analytics/{module_slug}/{view_slug}`, resolved by one
  metadata-driven page component, not 73 hand-written pages.
- **Sources** — where the numbers come from. Any view carrying financial
  figures must include `internal_db`; GA4 is never the accounting value.
- **State** — the *ceiling* declared by the registry. A runtime probe may
  downgrade it (LIVE → PARTIAL when a rollup has no rows yet) but never
  upgrade it.
- **Requires** — the capabilities a gated or partial view is waiting on. This is
  what the gated UI shows the admin instead of an unexplained empty state.
- **Backend / Frontend / Tests** — work tracking, updated as work lands. `TODO`
  means the resolver / UI wiring / test still has to be written.
  `—` means there is nothing to do: the view is gated
  (INTEGRATION_REQUIRED, FEATURE_REQUIRED, BLOCKED_BY_MISSING_SOURCE, NOT_APPLICABLE), so it needs
  no resolver work — its shell is rendered from the registry alone. These three
  columns are re-seeded on every regeneration; durable status belongs in the
  registry's `state` field, not here.
- **Limitation** — the one line shown to the admin on a gated or partial view.
  Every non-LIVE view must have one; the registry test enforces it.

## All views

| # | Module | View | Route | Sources | Permission | State | Requires | Backend | Frontend | Tests | Limitation |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Executive & Business Health | Executive Overview | `/admin/analytics/executive/executive-overview` | internal_db | `analytics.executive.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 2 | Executive & Business Health | Real-Time Sales | `/admin/analytics/executive/real-time-sales` | internal_db | `analytics.executive.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 3 | Sales, Revenue & Finance | Revenue and Profitability | `/admin/analytics/sales-finance/revenue-and-profitability` | internal_db | `analytics.sales.view` | **PARTIAL** | cost_rules, order_line_fact | TODO | TODO | TODO | Margin steps only cover orders whose products have a cost rule; uncovered lines are reported separately and never zero-filled. |
| 4 | Sales, Revenue & Finance | Orders and Average Order Value | `/admin/analytics/sales-finance/orders-and-average-order-value` | internal_db | `analytics.sales.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 5 | Products & Merchandising | Product Performance | `/admin/analytics/products/product-performance` | internal_db | `analytics.products.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 6 | Products & Merchandising | Category Performance | `/admin/analytics/products/category-performance` | internal_db | `analytics.products.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 7 | Products & Merchandising | SKU-Level Analytics | `/admin/analytics/products/sku-level-analytics` | internal_db | `analytics.products.view` | **PARTIAL** | product_variants | TODO | TODO | TODO | There is no variant model, so every product has exactly one SKU; size, colour and pack-size splits are not possible until variants exist. |
| 8 | Customers & Retention | Customer Analytics | `/admin/analytics/customers/customer-analytics` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 9 | Customers & Retention | New vs Returning Customers | `/admin/analytics/customers/new-vs-returning-customers` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 10 | Customers & Retention | Customer Lifetime Value | `/admin/analytics/customers/customer-lifetime-value` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 11 | Customers & Retention | Customer Segmentation | `/admin/analytics/customers/customer-segmentation` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 12 | Customers & Retention | Cohort and Retention | `/admin/analytics/customers/cohort-and-retention` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 13 | Customers & Retention | Customer Churn | `/admin/analytics/customers/customer-churn` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 14 | Website & Conversion | Conversion Funnel | `/admin/analytics/website/conversion-funnel` | internal_db | `analytics.website.view` | **PARTIAL** | ga4_data_api | TODO | TODO | TODO | The funnel starts at add-to-cart because sessions and product views are not recorded internally; the session-level steps need GA4. |
| 15 | Website & Conversion | Cart Abandonment | `/admin/analytics/website/cart-abandonment` | internal_db | `analytics.website.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 16 | Website & Conversion | Checkout Performance | `/admin/analytics/website/checkout-performance` | internal_db | `analytics.website.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 17 | Website & Conversion | Website Traffic | `/admin/analytics/website/website-traffic` | ga4 | `analytics.website.view` | **INTEGRATION_REQUIRED** | ga4_data_api, ga4_measurement | — | — | — | Sessions and pageviews are not recorded server-side; this view needs GA4 measurement plus the GA4 Data API. |
| 18 | Website & Conversion | Landing Page Performance | `/admin/analytics/website/landing-page-performance` | ga4 | `analytics.website.view` | **INTEGRATION_REQUIRED** | ga4_data_api | — | — | — | Landing pages are identified from session entry data, which only GA4 has; the store logs API requests, not page entries. |
| 19 | Marketing & Attribution | Marketing Channel Performance | `/admin/analytics/marketing/marketing-channel-performance` | internal_db, ga4 | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | ga4_data_api | — | — | — | The store does not persist session-level traffic sources; channel attribution needs the GA4 Data API to be connected. |
| 20 | Marketing & Attribution | Campaign Performance | `/admin/analytics/marketing/campaign-performance` | internal_db, ga4, ad_platforms | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | ga4_data_api, ad_platform | — | — | — | Campaign results need GA4 for click-through data and an ad platform for spend; neither is connected. |
| 21 | Marketing & Attribution | ROAS and Marketing Profitability | `/admin/analytics/marketing/roas-and-marketing-profitability` | internal_db, ad_platforms | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | ad_platform, cost_rules | — | — | — | ROAS needs ad spend from the ad platforms, and profitability additionally needs product cost rules; neither is available. |
| 22 | Marketing & Attribution | SEO Performance | `/admin/analytics/marketing/seo-performance` | google_search_console | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | search_console | — | — | — | Query, impression and position data only exists in Google Search Console, which is not connected. |
| 23 | Marketing & Attribution | Social Media Commerce | `/admin/analytics/marketing/social-media-commerce` | internal_db, ga4 | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | social_commerce_api, ga4_data_api | — | — | — | Post-level and social-shop data comes from the social commerce APIs, and the referring traffic from GA4; none of them is connected. |
| 24 | Marketing & Attribution | Email and SMS Marketing | `/admin/analytics/marketing/email-and-sms-marketing` | internal_db | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | email_sms_platform | — | — | — | Transactional mail is sent by the app, but campaign sends, opens and clicks live in an email/SMS platform that is not connected. |
| 25 | Sales, Revenue & Finance | Coupon and Discount Performance | `/admin/analytics/sales-finance/coupon-and-discount-performance` | internal_db | `analytics.sales.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 26 | Marketing & Attribution | Affiliate and Influencer | `/admin/analytics/marketing/affiliate-and-influencer` | internal_db | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | affiliate_platform | — | — | — | Partner links and commissions live in an affiliate platform that is not connected; the internal referrals table is customer-to-customer only. |
| 27 | Inventory & Supply Chain | Inventory Analytics | `/admin/analytics/inventory/inventory-analytics` | internal_db | `analytics.inventory.view` | **PARTIAL** | inventory_ledger | TODO | TODO | TODO | Stock movement history begins when the inventory ledger was switched on; earlier periods cannot be reconstructed and are shown as no data. |
| 28 | Inventory & Supply Chain | Stock Availability | `/admin/analytics/inventory/stock-availability` | internal_db | `analytics.inventory.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 29 | Inventory & Supply Chain | Low-Stock and Out-of-Stock | `/admin/analytics/inventory/low-stock-and-out-of-stock` | internal_db | `analytics.inventory.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 30 | Inventory & Supply Chain | Inventory Turnover | `/admin/analytics/inventory/inventory-turnover` | internal_db | `analytics.inventory.view` | **PARTIAL** | inventory_ledger, cost_rules | TODO | TODO | TODO | Turnover needs average stock across the period (ledger history starts now) and product costs to value COGS; both are incomplete. |
| 31 | Inventory & Supply Chain | Demand Forecasting | `/admin/analytics/inventory/demand-forecasting` | internal_db | `analytics.inventory.view` | **PARTIAL** | inventory_ledger, order_line_fact | TODO | TODO | TODO | Forecasts need several months of demand and stock-out history; with the history available now the suggestions are indicative only. |
| 32 | Inventory & Supply Chain | Supplier Performance | `/admin/analytics/inventory/supplier-performance` | internal_db | `analytics.inventory.view` | **FEATURE_REQUIRED** | suppliers | — | — | — | There is no supplier or purchase-order model in the system, so lead times and fill rates do not exist to be measured. |
| 33 | Inventory & Supply Chain | Warehouse Performance | `/admin/analytics/inventory/warehouse-performance` | internal_db | `analytics.inventory.view` | **FEATURE_REQUIRED** | warehouses | — | — | — | Stock is not tracked per warehouse, bin or location, so pick and pack throughput cannot be attributed anywhere. |
| 34 | Orders & Logistics | Shipping and Delivery | `/admin/analytics/orders/shipping-and-delivery` | internal_db | `analytics.orders.view` | **PARTIAL** | courier_scan_api | TODO | TODO | TODO | Only the status changes recorded on our own shipments are available; leg-by-leg transit timings need the courier scan API. |
| 35 | Orders & Logistics | Courier Performance | `/admin/analytics/orders/courier-performance` | internal_db | `analytics.orders.view` | **PARTIAL** | courier_scan_api | TODO | TODO | TODO | Comparison uses our own dispatch and delivery timestamps; failed-attempt and exception reasons need the courier scan API. |
| 36 | Orders & Logistics | Order Fulfilment | `/admin/analytics/orders/order-fulfilment` | internal_db | `analytics.orders.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 37 | Orders & Logistics | Returns and Refunds | `/admin/analytics/orders/returns-and-refunds` | internal_db | `analytics.orders.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 38 | Orders & Logistics | Cancellation Analytics | `/admin/analytics/orders/cancellation-analytics` | internal_db | `analytics.orders.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 39 | Payments & Risk | Payment Analytics | `/admin/analytics/payments/payment-analytics` | internal_db | `analytics.payments.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 40 | Payments & Risk | Payment Failure | `/admin/analytics/payments/payment-failure` | internal_db | `analytics.payments.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 41 | Payments & Risk | COD Performance | `/admin/analytics/payments/cod-performance` | internal_db | `analytics.payments.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 42 | Payments & Risk | Fraud and Risk Analytics | `/admin/analytics/payments/fraud-and-risk-analytics` | internal_db | `analytics.payments.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 43 | Website & Conversion | Geographic Sales | `/admin/analytics/website/geographic-sales` | internal_db | `analytics.website.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 44 | Website & Conversion | Device and Browser Analytics | `/admin/analytics/website/device-and-browser-analytics` | ga4 | `analytics.website.view` | **INTEGRATION_REQUIRED** | ga4_data_api | — | — | — | Client device, browser and OS are not stored with orders; these splits require GA4. |
| 45 | Website & Conversion | Search Analytics | `/admin/analytics/website/search-analytics` | ga4 | `analytics.website.view` | **INTEGRATION_REQUIRED** | ga4_data_api | — | — | — | On-site search terms are not persisted by the storefront; reporting them needs GA4 with site search configured. |
| 46 | Products & Merchandising | Product Recommendation Performance | `/admin/analytics/products/product-recommendation-performance` | internal_db | `analytics.products.view` | **FEATURE_REQUIRED** | recommendation_engine | — | — | — | No recommendation engine is running, so there are no recommendation impressions, clicks or attributed orders to measure. |
| 47 | Sales, Revenue & Finance | Pricing and Margin | `/admin/analytics/sales-finance/pricing-and-margin` | internal_db | `analytics.finance.view` | **PARTIAL** | cost_rules | TODO | TODO | TODO | Products without a cost rule are listed as uncovered and excluded from margin; enter costs under analytics cost rules to complete the picture. |
| 48 | Marketplace, Stores & B2B | Marketplace Performance | `/admin/analytics/marketplace/marketplace-performance` | internal_db | `analytics.marketplace.view` | **FEATURE_REQUIRED** | marketplace_channel | — | — | — | No marketplace channel is connected, so there are no marketplace orders, listings or commission fees to report. |
| 49 | Marketplace, Stores & B2B | Store or Branch Performance | `/admin/analytics/marketplace/store-or-branch-performance` | internal_db | `analytics.marketplace.view` | **NOT_APPLICABLE** | multi_store | — | — | — | This is a single-store deployment — there are no branches or stores to compare, and this view will stay unavailable until that changes. |
| 50 | Customer Experience & UX | Customer Support and Complaint | `/admin/analytics/customer-experience/customer-support-and-complaint` | internal_db | `analytics.cx.view` | **PARTIAL** | support_ticketing | TODO | TODO | TODO | Only inbound contact messages are stored — without ticketing there is no assignment, SLA clock, first-response or resolution time to report. |
| 51 | Customer Experience & UX | Reviews and Ratings | `/admin/analytics/customer-experience/reviews-and-ratings` | internal_db | `analytics.cx.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 52 | Customers & Retention | Loyalty and Rewards | `/admin/analytics/customers/loyalty-and-rewards` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 53 | Customers & Retention | Subscription Commerce | `/admin/analytics/customers/subscription-commerce` | internal_db | `analytics.customers.view` | **FEATURE_REQUIRED** | subscriptions | — | — | — | The store sells one-off orders only — there is no subscription, plan or renewal model, so there is nothing to measure. |
| 54 | Marketplace, Stores & B2B | B2B Customer Analytics | `/admin/analytics/marketplace/b2b-customer-analytics` | internal_db | `analytics.marketplace.view` | **FEATURE_REQUIRED** | b2b_accounts | — | — | — | There is no B2B account, price list or credit model, so business buyers cannot be separated from retail ones. |
| 55 | Executive & Business Health | Sales Forecasting | `/admin/analytics/executive/sales-forecasting` | internal_db | `analytics.executive.view` | **PARTIAL** | order_line_fact | TODO | TODO | TODO | Only a few weeks of line-level order history exist, so the projection is a trend extrapolation with no seasonal model; accuracy improves as history accumulates. |
| 56 | Executive & Business Health | Seasonal and Festival Sales | `/admin/analytics/executive/seasonal-and-festival-sales` | internal_db | `analytics.executive.view` | **PARTIAL** | order_line_fact | TODO | TODO | TODO | Year-on-year comparison needs a full prior year of orders, which does not exist yet; until then only peaks within the selected range are shown. |
| 57 | Products & Merchandising | Product Bundling and Cross-Sell | `/admin/analytics/products/product-bundling-and-cross-sell` | internal_db | `analytics.products.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 58 | Products & Merchandising | Upsell Performance | `/admin/analytics/products/upsell-performance` | internal_db | `analytics.products.view` | **PARTIAL** | recommendation_engine | TODO | TODO | TODO | No upsell placement is instrumented, so uplift is inferred from basket composition and cannot be attributed to a specific offer. |
| 59 | Customers & Retention | RFM Customer Analysis | `/admin/analytics/customers/rfm-customer-analysis` | internal_db | `analytics.customers.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 60 | Executive & Business Health | E-commerce Business Health | `/admin/analytics/executive/ecommerce-business-health` | internal_db | `analytics.executive.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 61 | Customer Experience & UX | Behaviour and UX Insights | `/admin/analytics/customer-experience/behaviour-and-ux-insights` | microsoft_clarity, ga4 | `analytics.cx.view` | **INTEGRATION_REQUIRED** | clarity_project, ga4_data_api | — | — | — | Scroll, click and replay behaviour comes from Microsoft Clarity and GA4; neither is connected, and the store records no page-level interaction. |
| 62 | Analytics Control Centre | Analytics Tracking Health | `/admin/analytics/control-centre/analytics-tracking-health` | internal_db | `analytics.control_centre.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 63 | Analytics Control Centre | Data Reconciliation | `/admin/analytics/control-centre/data-reconciliation` | internal_db | `analytics.control_centre.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 64 | Payments & Risk | Settlements and Payouts | `/admin/analytics/payments/settlements-and-payouts` | internal_db, payment_gateway | `analytics.finance.view` | **INTEGRATION_REQUIRED** | gateway_settlement_api | — | — | — | Settlement batches, fees and payout dates must be read from the gateway settlement API; captured payment records alone cannot show what was paid out. |
| 65 | Marketing & Attribution | Customer Journey and Attribution | `/admin/analytics/marketing/customer-journey-and-attribution` | internal_db, ga4 | `analytics.marketing.view` | **INTEGRATION_REQUIRED** | ga4_data_api | — | — | — | Cross-session paths need GA4; internally only cart and order events exist, so a journey could not start earlier than add-to-cart. |
| 66 | Analytics Control Centre | Experiment and A/B Testing | `/admin/analytics/control-centre/experiment-and-ab-testing` | internal_db | `analytics.control_centre.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 67 | Sales, Revenue & Finance | Contribution Margin | `/admin/analytics/sales-finance/contribution-margin` | internal_db | `analytics.finance.view` | **PARTIAL** | cost_rules, order_line_fact | TODO | TODO | TODO | CM covers only orders whose products have a cost rule, and shipping and gateway fees are allocated by rule rather than observed per order. |
| 68 | Sales, Revenue & Finance | Unit Economics | `/admin/analytics/sales-finance/unit-economics` | internal_db | `analytics.finance.view` | **PARTIAL** | cost_rules, ad_platform | TODO | TODO | TODO | Acquisition cost is excluded until an ad platform is connected, so this is contribution economics only; it also depends on product cost coverage. |
| 69 | Sales, Revenue & Finance | Cash Flow and Working Capital | `/admin/analytics/sales-finance/cash-flow-and-working-capital` | internal_db | `analytics.finance.view` | **INTEGRATION_REQUIRED** | bank_cash_feed | — | — | — | Needs a bank or cash feed. Captured payments and gateway settlements are not cash and will not be used as a substitute. |
| 70 | Sales, Revenue & Finance | Tax and GST | `/admin/analytics/sales-finance/tax-and-gst` | internal_db | `analytics.finance.view` | **PARTIAL** | hsn_tax_detail | TODO | TODO | TODO | Operational reporting only — HSN-level detail is not stored, so these figures are NOT compliance-grade and must not be used for GST filing. |
| 71 | Customer Experience & UX | Website Speed and Technical Performance | `/admin/analytics/customer-experience/website-speed-and-technical-performance` | internal_db | `analytics.cx.view` | **PARTIAL** | ga4_data_api | TODO | TODO | TODO | Server-side latency and errors are measured from the internal request log; browser Core Web Vitals need a front-end RUM source such as GA4. |
| 72 | Executive & Business Health | Budget vs Actual | `/admin/analytics/executive/budget-vs-actual` | internal_db | `analytics.executive.view` | **LIVE** | — | TODO | TODO | TODO | — |
| 73 | Analytics Control Centre | Alerts and Anomaly | `/admin/analytics/control-centre/alerts-and-anomaly` | internal_db | `analytics.control_centre.view` | **LIVE** | — | TODO | TODO | TODO | — |
