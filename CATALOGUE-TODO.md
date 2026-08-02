# Catalogue — what still needs filling

Seeded 2026-08-02 from `zx10R/product/*.docx`. Every product is **Rs 0.00 with 0 stock**, so nothing is buyable until you set a price and stock. Edit in Admin -> Products.

## Blocking for launch (all 9 products)

| Field | Why it matters |
|---|---|
| **Price** | NOT NULL in the schema, so it had to be seeded as 0.00. Products show Rs 0.00 today. |
| **Stock** | Seeded 0 on purpose - this is what keeps a Rs 0 product out of a shopper's cart. Set it only after pricing. |
| **Images** | No product photography was in the source docs. Cards render a 'W' placeholder tile. |
| **HSN code** | GST invoices and GSTR filings key on it. The analytics Tax/GST view stays non-compliance-grade until every SKU has one. |
| **How to use / dosage** | Left deliberately empty - see the conflict table below. |


## Dosage conflict - needs your decision

The two source docs disagree. I did not pick one, because publishing a dose that contradicts your pack label is a compliance risk.

| Product | Listings doc | PDP copy doc |
|---|---|---|
| Multivitamin | 2 gummies daily | 1 gummy daily |
| Hair, Skin & Nails | 2 gummies daily after a meal | 1 gummy daily after food |
| Immunity | 2 gummies daily | 1 gummy daily |
| ACV | 2-4 gummies daily | 1 gummy before meals |
| Omega-3 | 2 gummies daily | 1 gummy daily with food |
| Gut Health | 3 gummies daily | 1 gummy after meals |
| Ashwagandha | 4 gummies daily | 1 gummy in the evening |
| PCOS | 4 gummies daily | (no PDP copy) |
| Sleep | 1 gummy before bedtime | (no PDP copy) |

The listings doc gives product-specific doses that track the ingredient amounts; the PDP doc says '1 gummy' for almost everything, which reads like an uncustomised template. Confirm against the physical label.


## Per-product state


### Wellvia Adult Multivitamin Gummies

- SKU `WV-MULTI-01` - id 1 - category **Daily Wellness**
- Present: Tagline, Highlights, Benefits, Results timeline
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, FAQs**

### Wellvia Hair, Skin & Nails Gummies

- SKU `WV-HSN-01` - id 2 - category **Beauty & Glow**
- Present: Tagline, Highlights, Benefits, Results timeline
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, FAQs**

### Wellvia Immunity Gummies

- SKU `WV-IMM-01` - id 3 - category **Immunity Support**
- Present: Tagline, Highlights, Benefits
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, Results timeline, FAQs**

### Wellvia ACV Gummies

- SKU `WV-ACV-01` - id 4 - category **Energy & Vitality**
- Present: Tagline, Highlights, Benefits
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, Results timeline, FAQs**

### Wellvia Vegan Omega-3 Gummies

- SKU `WV-OMEGA-01` - id 5 - category **Daily Wellness**
- Present: Tagline, Highlights, Benefits
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, Results timeline, FAQs**

### Wellvia Gut Health Gummies

- SKU `WV-GUT-01` - id 6 - category **Gut Health**
- Present: Tagline, Highlights, Benefits
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, Results timeline, FAQs**

### Wellvia Ashwagandha Gummies

- SKU `WV-ASHWA-01` - id 7 - category **Daily Wellness**
- Present: Tagline, Highlights, Benefits
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, Results timeline, FAQs**

### Wellvia Women's Wellness PCOS Gummies

- SKU `WV-PCOS-01` - id 8 - category **Daily Wellness**
- Present: -
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, Tagline, Highlights, Benefits, Results timeline, FAQs**

### Wellvia Sleep Gummies

- SKU `WV-SLEEP-01` - id 9 - category **Better Sleep**
- Present: -
- **Missing: Price, MRP / strikethrough, Unit cost, Stock, Shipping weight, HSN code, Flavour, Images, How to use / dosage, Tagline, Highlights, Benefits, Results timeline, FAQs**

## Category assignments I chose

Slugs come from `frontend/src/lib/catalogOptions.js` (the Shop by Goal filter), not invented. A product can only be in one category. These three were judgement calls - change them in Admin -> Products if you disagree:

- **ACV -> Energy & Vitality** (could equally be Daily Wellness)
- **Ashwagandha -> Daily Wellness** (stress/mood; it is explicitly non-drowsy, so I kept it out of Better Sleep)
- **PCOS -> Daily Wellness** (no women's-health goal exists in the six)


## Not created, on purpose

- **Review quotes.** The PDP doc contains five-star quotes ('My hair fall reduced in weeks'). They are marketing copy, not real customers - seeding them as reviews would be fabricated testimonials, which ASCI/CCPA treat as deceptive. Left out entirely.
- **Flavour tags.** The docs mention Strawberry, but your filter facets are Grape, Green Apple, Black Currant, Mixed Berry, Orange, Mixed Fruit. Setting 'Strawberry' would create a value no filter can select. Either add Strawberry to `FLAVOURS` in `catalogOptions.js` or pick from the six.
- **Health-claim review.** Phrases like 'Immunity Boost', 'Bloating Relief' and 'Anti-inflammatory Support' are stronger than the listings doc's 'help support' wording. Worth a compliance read before launch.

