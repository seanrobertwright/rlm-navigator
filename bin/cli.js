#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const { execSync, spawnSync } = require("child_process");
const net = require("net");
const readline = require("readline");
const chalk = require("chalk");
const ora = require("ora");

const CWD = process.cwd();
const RLM_DIR = path.join(CWD, ".rlm");
const PKG_ROOT = path.resolve(__dirname, "..");

// ---------------------------------------------------------------------------
// Visual Helpers
// ---------------------------------------------------------------------------

const BANNER = `
  ${chalk.cyan("╔═══════════════════════════════════════════╗")}
  ${chalk.cyan("║")}                                           ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██████╗ ██╗     ███╗   ███╗")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██╔══██╗██║     ████╗ ████║")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██████╔╝██║     ██╔████╔██║")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██╔══██╗██║     ██║╚██╔╝██║")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██║  ██║███████╗██║ ╚═╝ ██║")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}          ${chalk.bold.white("N A V I G A T O R")}                ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.dim("Token-efficient codebase navigation")}     ${chalk.cyan("║")}
  ${chalk.cyan("║")}                                           ${chalk.cyan("║")}
  ${chalk.cyan("╚═══════════════════════════════════════════╝")}
`;

function banner() {
  console.log(BANNER);
}

function step(text) {
  return ora({ text, spinner: "dots", color: "cyan" }).start();
}

function successBox(lines) {
  const maxLen = Math.max(...lines.map((l) => l.length));
  const pad = (s) => s + " ".repeat(maxLen - s.length);
  console.log("");
  console.log(chalk.green("  ┌─" + "─".repeat(maxLen + 2) + "─┐"));
  for (const line of lines) {
    console.log(chalk.green("  │ ") + pad(line) + chalk.green("  │"));
  }
  console.log(chalk.green("  └─" + "─".repeat(maxLen + 2) + "─┘"));
  console.log("");
}

function errorBox(title, detail) {
  console.log("");
  console.log(chalk.red("  ✖ " + chalk.bold(title)));
  if (detail) console.log(chalk.dim("    " + detail));
  console.log("");
}

// ---------------------------------------------------------------------------
// Core Helpers
// ---------------------------------------------------------------------------

function copyDirSync(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
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
  try {
    const result = execSync(cmd, { stdio: "pipe", encoding: "utf-8", ...opts });
    return { ok: true, stdout: result || "", stderr: "" };
  } catch (err) {
    return {
      ok: false,
      stdout: err.stdout || "",
      stderr: err.stderr || err.message,
    };
  }
}

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

async function install() {
  banner();

  // Pre-flight: already installed?
  if (fs.existsSync(RLM_DIR)) {
    errorBox(
      "Already installed",
      "Run 'npx rlm-navigator uninstall' first to reinstall."
    );
    process.exit(1);
  }

  // Pre-flight: Python
  let spinner = step("Checking Python availability...");
  const python = findPython();
  if (!python) {
    spinner.fail("Python not found");
    errorBox(
      "Python 3.8+ required",
      "Install Python and ensure 'python' or 'python3' is on PATH."
    );
    process.exit(1);
  }
  spinner.succeed(`Python found (${chalk.dim(python)})`);

  // 1. Copy files
  spinner = step("Copying daemon, server, and skills...");
  try {
    copyDirSync(path.join(PKG_ROOT, "daemon"), path.join(RLM_DIR, "daemon"));
    copyDirSync(path.join(PKG_ROOT, "server"), path.join(RLM_DIR, "server"));

    const srcSkills = path.join(PKG_ROOT, ".claude", "skills");
    const srcAgents = path.join(PKG_ROOT, ".claude", "agents");
    const destClaude = path.join(CWD, ".claude");
    if (fs.existsSync(srcSkills)) {
      copyDirSync(srcSkills, path.join(destClaude, "skills"));
    }
    if (fs.existsSync(srcAgents)) {
      copyDirSync(srcAgents, path.join(destClaude, "agents"));
    }
    spinner.succeed("Files copied");
  } catch (err) {
    spinner.fail("Failed to copy files");
    errorBox("Copy failed", err.message);
    process.exit(1);
  }

  // 2. Python deps
  spinner = step("Installing Python dependencies...");
  const reqFile = path.join(RLM_DIR, "daemon", "requirements.txt");
  let result = run(`${python} -m pip install -r "${reqFile}"`);
  if (!result.ok) {
    spinner.fail("Failed to install Python dependencies");
    if (result.stderr) console.log(chalk.dim(result.stderr));
    process.exit(1);
  }
  spinner.succeed("Python dependencies installed");

  // 3. Build MCP server
  spinner = step("Building MCP server...");
  result = run("npm install", { cwd: path.join(RLM_DIR, "server") });
  if (!result.ok) {
    spinner.fail("npm install failed");
    if (result.stderr) console.log(chalk.dim(result.stderr));
    process.exit(1);
  }
  result = run("npm run build", { cwd: path.join(RLM_DIR, "server") });
  if (!result.ok) {
    spinner.fail("MCP server build failed");
    if (result.stderr) console.log(chalk.dim(result.stderr));
    process.exit(1);
  }
  spinner.succeed("MCP server built");

  // 4. CLAUDE.md
  spinner = step("Integrating CLAUDE.md...");
  const snippetPath = path.join(PKG_ROOT, "templates", "CLAUDE_SNIPPET.md");
  const snippet = fs.readFileSync(snippetPath, "utf-8");
  const claudeMdPath = path.join(CWD, "CLAUDE.md");

  if (!fs.existsSync(claudeMdPath)) {
    fs.writeFileSync(claudeMdPath, snippet);
    spinner.succeed("Created CLAUDE.md");
  } else {
    const existing = fs.readFileSync(claudeMdPath, "utf-8");
    if (existing.includes("<!-- rlm-navigator:start -->")) {
      spinner.succeed("CLAUDE.md already configured");
    } else {
      fs.writeFileSync(claudeMdPath, snippet + "\n" + existing);
      spinner.succeed("Updated CLAUDE.md");
    }
  }

  // 5. .gitignore prompt
  spinner = step("Checking .gitignore...");
  spinner.stop();
  const answer = await ask(chalk.cyan("  ? ") + "Add .rlm/ to .gitignore? " + chalk.dim("[Y/n] "));
  spinner = step("Updating .gitignore...");

  const gitignorePath = path.join(CWD, ".gitignore");
  if (answer.toLowerCase() !== "n") {
    if (fs.existsSync(gitignorePath)) {
      const content = fs.readFileSync(gitignorePath, "utf-8");
      if (!content.includes(".rlm")) {
        fs.appendFileSync(gitignorePath, "\n# RLM Navigator (local install)\n.rlm/\n");
        spinner.succeed("Added .rlm/ to .gitignore");
      } else {
        spinner.succeed(".rlm/ already in .gitignore");
      }
    } else {
      fs.writeFileSync(gitignorePath, "# RLM Navigator (local install)\n.rlm/\n");
      spinner.succeed("Created .gitignore");
    }
  } else {
    spinner.info("Skipped .gitignore");
  }

  // 6. Register MCP server
  spinner = step("Registering MCP server with Claude Code...");
  const mcpServerPath = path.join(RLM_DIR, "server", "build", "index.js");
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;

  if (claudeAvailable) {
    result = run(`claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`);
    if (result.ok) {
      spinner.succeed("MCP server registered");
    } else {
      spinner.warn("Auto-registration failed");
      console.log(chalk.dim("  Run manually:"));
      console.log(chalk.dim(`  claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`));
    }
  } else {
    spinner.warn("Claude CLI not found — register manually:");
    console.log(chalk.dim(`  claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`));
  }

  successBox([
    chalk.bold.green("Installation complete!"),
    "",
    "The daemon will auto-start when Claude Code connects.",
    `Run ${chalk.cyan("npx rlm-navigator status")} to check daemon health.`,
  ]);
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------

function update() {
  banner();

  if (!fs.existsSync(RLM_DIR)) {
    errorBox("Not installed", "Run 'npx rlm-navigator install' first.");
    process.exit(1);
  }

  let spinner = step("Checking Python...");
  const python = findPython();
  if (!python) {
    spinner.fail("Python not found");
    errorBox("Python 3.8+ required", "Install Python and ensure it's on PATH.");
    process.exit(1);
  }
  spinner.succeed(`Python found (${chalk.dim(python)})`);

  // 1. Copy source files
  spinner = step("Updating daemon and server source files...");
  copyDirSync(path.join(PKG_ROOT, "daemon"), path.join(RLM_DIR, "daemon"));
  copyDirSync(path.join(PKG_ROOT, "server"), path.join(RLM_DIR, "server"));
  spinner.succeed("Source files updated");

  // 2. Skills and agents
  spinner = step("Updating skill and agent files...");
  const srcSkills = path.join(PKG_ROOT, ".claude", "skills");
  const srcAgents = path.join(PKG_ROOT, ".claude", "agents");
  const destClaude = path.join(CWD, ".claude");
  if (fs.existsSync(srcSkills)) {
    copyDirSync(srcSkills, path.join(destClaude, "skills"));
  }
  if (fs.existsSync(srcAgents)) {
    copyDirSync(srcAgents, path.join(destClaude, "agents"));
  }
  spinner.succeed("Skills and agents updated");

  // 3. Deps and rebuild
  spinner = step("Installing dependencies...");
  const reqFile = path.join(RLM_DIR, "daemon", "requirements.txt");
  let result = run(`${python} -m pip install -r "${reqFile}"`);
  if (!result.ok) {
    spinner.warn("Python deps may have issues");
    if (result.stderr) console.log(chalk.dim(result.stderr));
  } else {
    spinner.succeed("Python dependencies installed");
  }

  spinner = step("Building MCP server...");
  result = run("npm install", { cwd: path.join(RLM_DIR, "server") });
  if (!result.ok) {
    spinner.fail("npm install failed");
    if (result.stderr) console.log(chalk.dim(result.stderr));
    process.exit(1);
  }
  result = run("npm run build", { cwd: path.join(RLM_DIR, "server") });
  if (!result.ok) {
    spinner.fail("MCP server build failed");
    if (result.stderr) console.log(chalk.dim(result.stderr));
    process.exit(1);
  }
  spinner.succeed("MCP server built");

  // 4. CLAUDE.md
  spinner = step("Updating CLAUDE.md...");
  const snippetPath = path.join(PKG_ROOT, "templates", "CLAUDE_SNIPPET.md");
  const snippet = fs.readFileSync(snippetPath, "utf-8");
  const claudeMdPath = path.join(CWD, "CLAUDE.md");

  if (!fs.existsSync(claudeMdPath)) {
    fs.writeFileSync(claudeMdPath, snippet);
    spinner.succeed("Created CLAUDE.md");
  } else {
    const existing = fs.readFileSync(claudeMdPath, "utf-8");
    if (existing.includes("<!-- rlm-navigator:start -->")) {
      const updated = existing.replace(
        /<!-- rlm-navigator:start -->[\s\S]*?<!-- rlm-navigator:end -->\n?/,
        snippet
      );
      fs.writeFileSync(claudeMdPath, updated);
      spinner.succeed("CLAUDE.md snippet replaced");
    } else {
      fs.writeFileSync(claudeMdPath, snippet + "\n" + existing);
      spinner.succeed("CLAUDE.md updated");
    }
  }

  // 5. Migrate MCP registration
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;
  if (claudeAvailable) {
    spinner = step("Ensuring project-scoped MCP registration...");
    const mcpServerPath = path.join(RLM_DIR, "server", "build", "index.js");
    run("claude mcp remove rlm-navigator --scope user");
    result = run(`claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`);
    if (result.ok) {
      spinner.succeed("MCP registration updated");
    } else {
      spinner.warn("MCP registration may need manual update");
    }
  }

  successBox([
    chalk.bold.green("Update complete!"),
    "",
    "Restart Claude Code to pick up changes.",
  ]);
}

// ---------------------------------------------------------------------------
// Uninstall
// ---------------------------------------------------------------------------

function uninstall() {
  banner();

  // 1. Remove MCP registration
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;
  if (claudeAvailable) {
    let spinner = step("Removing MCP server registration...");
    run("claude mcp remove rlm-navigator --scope project");
    run("claude mcp remove rlm-navigator --scope user");
    spinner.succeed("MCP registration removed");
  }

  // 2. Remove .rlm/
  let spinner;
  if (fs.existsSync(RLM_DIR)) {
    spinner = step("Removing .rlm/ directory...");
    fs.rmSync(RLM_DIR, { recursive: true, force: true });
    spinner.succeed(".rlm/ removed");
  } else {
    console.log(chalk.dim("  .rlm/ not found — nothing to remove."));
  }

  // 3. Remove .claude/skills/rlm-navigator/ and agent
  spinner = step("Removing skill and agent files...");
  const skillDir = path.join(CWD, ".claude", "skills", "rlm-navigator");
  const agentFile = path.join(CWD, ".claude", "agents", "rlm-subcall.md");
  let removedAny = false;
  if (fs.existsSync(skillDir)) {
    fs.rmSync(skillDir, { recursive: true, force: true });
    removedAny = true;
  }
  if (fs.existsSync(agentFile)) {
    fs.unlinkSync(agentFile);
    removedAny = true;
  }
  if (removedAny) {
    spinner.succeed("Skill and agent files removed");
  } else {
    spinner.succeed("No skill/agent files to remove");
  }

  // 4. Remove CLAUDE.md snippet
  spinner = step("Cleaning CLAUDE.md...");
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
        spinner.succeed("Removed empty CLAUDE.md");
      } else {
        fs.writeFileSync(claudeMdPath, cleaned);
        spinner.succeed("Removed RLM block from CLAUDE.md");
      }
    } else {
      spinner.succeed("No RLM block in CLAUDE.md");
    }
  } else {
    spinner.succeed("No CLAUDE.md to clean");
  }

  successBox([
    chalk.bold.green("Uninstall complete!"),
    "",
    "RLM Navigator has been removed from this project.",
  ]);
}

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------

function status() {
  const divider = chalk.dim("  ────────────────────────");

  console.log("");
  console.log(chalk.bold.cyan("  RLM Navigator Status"));
  console.log(divider);

  // Check .rlm/
  if (!fs.existsSync(RLM_DIR)) {
    console.log(`  Installed:   ${chalk.red("✖ No")}`);
    console.log("");
    console.log(chalk.dim(`  Run: npx rlm-navigator install`));
    console.log("");
    return;
  }
  console.log(`  Installed:   ${chalk.green("✔ Yes")}`);

  // Check port file
  const portFile = path.join(RLM_DIR, "port");
  let port = 9177;
  if (fs.existsSync(portFile)) {
    port = parseInt(fs.readFileSync(portFile, "utf-8").trim(), 10);
    console.log(`  Port:        ${chalk.white(port)}`);
  } else {
    console.log(`  Port:        ${chalk.dim("no port file")}`);
  }

  // TCP health check
  const client = new net.Socket();
  const timer = setTimeout(() => {
    client.destroy();
    console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(timeout)")}`);
    console.log("");
  }, 2000);

  client.connect(port, "127.0.0.1", () => {
    client.on("data", (chunk) => {
      clearTimeout(timer);
      const msg = chunk.toString("utf-8");
      client.destroy();
      if (msg.includes("ALIVE")) {
        console.log(`  Daemon:      ${chalk.green("● ONLINE")}`);
      } else {
        console.log(`  Daemon:      ${chalk.yellow("● UNKNOWN")} ${chalk.dim("(unexpected response)")}`);
      }
      console.log("");
    });
  });

  client.on("error", () => {
    clearTimeout(timer);
    console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(connection refused)")}`);
    console.log("");
  });
}

// ---------------------------------------------------------------------------
// Help
// ---------------------------------------------------------------------------

function help() {
  banner();

  const cmd = (name, desc) =>
    `  ${chalk.cyan(name.padEnd(14))}${desc}`;

  console.log(chalk.bold("  Usage:") + chalk.dim("  npx rlm-navigator <command>"));
  console.log("");
  console.log(chalk.bold("  Commands:"));
  console.log(cmd("install", "Install RLM Navigator into the current project"));
  console.log(cmd("update", "Update an existing installation to latest version"));
  console.log(cmd("uninstall", "Remove RLM Navigator from the current project"));
  console.log(cmd("status", "Check daemon health and installation status"));
  console.log(cmd("help", "Show this help message"));
  console.log("");
  console.log(chalk.dim("  The daemon auto-starts when Claude Code connects."));
  console.log("");
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const command = process.argv[2] || "help";

switch (command) {
  case "install":
    install().catch((err) => {
      errorBox("Install failed", err.message);
      process.exit(1);
    });
    break;
  case "update":
    update();
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
    console.error(chalk.red(`  Unknown command: ${command}`));
    console.log("");
    help();
    process.exit(1);
}
