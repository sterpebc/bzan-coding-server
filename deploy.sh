#!/bin/sh
# Deploys/updates the Coding Server app on the dev server.
#
# This does the "update the code and dependencies" part only. How the
# process is actually (re)started depends on your privilege level on the
# dev server -- see DEPLOYMENT.md for the three options (system-level
# systemd, user-level systemd, or a no-sudo cron/nohup fallback) and run
# the matching restart command after this script finishes.
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR"

echo "Pulling latest code..."
git pull

echo "Ensuring virtualenv exists..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

echo "Installing/updating dependencies..."
./.venv/bin/pip install --no-cache-dir --upgrade pip
./.venv/bin/pip install --no-cache-dir "setuptools<81" -r requirements.txt

echo "Done. Now restart the service using whichever method you set up"
echo "(see DEPLOYMENT.md), e.g.:"
echo "  systemctl restart coding-server            # system-level systemd (needs sudo)"
echo "  systemctl --user restart coding-server      # user-level systemd (no sudo)"
echo "  ./scripts/restart_local.sh                  # no-sudo nohup fallback"
