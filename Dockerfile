# Use a Debian-based "slim" image for better compatibility with pre-compiled packages
FROM python:3.12-slim AS base

WORKDIR /app

# Install Google Cloud CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg && \
    curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
      | gpg --dearmor -o /usr/share/keyrings/google-cloud-sdk.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/google-cloud-sdk.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
      > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends google-cloud-sdk && \
    rm -rf /var/lib/apt/lists/*

# Copy and install dependencies
COPY requirements.txt ./
COPY vendor/sqlite-web ./vendor/sqlite-web
RUN pip install --no-cache-dir "setuptools<81"
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and helper scripts
COPY . .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/entrypoint.sh"]

# Development image: convenient defaults for local iteration
FROM base AS dev
ENV MODE=dev

# Production image: optimized command for Cloud Run
FROM base AS prod
ENV MODE=prod
EXPOSE 8080

# Default to production gunicorn server if nothing else provided
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:application"]
