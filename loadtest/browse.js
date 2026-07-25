import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

// Wellvia storefront browse load test.
// Read-only: catalog list, product detail, bestsellers, categories — the hot
// anonymous paths a shopper hits. No writes, so it is safe to point at any
// stack (here: an isolated local backend on a throwaway DB).
//
// Run:  k6 run -e BASE_URL=http://localhost:8001 browse.js
// Ramps VU count in stages to find the "knee" where p95 latency and errors
// climb, i.e. the practical concurrency ceiling of this configuration.

const BASE = __ENV.BASE_URL || 'http://localhost:8001';
const API = `${BASE}/api/v1`;

const listT = new Trend('t_products_list', true);
const detailT = new Trend('t_product_detail', true);
const bestT = new Trend('t_bestsellers', true);
const catT = new Trend('t_categories', true);
const errRate = new Rate('errors');
const reqs = new Counter('browse_requests');

export const options = {
  scenarios: {
    browse: {
      executor: 'ramping-vus',
      startVUs: 5,
      stages: [
        { duration: '20s', target: 20 },   // warm up
        { duration: '30s', target: 50 },
        { duration: '30s', target: 100 },
        { duration: '30s', target: 150 },
        { duration: '20s', target: 200 },   // push for the knee
        { duration: '15s', target: 0 },     // ramp down
      ],
      gracefulStop: '10s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.02'],           // <2% errors
    'http_req_duration{expected_response:true}': ['p(95)<800', 'p(99)<2000'],
    errors: ['rate<0.02'],
  },
};

// Grab a page of real product ids up front so detail hits are valid.
export function setup() {
  const res = http.get(`${API}/products?page=1&page_size=24`);
  let ids = [];
  try {
    const body = JSON.parse(res.body);
    const items = body.items || body.data || body || [];
    ids = items.map((p) => p.id).filter(Boolean);
  } catch (e) { /* fall through */ }
  return { ids: ids.length ? ids : [1, 2, 3, 4, 5] };
}

function rec(trend, res, name) {
  trend.add(res.timings.duration);
  reqs.add(1);
  const ok = check(res, { [`${name} 200`]: (r) => r.status === 200 });
  errRate.add(!ok);
  return ok;
}

export default function (data) {
  const ids = data.ids;

  group('catalog list', () => {
    const page = 1 + (__ITER % 3);
    rec(listT, http.get(`${API}/products?page=${page}&page_size=12`, { tags: { name: 'products_list' } }), 'list');
  });
  sleep(0.3 + Math.random() * 0.4);

  group('product detail', () => {
    const id = ids[Math.floor(Math.random() * ids.length)];
    rec(detailT, http.get(`${API}/products/${id}`, { tags: { name: 'product_detail' } }), 'detail');
  });
  sleep(0.2 + Math.random() * 0.4);

  // A third of shoppers glance at the bestsellers rail / categories nav.
  if (Math.random() < 0.35) {
    group('bestsellers', () => {
      rec(bestT, http.get(`${API}/products/bestsellers?limit=12`, { tags: { name: 'bestsellers' } }), 'best');
    });
  }
  if (Math.random() < 0.5) {
    group('categories', () => {
      rec(catT, http.get(`${API}/categories`, { tags: { name: 'categories' } }), 'cats');
    });
  }
  sleep(0.5 + Math.random() * 0.8);
}
