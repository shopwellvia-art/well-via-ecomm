FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build


FROM nginx:1.27-alpine AS runtime

# Build provenance (see backend/Dockerfile for the rationale). Baked into a
# static /version.json that nginx serves, so `curl http://<host>:8090/version.json`
# reveals exactly which commit's frontend is live — no shell access needed.
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

RUN printf '{"service":"frontend","revision":"%s","built":"%s"}\n' \
        "$GIT_SHA" "$BUILD_TIME" > /usr/share/nginx/html/version.json

LABEL org.opencontainers.image.title="simple-com-frontend" \
      org.opencontainers.image.source="https://github.com/shopwellvia-art/well-via-ecomm" \
      org.opencontainers.image.revision=$GIT_SHA \
      org.opencontainers.image.created=$BUILD_TIME

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q -O- http://127.0.0.1/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
