#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== RLM Navigator Installer ==="
echo ""

# 1. Install Python dependencies
echo "[1/3] Installing Python daemon dependencies..."
pip install -r "$SCRIPT_DIR/daemon/requirements.txt"
echo "  Done."

# 2. Build MCP server
echo "[2/3] Building MCP server..."
cd "$SCRIPT_DIR/server"
npm install
npm run build
cd "$SCRIPT_DIR"
echo "  Done."

# 3. Register MCP server with Claude Code
echo "[3/3] Registering MCP server with Claude Code..."
MCP_SERVER_PATH="$SCRIPT_DIR/server/build/index.js"

if command -v claude &> /dev/null; then
    claude mcp add rlm-navigator -- node "$MCP_SERVER_PATH"
    echo "  Registered rlm-navigator MCP server."
else
    echo "  Claude CLI not found. Register manually:"
    echo "    claude mcp add rlm-navigator -- node $MCP_SERVER_PATH"
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "Usage:"
echo "  1. Start the daemon:  python $SCRIPT_DIR/daemon/rlm_daemon.py --root /your/project"
echo "  2. In Claude Code, the rlm-navigator tools are now available."
echo "  3. Use the /rlm-navigator skill for guided navigation."
