#!/bin/sh
set -e

# If arguments are provided, run them (allows docker run <image> python app.py)
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if [ "${MODE:-}" = "dev" ]; then
  echo "Starting in dev mode: python app.py"
  exec python app.py
else
  echo "Starting in prod mode: gunicorn --bind 0.0.0.0:8080 app:application"
  exec gunicorn --bind 0.0.0.0:8080 app:application
fi
