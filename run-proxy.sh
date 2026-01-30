#!/bin/bash
set -e

# Ensure we're in the right directory
SCRIPT_DIR="/opt/mcp-servers/mealie"
cd "$SCRIPT_DIR"

# Load environment from .env file
if [ -f "$SCRIPT_DIR/.env" ]; then
    while IFS='=' read -r key value; do
        # Skip comments and empty lines
        [[ "$key" =~ ^#.*$ ]] && continue
        [[ -z "$key" ]] && continue
        # Remove any leading/trailing whitespace and quotes
        value=$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//')
        export "$key=$value"
    done < "$SCRIPT_DIR/.env"
fi

# Activate venv and run mcp-proxy wrapping the server
# Use explicit -e flags to pass environment to subprocess
source "$SCRIPT_DIR/venv/bin/activate"
exec "$SCRIPT_DIR/venv/bin/mcp-proxy" --host=0.0.0.0 --port=8096 \
    -e MEALIE_URL "$MEALIE_URL" \
    -e MEALIE_API_KEY "$MEALIE_API_KEY" \
    -e MEALIE_USERNAME "${MEALIE_USERNAME:-}" \
    -e MEALIE_PASSWORD "${MEALIE_PASSWORD:-}" \
    -e MCP_LOG_FILE "/var/log/mealie-mcp.log" \
    "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/server.py"
