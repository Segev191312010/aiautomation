FROM node:22-alpine AS builder

WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM nginx:alpine
COPY dashboard/nginx.conf /etc/nginx/nginx.conf
COPY --from=builder /build/dist /usr/share/nginx/html
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=2 \
    CMD wget --quiet --tries=1 --spider http://localhost/health || exit 1
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
