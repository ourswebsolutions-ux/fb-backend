# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# System deps needed to compile some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --prefix=/install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Playwright needs these system libraries to run Chromium headlessly
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime deps
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libgtk-3-0 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    fonts-liberation \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY . .

# NOTE:
# Playwright browser installation downloads Chromium during image build.
# In some environments this can fail due to network restrictions/timeouts.
# We therefore do NOT install browsers at build time.
# Playwright stores browsers under the user's home; let appuser access them
ENV PLAYWRIGHT_BROWSERS_PATH=/home/appuser/.cache/ms-playwright

# Create a non-root user for security
RUN useradd -m -u 1000 appuser \
    && chown -R appuser:appuser /app \
    && chown -R appuser:appuser /root/.cache 2>/dev/null || true

# Playwright stores browsers under the user's home; let appuser access them
RUN mkdir -p /home/appuser/.cache && chown -R appuser:appuser /home/appuser/.cache




USER appuser

# IMPORTANT: do not block server startup on Playwright downloads.
# Start uvicorn immediately. Run Playwright install as a detached best-effort task.
CMD ["sh", "-c", "set -e; \
    BROWSERS_DIR=\"$PLAYWRIGHT_BROWSERS_PATH\"; \
    ( \
    if [ ! -x \"$BROWSERS_DIR/chromium-*/chrome-linux64/chrome\" ]; then \
    i=1; \
    while [ $i -le 3 ]; do \
    echo \"Playwright: installing Chromium (attempt $i/3)...\"; \
    if playwright install chromium; then exit 0; fi; \
    i=$((i+1)); \
    sleep 10; \
    done; \
    fi; \
    ) >/tmp/playwright-install.log 2>&1 & \
    exec uvicorn main:app --host 0.0.0.0 --port 8000"]
