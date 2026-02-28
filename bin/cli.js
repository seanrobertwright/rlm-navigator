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
  ${chalk.cyan("╔═══════════════════════════════════════════════════╗")}
  ${chalk.cyan("║")}                                                   ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██████╗ ██╗     ███╗   ███╗")}       ${chalk.yellow("▲")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██╔══██╗██║     ████╗ ████║")}       ${chalk.yellow("N")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██████╔╝██║     ██╔████╔██║")}   ${chalk.yellow("◄W──◆──E►")}         ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██╔══██╗██║     ██║╚██╔╝██║")}       ${chalk.yellow("S")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("██║  ██║███████╗██║ ╚═╝ ██║")}       ${chalk.yellow("▼")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.bold.cyan("╚═╝  ╚═╝╚══════╝╚═╝     ╚═╝")}                     ${chalk.cyan("║")}
  ${chalk.cyan("║")}          ${chalk.bold.white("N A V I G A T O R")}                        ${chalk.cyan("║")}
  ${chalk.cyan("║")}   ${chalk.dim("Token-efficient codebase navigation")}             ${chalk.cyan("║")}
  ${chalk.cyan("║")}                                                   ${chalk.cyan("║")}
  ${chalk.cyan("╚═══════════════════════════════════════════════════╝")}
`;

// ---------------------------------------------------------------------------
// Enrichment Provider Data
// ---------------------------------------------------------------------------

const ENRICHMENT_PROVIDERS = [
  {
    name: "Anthropic",
    key: "anthropic",
    desc: "Claude Haiku 4.5",
    api_key_env: "ANTHROPIC_API_KEY",
    models: [
      { id: "claude-haiku-4-5-20251001", label: "Claude Haiku 4.5 (recommended)" },
      { id: "claude-sonnet-4-5-20250514", label: "Claude Sonnet 4.5" },
    ],
  },
  {
    name: "OpenAI",
    key: "openai",
    desc: "GPT-4o-mini, GPT-4o, GPT-4.1-mini",
    api_key_env: "OPENAI_API_KEY",
    models: [
      { id: "gpt-4o-mini", label: "GPT-4o-mini (recommended)" },
      { id: "gpt-4o", label: "GPT-4o" },
      { id: "gpt-4.1-mini", label: "GPT-4.1-mini" },
    ],
  },
  {
    name: "OpenRouter",
    key: "openrouter",
    desc: "Multi-provider proxy",
    api_key_env: "OPENROUTER_API_KEY",
    models: [
      { id: "anthropic/claude-haiku-4-5", label: "Claude Haiku 4.5 (recommended)" },
      { id: "openai/gpt-4o-mini", label: "GPT-4o-mini" },
      { id: "google/gemini-2.0-flash", label: "Gemini 2.0 Flash" },
      { id: "meta-llama/llama-3.3-70b-instruct", label: "Llama 3.3 70B" },
    ],
  },
];

function apiKeyInstructions(envVar) {
  const isWindows = process.platform === "win32";
  if (isWindows) {
    return [
      chalk.bold("  Set your API key (PowerShell):"),
      chalk.dim(`    $env:${envVar} = "your-key-here"`) + chalk.dim("                # current session"),
      chalk.dim(`    [System.Environment]::SetEnvironmentVariable("${envVar}", "your-key-here", "User")`) + chalk.dim("  # permanent"),
    ];
  }
  return [
    chalk.bold("  Set your API key (bash/zsh):"),
    chalk.dim(`    export ${envVar}="your-key-here"`) + chalk.dim("                # current session"),
    chalk.dim(`    echo 'export ${envVar}="your-key-here"' >> ~/.bashrc`) + chalk.dim("   # permanent"),
  ];
}

async function configureEnrichment() {
  console.log("");
  console.log(chalk.bold.cyan("  Enrichment Provider"));
  console.log(chalk.dim("  Enrichment adds semantic summaries to code skeletons using a small LLM."));
  console.log(chalk.dim("  Requires an API key from your chosen provider."));
  console.log("");

  // Provider selection
  for (let i = 0; i < ENRICHMENT_PROVIDERS.length; i++) {
    const p = ENRICHMENT_PROVIDERS[i];
    console.log(`  ${chalk.cyan(i + 1 + ")")} ${chalk.white(p.name)}  ${chalk.dim("(" + p.desc + ")")}`);
  }
  console.log(`  ${chalk.cyan(ENRICHMENT_PROVIDERS.length + 1 + ")")} ${chalk.dim("Skip (no enrichment)")}`);
  console.log("");

  const providerAnswer = await ask(chalk.cyan("  ? ") + "Select provider " + chalk.dim(`[1-${ENRICHMENT_PROVIDERS.length + 1}] `) );
  const providerIdx = parseInt(providerAnswer, 10) - 1;

  if (isNaN(providerIdx) || providerIdx < 0 || providerIdx >= ENRICHMENT_PROVIDERS.length) {
    console.log(chalk.dim("  Skipping enrichment configuration."));
    return null;
  }

  const provider = ENRICHMENT_PROVIDERS[providerIdx];

  // Model selection
  console.log("");
  console.log(chalk.bold(`  ${provider.name} Models:`));
  for (let i = 0; i < provider.models.length; i++) {
    console.log(`  ${chalk.cyan(i + 1 + ")")} ${provider.models[i].label}`);
  }
  console.log("");

  const modelAnswer = await ask(chalk.cyan("  ? ") + "Select model " + chalk.dim(`[1-${provider.models.length}] `));
  const modelIdx = parseInt(modelAnswer, 10) - 1;
  const model = provider.models[Math.max(0, Math.min(modelIdx, provider.models.length - 1))] || provider.models[0];

  // API key instructions
  console.log("");
  const instructions = apiKeyInstructions(provider.api_key_env);
  for (const line of instructions) {
    console.log(line);
  }
  console.log("");

  return {
    provider: provider.key,
    model: model.id,
    api_key_env: provider.api_key_env,
  };
}

function writeEnrichmentConfig(enrichment) {
  const configPath = path.join(RLM_DIR, "config.json");
  let config = {};
  if (fs.existsSync(configPath)) {
    try {
      config = JSON.parse(fs.readFileSync(configPath, "utf-8"));
    } catch {}
  }
  config.enrichment = enrichment || { provider: null, model: null, api_key_env: null };
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");
}

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
// Shared Setup Helpers (used by install + update)
// ---------------------------------------------------------------------------

function checkPython() {
  let spinner = step("Checking Python availability...");
  const python = findPython();
  if (!python) {
    spinner.fail("Python not found");
    errorBox("Python 3.8+ required", "Install Python and ensure 'python' or 'python3' is on PATH.");
    process.exit(1);
  }
  spinner.succeed(`Python found (${chalk.dim(python)})`);
  return python;
}

function copySourceFiles() {
  let spinner = step("Copying daemon, server, and skills...");
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
}

function installPythonDeps(python, { strict = true } = {}) {
  let spinner = step("Installing Python dependencies...");
  const reqFile = path.join(RLM_DIR, "daemon", "requirements.txt");
  const result = run(`${python} -m pip install -r "${reqFile}"`);
  if (!result.ok) {
    if (strict) {
      spinner.fail("Failed to install Python dependencies");
      if (result.stderr) console.log(chalk.dim(result.stderr));
      process.exit(1);
    } else {
      spinner.warn("Python deps may have issues");
      if (result.stderr) console.log(chalk.dim(result.stderr));
      return;
    }
  }
  spinner.succeed("Python dependencies installed");
}

function buildMcpServer() {
  let spinner = step("Building MCP server...");
  let result = run("npm install", { cwd: path.join(RLM_DIR, "server") });
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
}

function integrateClaudeMd({ replaceExisting = false } = {}) {
  let spinner = step(replaceExisting ? "Updating CLAUDE.md..." : "Integrating CLAUDE.md...");
  const snippetPath = path.join(PKG_ROOT, "templates", "CLAUDE_SNIPPET.md");
  const snippet = fs.readFileSync(snippetPath, "utf-8");
  const claudeMdPath = path.join(CWD, "CLAUDE.md");

  if (!fs.existsSync(claudeMdPath)) {
    fs.writeFileSync(claudeMdPath, snippet);
    spinner.succeed("Created CLAUDE.md");
  } else {
    const existing = fs.readFileSync(claudeMdPath, "utf-8");
    if (existing.includes("<!-- rlm-navigator:start -->")) {
      if (replaceExisting) {
        const updated = existing.replace(
          /<!-- rlm-navigator:start -->[\s\S]*?<!-- rlm-navigator:end -->\n?/,
          snippet
        );
        fs.writeFileSync(claudeMdPath, updated);
        spinner.succeed("CLAUDE.md snippet replaced");
      } else {
        spinner.succeed("CLAUDE.md already configured");
      }
    } else {
      fs.writeFileSync(claudeMdPath, snippet + "\n" + existing);
      spinner.succeed("Updated CLAUDE.md");
    }
  }
}

function registerMcpServer() {
  let spinner = step("Registering MCP server with Claude Code...");
  const mcpServerPath = path.join(RLM_DIR, "server", "build", "index.js");
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;

  if (claudeAvailable) {
    const result = run(`claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`);
    if (result.ok) {
      spinner.succeed("MCP server registered");
    } else {
      spinner.warn("Auto-registration failed");
      console.log(chalk.dim("  Run manually:"));
      console.log(chalk.dim(`  claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`));
    }
    return claudeAvailable;
  } else {
    spinner.warn("Claude CLI not found — register manually:");
    console.log(chalk.dim(`  claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`));
    return false;
  }
}

function registerHook() {
  let spinner = step("Registering session-end hook...");
  try {
    installHook();
    spinner.succeed("Session-end hook registered");
  } catch (err) {
    spinner.warn("Hook registration failed: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// Daemon Lifecycle Helpers
// ---------------------------------------------------------------------------

function shutdownDaemon() {
  const portFile = path.join(RLM_DIR, "port");
  const lockFile = path.join(RLM_DIR, "daemon.lock");

  let port = null;
  let pid = null;

  // Try lock file first
  if (fs.existsSync(lockFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(lockFile, "utf-8"));
      port = data.port;
      pid = data.pid;
    } catch {}
  }

  // Fall back to port file
  if (!port && fs.existsSync(portFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(portFile, "utf-8"));
      port = data.port;
      pid = pid || data.pid;
    } catch {}
  }

  if (!port && !pid) return;

  // Try graceful shutdown via TCP
  if (port) {
    try {
      const client = new net.Socket();
      client.connect(port, "127.0.0.1", () => {
        client.write(JSON.stringify({ action: "shutdown" }));
        client.destroy();
      });
      client.on("error", () => {});
    } catch {}

    // Wait up to 3 seconds for daemon to exit
    const start = Date.now();
    while (Date.now() - start < 3000) {
      if (pid && !isPidAlive(pid)) break;
      spawnSync("node", ["-e", "setTimeout(()=>{},200)"], { stdio: "ignore" });
    }
  }

  // Force kill if still alive
  if (pid && isPidAlive(pid)) {
    try {
      if (process.platform === "win32") {
        execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
      } else {
        process.kill(pid, "SIGKILL");
      }
    } catch {}
  }

  // Clean up files
  for (const f of [portFile, lockFile]) {
    try { fs.unlinkSync(f); } catch {}
  }
}

function isPidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Hook Helpers
// ---------------------------------------------------------------------------

function installHook() {
  // 1. Copy hook file
  const srcHook = path.join(PKG_ROOT, "hooks", "rlm-session-end.js");
  const destHookDir = path.join(RLM_DIR, "hooks");
  const destHook = path.join(destHookDir, "rlm-session-end.js");
  fs.mkdirSync(destHookDir, { recursive: true });
  fs.copyFileSync(srcHook, destHook);

  // 2. Merge into .claude/settings.json
  const settingsPath = path.join(CWD, ".claude", "settings.json");
  let settings = {};
  if (fs.existsSync(settingsPath)) {
    try {
      settings = JSON.parse(fs.readFileSync(settingsPath, "utf-8"));
    } catch {
      settings = {};
    }
  }

  if (!settings.hooks) settings.hooks = {};
  if (!Array.isArray(settings.hooks.SessionEnd)) settings.hooks.SessionEnd = [];

  const hookCmd = `node "${destHook.replace(/\\/g, "/")}"`;
  const alreadyRegistered = settings.hooks.SessionEnd.some((entry) =>
    (entry.hooks || []).some((h) => h.command && h.command.includes("rlm-session-end"))
  );

  if (!alreadyRegistered) {
    settings.hooks.SessionEnd.push({
      hooks: [{ type: "command", command: hookCmd }],
    });
  }

  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
}

function uninstallHook() {
  const settingsPath = path.join(CWD, ".claude", "settings.json");
  if (!fs.existsSync(settingsPath)) return;

  let settings;
  try {
    settings = JSON.parse(fs.readFileSync(settingsPath, "utf-8"));
  } catch {
    return;
  }

  if (settings.hooks && Array.isArray(settings.hooks.SessionEnd)) {
    settings.hooks.SessionEnd = settings.hooks.SessionEnd.filter((entry) =>
      !(entry.hooks || []).some((h) => h.command && h.command.includes("rlm-session-end"))
    );
    if (settings.hooks.SessionEnd.length === 0) {
      delete settings.hooks.SessionEnd;
    }
    if (Object.keys(settings.hooks).length === 0) {
      delete settings.hooks;
    }
  }

  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
}

// ---------------------------------------------------------------------------
// Install
// ---------------------------------------------------------------------------

async function install() {
  banner();

  // Pre-flight: already installed? Shut down existing daemon
  if (fs.existsSync(RLM_DIR)) {
    let spinner = step("Stopping existing daemon...");
    shutdownDaemon();
    spinner.succeed("Existing daemon stopped");
  }

  const python = checkPython();
  copySourceFiles();
  installPythonDeps(python);
  buildMcpServer();
  integrateClaudeMd();

  // Enrichment provider selection
  const enrichment = await configureEnrichment();
  let enrichSpinner = step("Saving enrichment configuration...");
  writeEnrichmentConfig(enrichment);
  if (enrichment) {
    enrichSpinner.succeed(`Enrichment: ${enrichment.provider} / ${enrichment.model}`);
  } else {
    enrichSpinner.succeed("Enrichment: skipped");
  }

  // .gitignore prompt (install-only)
  let spinner = step("Checking .gitignore...");
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

  registerMcpServer();
  registerHook();

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

  const python = checkPython();
  copySourceFiles();
  installPythonDeps(python, { strict: false });
  buildMcpServer();
  integrateClaudeMd({ replaceExisting: true });

  // Migrate MCP registration (update-only: remove user scope first)
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;
  if (claudeAvailable) {
    let spinner = step("Ensuring project-scoped MCP registration...");
    run("claude mcp remove rlm-navigator --scope user");
    const mcpServerPath = path.join(RLM_DIR, "server", "build", "index.js");
    const result = run(`claude mcp add rlm-navigator --scope project -- node "${mcpServerPath}"`);
    if (result.ok) {
      spinner.succeed("MCP registration updated");
    } else {
      spinner.warn("MCP registration may need manual update");
    }
  }

  registerHook();

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

  // 0. Shut down running daemon first
  let shutdownSpinner = step("Stopping running daemon...");
  shutdownDaemon();
  shutdownSpinner.succeed("Daemon stopped");

  // 1. Remove MCP registration
  const claudeAvailable = spawnSync("claude", ["--version"], { stdio: "pipe" }).status === 0;
  if (claudeAvailable) {
    let spinner = step("Removing MCP server registration...");
    run("claude mcp remove rlm-navigator --scope project");
    run("claude mcp remove rlm-navigator --scope user");
    spinner.succeed("MCP registration removed");
  }

  // 2. Remove session-end hook
  let spinner;
  spinner = step("Removing session-end hook...");
  try {
    uninstallHook();
    spinner.succeed("Session-end hook removed");
  } catch (err) {
    spinner.warn("Hook removal failed: " + err.message);
  }

  // 3. Remove .rlm/
  if (fs.existsSync(RLM_DIR)) {
    spinner = step("Removing .rlm/ directory...");
    fs.rmSync(RLM_DIR, { recursive: true, force: true });
    spinner.succeed(".rlm/ removed");
  } else {
    console.log(chalk.dim("  .rlm/ not found — nothing to remove."));
  }

  // 4. Remove .claude/skills/rlm-navigator/ and agent
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

  // 5. Remove CLAUDE.md snippet
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

  if (!fs.existsSync(RLM_DIR)) {
    console.log(`  Installed:   ${chalk.red("✖ No")}`);
    console.log("");
    console.log(chalk.dim(`  Run: npx rlm-navigator install`));
    console.log("");
    return;
  }
  console.log(`  Installed:   ${chalk.green("✔ Yes")}`);

  const lockFile = path.join(RLM_DIR, "daemon.lock");
  const portFile = path.join(RLM_DIR, "port");
  let port = null;
  let pid = null;
  let startedAt = null;

  if (fs.existsSync(lockFile)) {
    try {
      const data = JSON.parse(fs.readFileSync(lockFile, "utf-8"));
      port = data.port;
      pid = data.pid;
      startedAt = data.started_at;
    } catch {}
  }

  if (!port && fs.existsSync(portFile)) {
    try {
      const raw = fs.readFileSync(portFile, "utf-8").trim();
      const data = JSON.parse(raw);
      port = data.port;
      pid = pid || data.pid;
    } catch {
      port = parseInt(fs.readFileSync(portFile, "utf-8").trim(), 10);
    }
  }

  if (port) console.log(`  Port:        ${chalk.white(port)}`);
  else console.log(`  Port:        ${chalk.dim("unknown")}`);

  if (pid) {
    const alive = isPidAlive(pid);
    console.log(`  PID:         ${chalk.white(pid)} ${alive ? chalk.green("(alive)") : chalk.red("(dead)")}`);
    if (!alive) {
      for (const f of [lockFile, portFile]) {
        try { fs.unlinkSync(f); } catch {}
      }
      console.log(`  ${chalk.yellow("⚠ Cleaned stale port/lock files")}`);
      console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(was orphaned)")}`);
      console.log("");
      return;
    }
  }

  if (startedAt) console.log(`  Started:     ${chalk.dim(startedAt)}`);

  if (!port) {
    console.log(`  Daemon:      ${chalk.red("● OFFLINE")} ${chalk.dim("(no port file)")}`);
    console.log("");
    return;
  }

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
        console.log(`  Daemon:      ${chalk.yellow("● UNKNOWN")}`);
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
