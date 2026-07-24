import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

// Stress: drive a rising request RATE (open model) to find the throughput knee
// — the point where p95 latency explodes and errors appear because workers /
// DB pool / CPU saturate. No think time; each iteration is one browse request.
const BASE = __ENV.BASE_URL || 'http://localhost:8001';
const API = `${BASE}/api/v1`;
const errRate = new Rate('errors');

export const options = {
  scenarios: {
    stress: {
      executor: 'ramping-arrival-rate',
      startRate: 50,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 800,
      stages: [
        { duration: '20s', target: 100 },
        { duration: '20s', target: 200 },
        { duration: '20s', target: 350 },
        { duration: '20s', target: 500 },
        { duration: '20s', target: 700 },
      ],
    },
  },
  thresholds: { errors: ['rate<0.05'] },
};

const paths = [
  () => `${API}/products?page=1&page_size=12`,
  () => `${API}/products?page=2&page_size=12`,
  () => `${API}/categories`,
  () => `${API}/products/bestsellers?limit=12`,
];

export default function () {
  const url = paths[Math.floor(Math.random() * paths.length)]();
  const res = http.get(url);
  errRate.add(!check(res, { '200': (r) => r.status === 200 }));
}
