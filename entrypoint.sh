#!/bin/sh
set -e

# This script acts as the container's entrypoint, providing flexible startup logic.

# If the first argument is 'gunicorn', 'python', or another command,
# execute it directly. This allows overriding the default behavior, e.g.,
# docker run <image> python create_admin.py <username>
if [ "$1" = "gunicorn" ] || [ "$1" = "python" ] || [ "$1" = "flask" ]; then
    exec "$@"
fi

# Check the MODE environment variable to decide which server to run.
if [ "$MODE" = "dev" ]; then
    # Development mode: Use the Flask development server for hot-reloading.
    echo "Running in development mode (MODE=dev)..."
    exec flask --app app --debug run --host 0.0.0.0 --port 8080
else
    # Production mode (default): Use Gunicorn for a robust production server.
    exec gunicorn --bind 0.0.0.0:8080 "app:application"
fi
