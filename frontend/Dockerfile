# ── Flow Studio Frontend ──────────────────────────────────────────────────────
# Stage 1: build
FROM node:22-alpine AS builder

# Built from the repository root (docker-compose.yml): the help panel bundles
# the Markdown in docs/, which must sit next to the frontend, as in the repo.
WORKDIR /app
COPY frontend/package.json ./
RUN npm install

COPY frontend/ .
COPY docs/ /docs/
RUN npm run build
# Output: /app/dist

# Stage 2: serve with nginx
FROM nginx:alpine

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx config — proxies /api and /ws to backend
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
