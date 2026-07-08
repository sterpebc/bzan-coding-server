# Use a Debian-based "slim" image for better compatibility with pre-compiled packages
FROM python:3.12-slim AS base

WORKDIR /app

# Copy dependency definition first to leverage Docker layer caching
COPY requirements.txt ./
# Install all Python dependencies in a single, consolidated RUN command
RUN pip install --no-cache-dir "setuptools<81" -r requirements.txt

# Copy application code and helper scripts
COPY . .
RUN chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/app/entrypoint.sh"]

# Development image: convenient defaults for local iteration
FROM base AS dev
ENV MODE=dev

# Production image
FROM base AS prod
ENV MODE=prod
