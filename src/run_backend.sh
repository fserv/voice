#!/usr/bin/env bash
# Start the travel-agent backend using the local venv and .env.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

[ -f .env ] && set -a && . ./.env && set +a
exec .venv/bin/python app.py
