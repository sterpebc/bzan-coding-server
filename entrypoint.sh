#!/bin/sh
set -e

# If arguments are provided, run them (allows docker run <image> python app.py)
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# If no arguments are provided, the Dockerfile's CMD will be executed by the shell.
# This script's purpose is to allow overriding the CMD.
