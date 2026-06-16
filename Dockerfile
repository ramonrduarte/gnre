FROM python:3.12-slim

# System dependencies required by Playwright's Chromium (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium runtime libs
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 \
    libglib2.0-0 libdbus-1-3 libx11-6 libxcb1 libxext6 \
    # lxml build deps (slim image doesn't have libxml2)
    libxml2 libxslt1.1 \
    # psycopg2-binary needs libpq at runtime
    libpq5 \
    # Clean up
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser into the image
RUN playwright install chromium

# Copy application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Persistent volume mount point for certs and SQLite (when not using Postgres)
RUN mkdir -p /app/data/certs

EXPOSE 8000

# Run from backend/ so relative imports work
CMD ["python", "backend/main.py"]
