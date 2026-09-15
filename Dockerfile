# syntax=docker/dockerfile:1

# --- Stage 1: build the React SPA (web/dist/) -------------------------------
FROM node:22-bookworm-slim AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Stage 2: the API, serving the built SPA alongside it -------------------
FROM python:3.12-slim-bookworm AS runtime

# ffmpeg: pydub (stitch_stage) shells out to it to concatenate/export mp3s.
# No other system packages needed — everything else is a Python wheel.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# The uv binary itself, straight from astral's distroless image — no pip/curl
# bootstrap needed in this layer.
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, separate from the app source, so an app-only change
# doesn't invalidate the (slow) dependency-install layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
COPY profiles/ ./profiles/
RUN uv sync --frozen --no-dev

# web/dist is served relative to the working directory (see
# podcast/api/app.py:WEB_DIST) — must land at ./web/dist here, matching that.
COPY --from=web-builder /web/dist/ ./web/dist/

# data/ (sqlite DB + episode artefacts, both relative paths — see
# podcast/paths.py, podcast/db.py) is a volume mount point, not baked into
# the image. See docker-compose.yml and docs/decisions.md ("Packaging").
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uv", "run", "podcast", "serve"]
