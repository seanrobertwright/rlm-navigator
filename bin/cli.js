#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { execSync, spawnSync } = require("child_process");
const net = require("net");
const readline = require("readline");

const CWD = process.cwd();
const RLM_DIR = path.join(CWD, ".rlm");
const PKG_ROOT = path.resolve(__dirname, "..");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function copyDirSync(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      // Skip node_modules, build, __pycache__, .venv
      if (["node_modules", "build", "__pycache__", ".venv", ".git"].includes(entry.name)) continue;
      copyDirSync(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function findPython() {
  for (const cmd of ["python", "python3"]) {
    try {
      const result = spawnSync(cmd, ["--version"], { stdio: "pipe" });
      if (result.status === 0) return cmd;
    } catch {
      // not found
    }
  }
  return null;
}

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim());
    });
  });
}

function run(cmd, opts = {}) {
  console.log(`  $ ${cmd}`);
  try {
    execSync(cmd, { stdio: "inherit", ...opts });
    return true;
  } catch (err) {
    console.error(`  Command failed: ${cmd}`);
    return false;
  }
}

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

async function install() {
  console.log("=== RLM Navigator — Per-Project Install ===\n");

  if (fs.existsSync(RLM_DIR)) {
    console.log(".rlm/ already exists. Run 'rlm-navigator uninstall' first to reinstall.");
    process.exit(1);
  }

  // Check Python
  const python = findPython();
  if (!python) {
    console.error("Error: Python not found. Install Python 3.8+ and ensure 'python' or 'python3' is on PATH.");
    process.exit(1);
  }

  // 1. Copy daemon/, server/, and .claude/ into .rlm/ and project
  console.log("[1/5] Copying daemon, server, and skills ...");
  copyDirSync(path.join(PKG_ROOT, "daemon"), path.join(RLM_DIR, "daemon"));
  copyDirSync(path.join(PKG_ROOT, "server"), path.join(RLM_DIR, "server"));

  // Copy .claude/skills/ and .claude/agents/ into project's .claude/
  const srcSkills = path.join(PKG_ROOT, ".claude", "skills");
  const srcAgents = path.join(PKG_ROOT, ".claude", "agents");
  const destClaude = path.join(CWD, ".claude");
  if (fs.existsSync(srcSkills)) {
    copyDirSync(srcSkills, path.join(destClaude, "skills"));
  }
  if (fs.existsSync(srcAgents)) {
    copyDirSync(srcAgents, path.join(destClaude, "agents"));
  }
  console.log("  Done.\n");

  // 2. Install Python deps
  console.log("[2/5] Installing Python dependencies ...");
  const reqFile = path.join(RLM_DIR, "daemon", "requirements.txt");
  if (!run(`${python} -m pip install -r "${reqFile}"`)) {
    console.error("\nFailed to install Python dependencies. Check pip is available.");
    process.exit(1);
  }
  console.log("");

  // 3. Build MCP server
  console.log("[3/5] Building MCP server ...");
  if (!run("npm install", { cwd: path.join(RLM_DIR, "server") })) {
    process.exit(1);
  }
  if (!run("npm run build", { cwd: path.join(RLM_DIR, "server") })) {
    process.exit(1);
  }
  console.log("");

  // 4. CLAUDE.md integration
  console.log("[4/5] Integrating CLAUDE.md ...");
  const snippetPath = path.join(PKG_ROOT, "templates", "CLAUDE_SNIPPET.md");
  const snippet = fs.readFileSync(snippetPath, "utf-8");
  const claudeMdPath = path.join(CWD, "CLAUDE.md");

  if (!fs.existsSync(claudeMdPath)) {
    fs.writeFileSync(claudeMdPath, snippet);
    console.log("  Created CLAUDE.md with RLM Navigator instructions.\n");
  } else {
    const existing = fs.readFileSync(claudeMdPath, "utf-8");
    if (existing.includes("<!-- rlm-navigator:start -->")) {
      console.log("  CLAUDE.md already contains RLM Navigator block — skipped.\n");
    } else {
      fs.writeFileSync(claudeMdPath, existing + "\n" + snippet);
      console.log("  Appended RLM Navigator block to CLAUDE.md.\n");
    }
  }

  // 5. .gitignore
  const gitignorePath = path.join(CWD, ".gitignore");
  const answer = await ask("Add .rlm/ to .gitignore? [Y/n] ");
  if (answer.toLowerCase() !== "n") {
    if (fs.existsSync(gitignorePath)) {
      const content = fs.readFileSync(gitignorePath, "utf-8");
      if (!content.includes(".rlm")) {
        fs.appendFileSync(gitignorePath, "\n# RLM Navigator (local install)\n.rlm/\n");
        console.log("  Added .rlm/ to .gitignore.\n");
      } else {
        console.log("  .rlm already in .gitignore.\n");
      }
    } else {
      fs.writeFileSync(gitignorePath, "# RLM Navigator (local install)\n.rlm/\n");
      console.log("  Created .gitignore with .rlm/ entry.\n");
    }
  }

  // 6. Register MCP server
  console.log("[5/5] Registering MCP server with Claude Code ...");
  const mcpServerPath = path.join(RLM_DIR, "server", "build", "index.js");
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;

  if (claudeAvailable) {
    const registerCmd = `claude mcp add rlm-navigator --env RLM_PROJECT_ROOT="${CWD}" -- node "${mcpServerPath}"`;
    if (!run(registerCmd)) {
      console.log("  Manual registration command:");
      console.log(`  claude mcp add rlm-navigator --env RLM_PROJECT_ROOT="${CWD}" -- node "${mcpServerPath}"`);
    }
  } else {
    console.log("  Claude CLI not found. Register manually:");
    console.log(`  claude mcp add rlm-navigator --env RLM_PROJECT_ROOT="${CWD}" -- node "${mcpServerPath}"`);
  }

  console.log("\n=== Installation complete ===");
  console.log("\nThe daemon will auto-start when Claude Code connects.");
  console.log("Run 'npx rlm-navigator status' to check daemon health.");
}

// ---------------------------------------------------------------------------
// Uninstall
// ---------------------------------------------------------------------------

function uninstall() {
  console.log("=== RLM Navigator — Uninstall ===\n");

  // 1. Remove MCP registration
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;
  if (claudeAvailable) {
    console.log("Removing MCP server registration ...");
    run("claude mcp remove rlm-navigator");
  }

  // 2. Remove .rlm/
  if (fs.existsSync(RLM_DIR)) {
    console.log("Removing .rlm/ directory ...");
    fs.rmSync(RLM_DIR, { recursive: true, force: true });
    console.log("  Done.\n");
  } else {
    console.log(".rlm/ not found — nothing to remove.\n");
  }

  // 3. Remove .claude/skills/rlm-navigator/ and .claude/agents/rlm-subcall.md
  const skillDir = path.join(CWD, ".claude", "skills", "rlm-navigator");
  const agentFile = path.join(CWD, ".claude", "agents", "rlm-subcall.md");
  if (fs.existsSync(skillDir)) {
    fs.rmSync(skillDir, { recursive: true, force: true });
    console.log("Removed .claude/skills/rlm-navigator/");
  }
  if (fs.existsSync(agentFile)) {
    fs.unlinkSync(agentFile);
    console.log("Removed .claude/agents/rlm-subcall.md");
  }

  // 4. Remove CLAUDE.md snippet
  const claudeMdPath = path.join(CWD, "CLAUDE.md");
  if (fs.existsSync(claudeMdPath)) {
    const content = fs.readFileSync(claudeMdPath, "utf-8");
    if (content.includes("<!-- rlm-navigator:start -->")) {
      const cleaned = content.replace(
        /\n?<!-- rlm-navigator:start -->[\s\S]*?<!-- rlm-navigator:end -->\n?/,
        ""
      );
      if (cleaned.trim() === "") {
        fs.unlinkSync(claudeMdPath);
        console.log("Removed empty CLAUDE.md.");
      } else {
        fs.writeFileSync(claudeMdPath, cleaned);
        console.log("Removed RLM Navigator block from CLAUDE.md.");
      }
    }
  }

  console.log("\n=== Uninstall complete ===");
}

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

function status() {
  console.log("=== RLM Navigator — Status ===\n");

  // Check .rlm/
  if (!fs.existsSync(RLM_DIR)) {
    console.log("Not installed (.rlm/ not found).");
    console.log("Run: npx rlm-navigator install");
    return;
  }
  console.log(".rlm/ directory: present");

  // Check port file
  const portFile = path.join(RLM_DIR, "port");
  let port = 9177;
  if (fs.existsSync(portFile)) {
    port = parseInt(fs.readFileSync(portFile, "utf-8").trim(), 10);
    console.log(`Port file: ${port}`);
  } else {
    console.log("Port file: not found (daemon may not be running)");
  }

  // TCP health check
  const client = new net.Socket();
  const timer = setTimeout(() => {
    client.destroy();
    console.log("Daemon: OFFLINE (connection timed out)");
  }, 2000);

  client.connect(port, "127.0.0.1", () => {
    client.on("data", (chunk) => {
      clearTimeout(timer);
      const msg = chunk.toString("utf-8");
      client.destroy();
      if (msg.includes("ALIVE")) {
        console.log("Daemon: ONLINE");
      } else {
        console.log("Daemon: responded but unexpected message");
      }
    });
  });

  client.on("error", () => {
    clearTimeout(timer);
    console.log("Daemon: OFFLINE (connection refused)");
  });
}

// ---------------------------------------------------------------------------
// Help
// ---------------------------------------------------------------------------

function help() {
  console.log(`
rlm-navigator — Token-efficient codebase navigation for AI coding

Usage:
  npx rlm-navigator <command>

Commands:
  install     Install RLM Navigator into the current project (.rlm/)
  uninstall   Remove RLM Navigator from the current project
  status      Check daemon health and installation status
  help        Show this help message

The install command:
  - Copies daemon and server into .rlm/
  - Installs Python dependencies
  - Builds the MCP server
  - Registers with Claude Code
  - Integrates CLAUDE.md with navigation instructions
  - Daemon auto-starts when Claude Code connects
`.trim());
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const command = process.argv[2] || "help";

switch (command) {
  case "install":
    install().catch((err) => {
      console.error("Install failed:", err.message);
      process.exit(1);
    });
    break;
  case "uninstall":
    uninstall();
    break;
  case "status":
    status();
    break;
  case "help":
  case "--help":
  case "-h":
    help();
    break;
  default:
    console.error(`Unknown command: ${command}`);
    help();
    process.exit(1);
}
