FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# Probe 127.0.0.1, not "localhost": the container's /etc/hosts maps localhost to
# both 127.0.0.1 and ::1, and busybox wget picks ::1 first — which is refused
# unless nginx also listens on IPv6. An explicit IPv4 literal is resolver-proof.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD wget -q -O- http://127.0.0.1/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
