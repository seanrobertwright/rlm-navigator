# Ollama Enrichment Provider — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Ollama as a local enrichment provider with dynamic model discovery during install.

**Architecture:** Ollama uses the same `openai` SDK path as OpenRouter, pointed at `http://localhost:11434/v1` with a dummy API key. The CLI discovers local models via Ollama's HTTP API (fallback to `ollama list` CLI). Config stores `api_key_env: null`; `enrichment_enabled` gets a special case for Ollama.

**Tech Stack:** Node.js (http module for discovery, child_process fallback), Python (openai SDK), JSON config.

---

### Task 1: Update `config.py` — Ollama enables enrichment without API key

**Files:**
- Modify: `daemon/config.py:83-86`
- Test: `daemon/tests/test_config.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_config.py` at the end of `TestConfigFile` (after `test_openrouter_provider`):

```python
    def test_ollama_provider_no_api_key(self, tmp_path):
        """Ollama provider should enable enrichment without an API key."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "ollama",
                "model": "llama3.2:latest",
                "api_key_env": None
            }
        }))
        from config import RLMConfig
        cfg = RLMConfig(root=str(tmp_path))
        assert cfg.enrichment_provider == "ollama"
        assert cfg.enrichment_model == "llama3.2:latest"
        assert cfg.enrichment_api_key is None
        assert cfg.enrichment_enabled is True
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_config.py::TestConfigFile::test_ollama_provider_no_api_key -v`
Expected: FAIL — `enrichment_enabled` returns `False` because `enrichment_api_key` is `None`.

**Step 3: Implement the config change**

In `daemon/config.py`, replace lines 83-86:

```python
    @property
    def enrichment_enabled(self) -> bool:
        """Enrichment requires a provider and a resolved API key."""
        return self.enrichment_provider is not None and self.enrichment_api_key is not None
```

With:

```python
    @property
    def enrichment_enabled(self) -> bool:
        """Enrichment requires a provider and a resolved API key (except Ollama)."""
        provider = self.enrichment_provider
        if provider == "ollama":
            return True  # Local, no API key needed
        return provider is not None and self.enrichment_api_key is not None
```

**Step 4: Run tests to verify they pass**

Run: `cd daemon && python -m pytest tests/test_config.py -v`
Expected: All 12 tests PASS (6 TestConfig + 6 TestConfigFile).

**Step 5: Commit**

```bash
git add daemon/config.py daemon/tests/test_config.py
git commit -m "feat: config allows Ollama provider without API key"
```

---

### Task 2: Update `node_enricher.py` — Ollama dispatch branch

**Files:**
- Modify: `daemon/node_enricher.py:18,49-77`
- Test: `daemon/tests/test_node_enricher.py`

**Step 1: Write the failing test**

Add to `daemon/tests/test_node_enricher.py` at the end of `TestCallEnrichmentApi` (after `test_unknown_provider_returns_none`):

```python
    def test_ollama_dispatch(self):
        """Should call openai SDK with Ollama base_url and dummy key."""
        from unittest.mock import MagicMock, patch
        import node_enricher
        from node_enricher import call_enrichment_api

        mock_config = MagicMock()
        mock_config.enrichment_provider = "ollama"
        mock_config.enrichment_model = "llama3.2:latest"
        mock_config.enrichment_api_key = None

        mock_choice = MagicMock()
        mock_choice.message.content = '{"foo": "bar"}'
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_sdk = MagicMock()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_sdk.OpenAI.return_value = mock_client

        with patch.dict(node_enricher._sdk_cache, {"openai": mock_sdk}):
            result = call_enrichment_api("test prompt", mock_config)
            assert result == '{"foo": "bar"}'
            mock_sdk.OpenAI.assert_called_once_with(
                api_key="ollama",
                base_url="http://localhost:11434/v1",
            )
            mock_client.chat.completions.create.assert_called_once_with(
                model="llama3.2:latest",
                max_tokens=1024,
                messages=[{"role": "user", "content": "test prompt"}],
            )
```

**Step 2: Run test to verify it fails**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py::TestCallEnrichmentApi::test_ollama_dispatch -v`
Expected: FAIL — no `"ollama"` branch in `call_enrichment_api`, returns `None`.

**Step 3: Implement the dispatch**

In `daemon/node_enricher.py`:

3a. Add after `OPENROUTER_BASE_URL` (line 18):

```python
OLLAMA_BASE_URL = "http://localhost:11434/v1"
```

3b. In `call_enrichment_api()`, change the early guard (line 55) from:

```python
    if not provider or not api_key or not model:
        return None
```

To:

```python
    if not provider or not model:
        return None
    if not api_key and provider != "ollama":
        return None
```

3c. Add a new `elif` branch after the `openai`/`openrouter` branch (after line 75, before `return None`):

```python
    elif provider == "ollama":
        client = _get_client("ollama", "ollama", OLLAMA_BASE_URL)
        response = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
```

**Step 4: Run tests to verify they pass**

Run: `cd daemon && python -m pytest tests/test_node_enricher.py -v`
Expected: All 19 tests PASS (13 existing + 5 dispatch + 1 new Ollama).

**Step 5: Commit**

```bash
git add daemon/node_enricher.py daemon/tests/test_node_enricher.py
git commit -m "feat: add Ollama dispatch branch in enricher"
```

---

### Task 3: Update `bin/cli.js` — Ollama provider with dynamic model discovery

**Files:**
- Modify: `bin/cli.js`

**Step 1: Add `http` require**

At `bin/cli.js` line 7, add after the `net` require:

```javascript
const http = require("http");
```

**Step 2: Add `discoverOllamaModels()` function**

Add after `writeEnrichmentConfig()` (after line 155):

```javascript
function discoverOllamaModels() {
  return new Promise((resolve) => {
    // Try HTTP API first (requires Ollama server running)
    const req = http.get("http://localhost:11434/api/tags", { timeout: 3000 }, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => {
        try {
          const parsed = JSON.parse(data);
          const models = (parsed.models || []).map((m) => m.name || m.model).filter(Boolean);
          if (models.length > 0) return resolve(models);
        } catch {}
        resolve(discoverOllamaModelsCli());
      });
    });
    req.on("error", () => resolve(discoverOllamaModelsCli()));
    req.on("timeout", () => { req.destroy(); resolve(discoverOllamaModelsCli()); });
  });
}

function discoverOllamaModelsCli() {
  try {
    const output = execSync("ollama list", { encoding: "utf-8", timeout: 5000 });
    const lines = output.trim().split("\n").slice(1); // skip header row
    return lines.map((line) => line.split(/\s+/)[0]).filter(Boolean);
  } catch {
    return [];
  }
}
```

**Step 3: Add Ollama to `ENRICHMENT_PROVIDERS`**

In the `ENRICHMENT_PROVIDERS` array, add after the OpenRouter entry (before the closing `];`):

```javascript
  {
    name: "Ollama",
    key: "ollama",
    desc: "Local models",
    api_key_env: null,
    models: null,  // discovered dynamically
  },
```

**Step 4: Update `configureEnrichment()` to handle Ollama's dynamic models**

Replace the model selection block (lines 116-128) and API key instructions block (lines 130-136) with logic that branches on whether the provider has static models or needs discovery:

Replace from line 114 (`const provider = ENRICHMENT_PROVIDERS[providerIdx];`) through line 141 (`api_key_env: provider.api_key_env,`) with:

```javascript
  const provider = ENRICHMENT_PROVIDERS[providerIdx];
  let modelId;

  if (provider.models === null) {
    // Dynamic model discovery (Ollama)
    let discoverSpinner = step("Discovering local Ollama models...");
    const discovered = await discoverOllamaModels();
    discoverSpinner.stop();

    if (discovered.length === 0) {
      console.log(chalk.yellow("  No Ollama models detected."));
      console.log(chalk.dim("  Install one with: ") + chalk.cyan("ollama pull llama3.2"));
      console.log(chalk.dim("  Then re-run: ") + chalk.cyan("npx rlm-navigator install"));
      console.log("");
      return null;
    }

    console.log("");
    console.log(chalk.bold(`  Ollama Models (locally available):`));
    for (let i = 0; i < discovered.length; i++) {
      console.log(`  ${chalk.cyan(i + 1 + ")")} ${discovered[i]}`);
    }
    console.log("");

    const modelAnswer = await ask(chalk.cyan("  ? ") + "Select model " + chalk.dim(`[1-${discovered.length}] `));
    const modelIdx = parseInt(modelAnswer, 10) - 1;
    modelId = (modelIdx >= 0 && modelIdx < discovered.length)
      ? discovered[modelIdx]
      : discovered[0];
  } else {
    // Static model list (Anthropic, OpenAI, OpenRouter)
    console.log("");
    console.log(chalk.bold(`  ${provider.name} Models:`));
    for (let i = 0; i < provider.models.length; i++) {
      console.log(`  ${chalk.cyan(i + 1 + ")")} ${provider.models[i].label}`);
    }
    console.log("");

    const modelAnswer = await ask(chalk.cyan("  ? ") + "Select model " + chalk.dim(`[1-${provider.models.length}] `));
    const modelIdx = parseInt(modelAnswer, 10) - 1;
    const model = (modelIdx >= 0 && modelIdx < provider.models.length)
      ? provider.models[modelIdx]
      : provider.models[0];
    modelId = model.id;
  }

  // API key instructions (skip for providers that don't need one)
  if (provider.api_key_env) {
    console.log("");
    const instructions = apiKeyInstructions(provider.api_key_env);
    for (const line of instructions) {
      console.log(line);
    }
    console.log("");
  }

  return {
    provider: provider.key,
    model: modelId,
    api_key_env: provider.api_key_env,
```

**Step 5: Build to verify no syntax errors**

Run: `cd server && npm run build`
Expected: Clean build.

**Step 6: Commit**

```bash
git add bin/cli.js
git commit -m "feat: add Ollama provider with dynamic model discovery to CLI install"
```

---

### Task 4: Full verification

**Files:** None (verification only)

**Step 1: Run all daemon tests**

Run: `cd daemon && python -m pytest tests/ -v`
Expected: All tests PASS (205+).

**Step 2: Build MCP server**

Run: `cd server && npm run build`
Expected: Clean build, no errors.

**Step 3: Verify commit history**

```bash
git log --oneline -4
```

Expected: 3 feature commits from tasks 1-3 plus design doc.
