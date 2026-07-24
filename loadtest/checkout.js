import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

// Checkout (write path) load test. Each iteration places a prepaid order
// (order build + stock reservation + payment init via the mock gateway).
// A pool of pre-authenticated tokens is created in setup() so per-iteration
// bcrypt login cost doesn't mask the order-creation throughput we're measuring.
//
// SAFE against the throwaway DB only. Run against the isolated stack.
const BASE = __ENV.BASE_URL || 'http://localhost:8001';
const API = `${BASE}/api/v1`;
const POOL = parseInt(__ENV.POOL || '40');

const coT = new Trend('t_checkout', true);
const errRate = new Rate('errors');
const orders = new Counter('orders_created');

export const options = {
  scenarios: {
    checkout: {
      executor: 'ramping-vus',
      startVUs: 5,
      stages: [
        { duration: '20s', target: 20 },
        { duration: '30s', target: 50 },
        { duration: '30s', target: 100 },
        { duration: '20s', target: 150 },
        { duration: '15s', target: 0 },
      ],
      gracefulStop: '10s',
    },
  },
  thresholds: {
    errors: ['rate<0.05'],
    'http_req_duration{name:checkout}': ['p(95)<3000'],
  },
};

export function setup() {
  const pres = http.get(`${API}/products?page=1&page_size=24`);
  let ids = [];
  try {
    const b = JSON.parse(pres.body);
    ids = (b.items || b).map((p) => p.id).filter(Boolean);
  } catch (e) { /* ignore */ }

  const tokens = [];
  for (let i = 0; i < POOL; i++) {
    const email = `lt_ck_${Date.now()}_${i}@gmail.com`;
    const pw = 'Passw0rd!23';
    const h = { headers: { 'Content-Type': 'application/json' } };
    http.post(`${API}/auth/register`, JSON.stringify({ email, password: pw, full_name: 'LT' }), h);
    const lr = http.post(`${API}/auth/login`, JSON.stringify({ email, password: pw }), h);
    try {
      const t = JSON.parse(lr.body).access_token;
      if (t) tokens.push(t);
    } catch (e) { /* ignore */ }
  }
  return { ids: ids.length ? ids : [1], tokens };
}

export default function (data) {
  const token = data.tokens[Math.floor(Math.random() * data.tokens.length)];
  const pid = data.ids[Math.floor(Math.random() * data.ids.length)];
  const res = http.post(
    `${API}/checkout`,
    JSON.stringify({
      items: [{ product_id: pid, quantity: 1 }],
      shipping_address: '12 Test Rd, Bengaluru 560001',
      shipping_pincode: '560001',
      payment_method: 'prepaid',
    }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, tags: { name: 'checkout' } }
  );
  coT.add(res.timings.duration);
  const ok = check(res, { 'checkout 201': (r) => r.status === 201 });
  errRate.add(!ok);
  if (ok) orders.add(1);
  sleep(0.4 + Math.random() * 0.6);
}
