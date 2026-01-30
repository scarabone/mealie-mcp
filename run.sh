#!/bin/bash
set -e
cd "$(dirname "$0")"

# Load environment
if [ -f .env ]; then
    source .env
    export MEALIE_URL MEALIE_API_KEY MEALIE_USERNAME MEALIE_PASSWORD
fi

# Activate venv and run server
source venv/bin/activate
exec python3 server.py
