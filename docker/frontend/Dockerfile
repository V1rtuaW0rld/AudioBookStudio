# ============================================================
# Frontend AudioBookStudio (React/Vite) — build puis nginx
# ============================================================

# === Stage 1 : build du bundle Vite ===
FROM oven/bun:1 AS build

WORKDIR /build

COPY webapp/frontend/package.json webapp/frontend/bun.lock ./
RUN bun install --frozen-lockfile

COPY webapp/frontend/ ./
RUN bun run build


# === Stage 2 : service statique via nginx ===
FROM nginx:alpine

COPY docker/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 5180

CMD ["nginx", "-g", "daemon off;"]
