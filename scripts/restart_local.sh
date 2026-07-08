#!/bin/sh
# No-sudo fallback for (re)starting the Coding Server as a background
# process using nohup, with a pidfile so this script can safely stop and
# restart it. Meant to be paired with a user crontab @reboot entry so it
# also comes back up automatically after the dev server reboots -- see
# DEPLOYMENT.md, option 3.
set -e

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR"

PIDFILE="$APP_DIR/.gunicorn.pid"
LOGFILE="$APP_DIR/gunicorn.log"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "Stopping existing process ($(cat "$PIDFILE"))..."
    kill "$(cat "$PIDFILE")"
    sleep 2
fi

if [ -f ".env" ]; then
    set -a
    . ./.env
    set +a
fi

echo "Starting gunicorn..."
# --access-logfile/--error-logfile are both required: gunicorn's access log
# (one line per request) is OFF by default, unlike Flask's dev server which
# logs every request automatically. '-' means "write to stdout/stderr",
# which the nohup redirect below then captures into LOGFILE.
#
# Bound to 127.0.0.1 (loopback only), not 0.0.0.0: nginx is now the only
# thing that talks to this directly (see deploy/nginx-default.conf), so
# there's no need for gunicorn to accept connections from the network at
# all -- and no firewall rule is needed for this port as a result.
nohup ./.venv/bin/gunicorn --bind 127.0.0.1:8080 \
    --access-logfile - --error-logfile - \
    "app:application" \
    >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"
disown

echo "Started with PID $(cat "$PIDFILE"). Logs: $LOGFILE"
