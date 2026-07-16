#!/usr/bin/env bash
# Push the travel-agent prompt, HTTP API tools, and client actions to the
# Vocal Bridge agent. Requires PUBLIC_BASE_URL and TOOL_API_KEY in the env
# (source your .env first), plus an authenticated `vb` CLI.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

: "${PUBLIC_BASE_URL:?set PUBLIC_BASE_URL (your https tunnel, no trailing slash)}"
: "${TOOL_API_KEY:?set TOOL_API_KEY (shared secret for tool endpoints)}"

# Render the tool template with the live URL + secret.
sed -e "s#__PUBLIC_BASE_URL__#${PUBLIC_BASE_URL}#g" \
    -e "s#__TOOL_API_KEY__#${TOOL_API_KEY}#g" \
    api_tools.template.json > api_tools.json

echo "==> Setting system prompt"
vb prompt set --file travel_prompt.txt

echo "==> Setting greeting + deploy target (web)"
vb config set --greeting "Hi, this is Skylark, your travel assistant. I can help you book flights, hotels, and rental cars. What can I do for you?" \
              --deploy-targets web

echo "==> Setting HTTP API tools (flights / hotels / cars: search + book)"
vb config set --api-tools-file api_tools.json

echo "==> Setting client actions (set_session / show_* / *_booking)"
vb config set --client-actions-file client_actions.json

echo "==> Done. Current tools:"
vb config get api-tools
