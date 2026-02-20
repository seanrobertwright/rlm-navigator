# Legacy installer. Prefer: npx rlm-navigator@latest install
# RLM Navigator Installer for Windows
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "=== RLM Navigator Installer ===" -ForegroundColor Cyan
Write-Host ""

# 1. Install Python dependencies
Write-Host "[1/3] Installing Python daemon dependencies..." -ForegroundColor Yellow
pip install -r "$ScriptDir\daemon\requirements.txt"
Write-Host "  Done." -ForegroundColor Green

# 2. Build MCP server
Write-Host "[2/3] Building MCP server..." -ForegroundColor Yellow
Push-Location "$ScriptDir\server"
npm install
npm run build
Pop-Location
Write-Host "  Done." -ForegroundColor Green

# 3. Register MCP server with Claude Code
Write-Host "[3/3] Registering MCP server with Claude Code..." -ForegroundColor Yellow
$McpServerPath = Join-Path $ScriptDir "server\build\index.js"

$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if ($claudeCmd) {
    claude mcp add rlm-navigator -- node $McpServerPath
    Write-Host "  Registered rlm-navigator MCP server." -ForegroundColor Green
} else {
    Write-Host "  Claude CLI not found. Register manually:" -ForegroundColor Yellow
    Write-Host "    claude mcp add rlm-navigator -- node $McpServerPath"
}

Write-Host ""
Write-Host "=== Installation complete ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Usage:"
Write-Host "  1. Start the daemon:  python $ScriptDir\daemon\rlm_daemon.py --root C:\your\project"
Write-Host "  2. In Claude Code, the rlm-navigator tools are now available."
Write-Host "  3. Use the /rlm-navigator skill for guided navigation."
