#!/bin/bash
set -e

REMOTE="services"
REMOTE_PATH="/opt/mcp-servers/mealie"

echo "=== Mealie MCP Server Deployment ==="

# Check if .env exists locally
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy .env.example and configure it first."
    exit 1
fi

echo "[1/6] Stopping existing service (if running)..."
ssh $REMOTE "systemctl stop mealie-mcp 2>/dev/null || true"

echo "[2/6] Creating remote directory..."
ssh $REMOTE "mkdir -p $REMOTE_PATH"

echo "[3/6] Copying files..."
scp -q server.py requirements.txt run.sh run-proxy.sh .env $REMOTE:$REMOTE_PATH/
scp -q mealie-mcp.service $REMOTE:/etc/systemd/system/
ssh $REMOTE "chmod +x $REMOTE_PATH/run.sh $REMOTE_PATH/run-proxy.sh"

echo "[4/6] Setting up Python venv and dependencies..."
ssh $REMOTE "cd $REMOTE_PATH && python3 -m venv venv && source venv/bin/activate && pip install -q -r requirements.txt"

echo "[5/6] Installing and enabling systemd service..."
ssh $REMOTE "systemctl daemon-reload && systemctl enable mealie-mcp"

echo "[6/6] Starting service..."
ssh $REMOTE "systemctl start mealie-mcp"

echo ""
echo "=== Deployment Complete ==="
echo ""
echo "Service status:"
ssh $REMOTE "systemctl status mealie-mcp --no-pager -l | head -15"

echo ""
echo "MCP Server available at:"
echo "  - Local:     http://192.168.2.100:8096/sse"
echo "  - Tailscale: http://100.78.245.24:8096/sse"
echo ""
echo "Claude Desktop config:"
cat << 'EOF'
{
  "mcpServers": {
    "mealie": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.2.100:8096/sse"]
    }
  }
}
EOF
