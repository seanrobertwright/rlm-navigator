# Enrichment Provider Selection — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users choose Anthropic, OpenAI, or OpenRouter as their enrichment provider during install, with model selection and OS-specific API key instructions.

**Architecture:** `.rlm/config.json` stores provider/model/env-var-name. `daemon/config.py` reads it (falls back to legacy env var check). `daemon/node_enricher.py` dispatches to the correct SDK via `call_enrichment_api()`. `bin/cli.js` drives the interactive selection during install.

**Tech Stack:** Node.js (CLI prompts, chalk, ora), Python (anthropic SDK, openai SDK), JSON config file.

---

### Task 1: Update `config.py` to read `.rlm/config.json`

**Files:**
- Modify: `daemon/config.py`
- Test: `daemon/tests/test_config.py`

**Step 1: Write the failing tests**

Add to `daemon/tests/test_config.py`:

```python
class TestConfigFile:
    """Tests for .rlm/config.json loading."""

    def test_loads_enrichment_from_config_file(self, tmp_path):
        """Config file should set provider, model, and resolve API key."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY"
            }
        }))
        os.environ["OPENAI_API_KEY"] = "sk-test-openai"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_provider == "openai"
            assert cfg.enrichment_model == "gpt-4o-mini"
            assert cfg.enrichment_api_key == "sk-test-openai"
            assert cfg.enrichment_enabled is True
        finally:
            del os.environ["OPENAI_API_KEY"]

    def test_missing_config_file_falls_back(self, tmp_path):
        """Without config file, should fall back to ANTHROPIC_API_KEY env var."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-fallback"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_provider == "anthropic"
            assert cfg.enrichment_model == "claude-haiku-4-5-20251001"
            assert cfg.enrichment_api_key == "sk-ant-fallback"
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_missing_api_key_disables_enrichment(self, tmp_path):
        """Config file present but env var not set -> enrichment disabled."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY"
            }
        }))
        old = os.environ.pop("OPENAI_API_KEY", None)
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_enabled is False
            assert cfg.enrichment_api_key is None
        finally:
            if old is not None:
                os.environ["OPENAI_API_KEY"] = old

    def test_skip_provider_disables_enrichment(self, tmp_path):
        """Provider set to null means enrichment is disabled."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": None,
                "model": None,
                "api_key_env": None
            }
        }))
        from config import RLMConfig
        cfg = RLMConfig(root=str(tmp_path))
        assert cfg.enrichment_enabled is False

    def test_openrouter_provider(self, tmp_path):
        """OpenRouter provider should work with its own env var."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "openrouter",
                "model": "anthropic/claude-haiku-4-5",
                "api_key_env": "OPENROUTER_API_KEY"
            }
        }))
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_provider == "openrouter"
            assert cfg.enrichment_model == "anthropic/claude-haiku-4-5"
            assert cfg.enrichment_api_key == "sk-or-test"
            assert cfg.enrichment_enabled is True
        finally:
            del os.environ["OPENROUTER_API_KEY"]
```

Add `import json` to the top of the test file.

**Step 2: Run tests to verify they fail**

Run: `cd daemon && python -m pytest tests/test_config.py::TestConfigFile -v`
Expected: FAIL — `RLMConfig()` doesn't accept `root=` param, no `enrichment_provider` property.

**Step 3: Implement config.py changes**

Replace `daemon/config.py` with:

```python
"""Configuration for RLM Navigator — manages API keys, feature flags, and dependency detection."""

import json
import os

# Try loading .env from project root if python-dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Sentinel for "not yet checked"
_UNCHECKED = object()

# Fallback defaults when no config file exists
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


class RLMConfig:
    """Centralized configuration. Reads .rlm/config.json then falls back to env vars."""

    def __init__(self, root: str = "."):
        self._root = root
        # Legacy env vars (kept for backward compat + doc indexing)
        self.anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY")
        self.openai_api_key: str | None = os.environ.get("CHATGPT_API_KEY")
        self.pageindex_model: str = os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20")
        self._pageindex_available = _UNCHECKED
        self._anthropic_available = _UNCHECKED
        # Enrichment config from .rlm/config.json
        self._enrichment_provider: str | None = None
        self._enrichment_model: str | None = None
        self._enrichment_api_key_env: str | None = None
        self._config_loaded = False
        self._load_rlm_config()

    def _load_rlm_config(self):
        """Read .rlm/config.json for enrichment provider settings."""
        config_path = os.path.join(self._root, ".rlm", "config.json")
        if not os.path.isfile(config_path):
            return
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        enrichment = data.get("enrichment")
        if not isinstance(enrichment, dict):
            return
        self._enrichment_provider = enrichment.get("provider")
        self._enrichment_model = enrichment.get("model")
        self._enrichment_api_key_env = enrichment.get("api_key_env")
        self._config_loaded = True

    @property
    def enrichment_provider(self) -> str | None:
        if self._config_loaded:
            return self._enrichment_provider
        # Fallback: if ANTHROPIC_API_KEY is set, assume anthropic
        if self.anthropic_api_key:
            return "anthropic"
        return None

    @property
    def enrichment_model(self) -> str | None:
        if self._config_loaded:
            return self._enrichment_model
        # Fallback default
        if self.anthropic_api_key:
            return _DEFAULT_ANTHROPIC_MODEL
        return None

    @property
    def enrichment_api_key(self) -> str | None:
        if self._config_loaded and self._enrichment_api_key_env:
            return os.environ.get(self._enrichment_api_key_env)
        # Fallback
        return self.anthropic_api_key

    @property
    def enrichment_enabled(self) -> bool:
        """Enrichment requires a provider and a resolved API key."""
        return self.enrichment_provider is not None and self.enrichment_api_key is not None

    @property
    def doc_indexing_enabled(self) -> bool:
        """Document indexing requires OpenAI key + PageIndex library."""
        return self.openai_api_key is not None and self.pageindex_available

    @property
    def pageindex_available(self) -> bool:
        if self._pageindex_available is _UNCHECKED:
            try:
                import pageindex  # noqa: F401
                self._pageindex_available = True
            except ImportError:
                self._pageindex_available = False
        return self._pageindex_available

    @property
    def anthropic_available(self) -> bool:
        if self._anthropic_available is _UNCHECKED:
            try:
                import anthropic  # noqa: F401
                self._anthropic_available = True
            except ImportError:
                self._anthropic_available = False
        return self._anthropic_available
```

**Step 4: Run tests to verify they pass**

Run: `cd daemon && python -m pytest tests/test_config.py -v`
Expected: All `TestConfig` and `TestConfigFile` tests PASS.

**Step 5: Commit**

```bash
git add daemon/config.py daemon/tests/test_config.py
git commit -m "feat: config.py reads .rlm/config.json for enrichment provider"
```

---

### Task 2: Update `node_enricher.py` with provider dispatch

**Files:**
- Modify: `daemon/node_enricher.py`
- Test: `daemon/tests/test_node_enricher.py`

**Step 1: Write the failing tests**

Add to `daemon/tests/test_node_enricher.py`:

```python
class TestCallEnrichmentApi:
    """Tests for multi-provider dispatch."""

    def test_anthropic_dispatch(self):
        """Should call anthropic SDK for anthropic provider."""
        from unittest.mock import MagicMock, patch
        from node_enricher import call_enrichment_api

        mock_config = MagicMock()
        mock_config.enrichment_provider = "anthropic"
        mock_config.enrichment_model = "claude-haiku-4-5-20251001"
        mock_config.enrichment_api_key = "sk-ant-test"

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"foo": "bar"}')]

        with patch("node_enricher.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.return_value = mock_response
            mock_anthropic.Anthropic.return_value = mock_client

            result = call_enrichment_api("test prompt", mock_config)
            assert result == '{"foo": "bar"}'
            mock_anthropic.Anthropic.assert_called_once_with(api_key="sk-ant-test")
            mock_client.messages.create.assert_called_once_with(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": "test prompt"}],
            )

    def test_openai_dispatch(self):
        """Should call openai SDK for openai provider."""
        from unittest.mock import MagicMock, patch
        from node_enricher import call_enrichment_api

        mock_config = MagicMock()
        mock_config.enrichment_provider = "openai"
        mock_config.enrichment_model = "gpt-4o-mini"
        mock_config.enrichment_api_key = "sk-test"

        mock_choice = MagicMock()
        mock_choice.message.content = '{"foo": "bar"}'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch("node_enricher.openai") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.OpenAI.return_value = mock_client

            result = call_enrichment_api("test prompt", mock_config)
            assert result == '{"foo": "bar"}'
            mock_openai.OpenAI.assert_called_once_with(api_key="sk-test", base_url=None)

    def test_openrouter_dispatch(self):
        """Should call openai SDK with OpenRouter base_url."""
        from unittest.mock import MagicMock, patch
        from node_enricher import call_enrichment_api

        mock_config = MagicMock()
        mock_config.enrichment_provider = "openrouter"
        mock_config.enrichment_model = "anthropic/claude-haiku-4-5"
        mock_config.enrichment_api_key = "sk-or-test"

        mock_choice = MagicMock()
        mock_choice.message.content = '{"foo": "bar"}'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch("node_enricher.openai") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.OpenAI.return_value = mock_client

            result = call_enrichment_api("test prompt", mock_config)
            assert result == '{"foo": "bar"}'
            mock_openai.OpenAI.assert_called_once_with(
                api_key="sk-or-test",
                base_url="https://openrouter.ai/api/v1",
            )

    def test_none_provider_returns_none(self):
        """Should return None for unknown/null provider."""
        from unittest.mock import MagicMock
        from node_enricher import call_enrichment_api

        mock_config = MagicMock()
        mock_config.enrichment_provider = None
        assert call_enrichment_api("test", mock_config) is None
```

**Step 2: Run tests to verify they fail**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py::TestCallEnrichmentApi -v`
Expected: FAIL — `call_enrichment_api` not defined.

**Step 3: Implement the dispatch function and update callers**

In `daemon/node_enricher.py`, add imports at top level (lazy) and the dispatch function. Then update `enrich_file()` and `EnrichmentWorker.process_one()`.

Add after the existing imports:

```python
# Lazy-loaded SDK modules (set when first used)
anthropic = None
openai = None

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def call_enrichment_api(prompt: str, config) -> Optional[str]:
    """Dispatch enrichment call to the configured provider. Returns raw text or None."""
    global anthropic, openai
    provider = config.enrichment_provider
    api_key = config.enrichment_api_key
    model = config.enrichment_model

    if not provider or not api_key or not model:
        return None

    if provider == "anthropic":
        if anthropic is None:
            import anthropic as _anthropic
            anthropic = _anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    elif provider in ("openai", "openrouter"):
        if openai is None:
            import openai as _openai
            openai = _openai
        base_url = OPENROUTER_BASE_URL if provider == "openrouter" else None
        client = openai.OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    return None
```

Update `enrich_file()` (replace lines 136-150) to use the dispatch:

```python
async def enrich_file(file_path: str, skeleton: str, config) -> Optional[dict]:
    """Call enrichment API to generate enrichments for a file's skeleton.

    Returns dict mapping symbol names to summaries, or None on failure.
    """
    if not config or not config.enrichment_enabled:
        return None

    symbols = parse_skeleton_symbols(skeleton)
    if not symbols:
        return None

    prompt = build_enrichment_prompt(file_path, symbols)

    try:
        text = call_enrichment_api(prompt, config)
        if text is None:
            return None
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except Exception:
        return None
```

Update `EnrichmentWorker.process_one()` (replace lines 190-210) to use the dispatch:

```python
    def process_one(self) -> bool:
        """Process one item from the queue. Returns True if an item was processed."""
        try:
            file_path, skeleton, mtime = self._queue.get_nowait()
        except queue.Empty:
            return False

        if not self._config or not getattr(self._config, 'enrichment_enabled', False):
            return True

        symbols = parse_skeleton_symbols(skeleton)
        if not symbols:
            return True

        prompt = build_enrichment_prompt(file_path, symbols)

        try:
            text = call_enrichment_api(prompt, self._config)
            if text is None:
                return True
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            enrichments = json.loads(text)
            self._cache.put(file_path, mtime, enrichments)
        except Exception:
            pass  # Enrichment is best-effort

        return True
```

**Step 4: Run tests to verify they pass**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py -v`
Expected: All tests PASS (existing + new `TestCallEnrichmentApi`).

**Step 5: Commit**

```bash
git add daemon/node_enricher.py daemon/tests/test_node_enricher.py
git commit -m "feat: multi-provider dispatch in node_enricher"
```

---

### Task 3: Pass `root` to `RLMConfig` in `rlm_daemon.py`

**Files:**
- Modify: `daemon/rlm_daemon.py`

**Step 1: Find all `RLMConfig()` instantiations**

There are 3 occurrences at lines 654, 764, and 785. Each is inside `handle_request(data, cache, root, ...)` where `root` is available as a parameter.

**Step 2: Update each call**

Change each `cfg = RLMConfig()` to `cfg = RLMConfig(root=root)`.

Line 654:
```python
        from config import RLMConfig
        cfg = RLMConfig(root=root)
```

Line 764:
```python
        from config import RLMConfig
        cfg = RLMConfig(root=root)
```

Line 785:
```python
        from config import RLMConfig
        cfg = RLMConfig(root=root)
```

**Step 3: Run full test suite**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All 194+ tests PASS.

**Step 4: Commit**

```bash
git add daemon/rlm_daemon.py
git commit -m "feat: pass project root to RLMConfig for config file discovery"
```

---

### Task 4: Add `openai` to `requirements.txt`

**Files:**
- Modify: `daemon/requirements.txt`

**Step 1: Add the dependency**

Add after the `anthropic` line:

```
openai>=1.0
```

**Step 2: Commit**

```bash
git add daemon/requirements.txt
git commit -m "feat: add openai SDK dependency for multi-provider enrichment"
```

---

### Task 5: Add provider/model selection to CLI install

**Files:**
- Modify: `bin/cli.js`

**Step 1: Add provider/model data constants**

Add after the `BANNER` constant (around line 33):

```javascript
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
```

**Step 2: Add the `configureEnrichment()` function**

Add after `apiKeyInstructions`:

```javascript
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
```

**Step 3: Add `writeEnrichmentConfig()` helper**

```javascript
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
```

**Step 4: Wire into the `install()` function**

In the `install()` function, after `buildMcpServer()` (line 410) and before the `.gitignore prompt` (line 413), add:

```javascript
  // Enrichment provider selection
  const enrichment = await configureEnrichment();
  let enrichSpinner = step("Saving enrichment configuration...");
  writeEnrichmentConfig(enrichment);
  if (enrichment) {
    enrichSpinner.succeed(`Enrichment: ${enrichment.provider} / ${enrichment.model}`);
  } else {
    enrichSpinner.succeed("Enrichment: skipped");
  }
```

**Step 5: Test manually**

Run: `node bin/cli.js install` in a test directory. Verify:
- Provider selection prompt appears
- Model selection prompt appears after picking 1-3
- OS-specific API key instructions display
- `.rlm/config.json` is written with correct values
- Selecting "4" (skip) writes `provider: null`

**Step 6: Commit**

```bash
git add bin/cli.js
git commit -m "feat: add enrichment provider/model selection to CLI install"
```

---

### Task 6: Update existing config tests for backward compat

**Files:**
- Modify: `daemon/tests/test_config.py`

**Step 1: Verify existing tests still pass with new `root` param**

The existing `TestConfig._fresh_config()` calls `RLMConfig()` with no args. Since `root` defaults to `"."`, this should still work. But the `enrichment_enabled` property logic changed — it no longer checks `anthropic_available`, just checks if `enrichment_api_key` is non-None.

**Step 2: Update `test_config_detects_anthropic_key`**

The test currently checks `cfg.anthropic_available` to decide if enrichment is enabled. With the new fallback logic, if `ANTHROPIC_API_KEY` is set, `enrichment_enabled` is True regardless of SDK availability (the SDK check moved to the enricher itself). Update:

```python
    def test_config_detects_anthropic_key(self):
        """Config should detect Anthropic key from environment."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
        try:
            cfg = self._fresh_config()
            assert cfg.anthropic_api_key == "sk-ant-test-key"
            # Fallback: enrichment_enabled is True when key is set
            assert cfg.enrichment_enabled is True
            assert cfg.enrichment_provider == "anthropic"
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
```

**Step 3: Run full test suite**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All tests PASS.

**Step 4: Commit**

```bash
git add daemon/tests/test_config.py
git commit -m "test: update config tests for new enrichment provider fallback"
```

---

### Task 7: Run full verification

**Files:** None (verification only)

**Step 1: Run all daemon tests**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All tests PASS.

**Step 2: Build MCP server**

Run: `cd server && npm run build`
Expected: Clean build, no errors.

**Step 3: Final commit — squash or tag**

```bash
git log --oneline -7
```

Verify all 6 commits from tasks 1-6 are present and correct.
