# One image, one process: FastAPI serves the REST API and the built web UI.
FROM node:20-slim AS webui

WORKDIR /web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./

ENV NEXT_EXPORT=1
RUN npm run build


FROM python:3.12-slim

# Firefox/Camoufox runtime dependencies + virtual X display.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libasound2 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    libegl1 \
    libgl1-mesa-dri \
    libgbm1 \
    xvfb \
    x11vnc \
    novnc \
    fonts-liberation \
    fonts-noto-color-emoji \
    fontconfig \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --no-dev --frozen

# The static export, where the package looks for it when no CPM_WEBUI_DIR is set.
COPY --from=webui /web/out ./src/camoufox_pm/webui

ENV CPM_HOST=0.0.0.0 \
    CPM_PORT=8100 \
    CPM_DB_PATH=/data/profiles.db

VOLUME ["/data"]

EXPOSE 8100

# Start a normal-size X11 virtual display, then launch the manager.
#CMD ["sh", "-c", "Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp >/tmp/xvfb.log 2>&1 & exec uv run camoufox-pm --host 0.0.0.0 --port 8100 --no-browser"]
CMD ["uv", "run", "camoufox-pm", "--host", "0.0.0.0", "--port", "8100", "--no-browser"]

