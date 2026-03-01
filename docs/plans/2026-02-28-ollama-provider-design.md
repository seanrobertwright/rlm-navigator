# Ollama Enrichment Provider — Design Document

**Date**: 2026-02-28
**Status**: Approved

## Problem

The enrichment provider system supports Anthropic, OpenAI, and OpenRouter — all cloud APIs requiring API keys. Users running local models via Ollama have no way to use them for enrichment.

## Solution

Add Ollama as a fourth enrichment provider. During install, the CLI discovers locally available models by querying Ollama's HTTP API (with CLI fallback) and lets the user pick one. The daemon dispatches enrichment calls via the existing `openai` SDK pointed at Ollama's OpenAI-compatible endpoint. No API key required.

## Install Flow UX

Ollama appears as option 4 (before Skip):

```
  1) Anthropic   (Claude Haiku 4.5)
  2) OpenAI      (GPT-4o-mini, GPT-4o, GPT-4.1-mini)
  3) OpenRouter  (Multi-provider proxy)
  4) Ollama      (Local models)
  5) Skip        (No enrichment)
```

After selecting Ollama, the installer discovers local models dynamically:

```
  Ollama Models (locally available):
  1) llama3.2:latest
  2) qwen2.5-coder:7b
  3) mistral:latest
```

If no models are found: displays "No Ollama models detected. Install one with: ollama pull llama3.2" and skips enrichment config.

No API key instructions are shown for Ollama.

## Model Discovery

The CLI discovers models using two methods with fallback:

1. **HTTP API** (primary): `GET http://localhost:11434/api/tags` — returns JSON with `models[].name`. Requires Ollama server to be running.
2. **CLI fallback**: `ollama list` — parse stdout table rows, extract model name column. Works even if server isn't running but Ollama is installed.
3. **Both fail**: return empty array, show "no models detected" message.

## Config File Format

`.rlm/config.json`:

```json
{
  "enrichment": {
    "provider": "ollama",
    "model": "llama3.2:latest",
    "api_key_env": null
  }
}
```

`api_key_env` is `null` — Ollama requires no authentication.

## Daemon Changes

### `config.py`

`enrichment_enabled` currently requires both `enrichment_provider` and `enrichment_api_key` to be non-null. For Ollama, `api_key_env` is null so `enrichment_api_key` returns None. The check needs a special case:

```python
@property
def enrichment_enabled(self) -> bool:
    provider = self.enrichment_provider
    if provider == "ollama":
        return True  # Local, no API key needed
    return provider is not None and self.enrichment_api_key is not None
```

### `node_enricher.py`

Add an `"ollama"` branch in `call_enrichment_api()`. Ollama exposes an OpenAI-compatible API at `http://localhost:11434/v1`. Reuse the `openai` SDK with a dummy API key (the SDK requires a non-empty string, Ollama ignores it):

```python
OLLAMA_BASE_URL = "http://localhost:11434/v1"

elif provider == "ollama":
    client = _get_client("ollama", "ollama", OLLAMA_BASE_URL)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content
```

The base URL is hardcoded — users with remote Ollama instances can use the OpenAI provider with a custom setup.

## Files Changed

| File | Change |
|------|--------|
| `bin/cli.js` | Add Ollama to `ENRICHMENT_PROVIDERS`, `discoverOllamaModels()` function, skip API key instructions for Ollama |
| `daemon/config.py` | `enrichment_enabled` allows Ollama without API key |
| `daemon/node_enricher.py` | Add `"ollama"` branch in dispatch, `OLLAMA_BASE_URL` constant |
| `daemon/tests/test_config.py` | Test Ollama provider enables enrichment without API key |
| `daemon/tests/test_node_enricher.py` | Test Ollama dispatch uses correct base_url and dummy key |

No new dependencies — reuses the existing `openai` SDK.
