# Base image: official Playwright Python image matching our pinned playwright==1.44.0.
# Includes Chromium, all system dependencies, and a working non-root user (pwuser).
# This avoids manually listing dozens of Chromium system library dependencies.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# Unbuffered output so Cloud Run captures logs in real time
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install Python dependencies before copying source so this layer is cached
# when only source files change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Register Chromium at the path expected by our pip-installed playwright version.
# The base image already has Chromium binaries; this step ensures the browser
# path is recorded for the exact package version we installed above.
RUN playwright install chromium

# Copy application source. .dockerignore excludes secrets, venv, data, and
# dev artifacts — see .dockerignore.
COPY . .

EXPOSE 8080

# PORT is injected by Cloud Run at runtime (defaults to 8080 locally).
CMD ["sh", "-c", "uvicorn src.api.server:app --host 0.0.0.0 --port ${PORT:-8080}"]
