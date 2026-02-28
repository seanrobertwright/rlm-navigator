# Enrichment Provider Selection — Design Document

**Date**: 2026-02-28
**Status**: Approved

## Problem

The enrichment system (Haiku-powered semantic summaries on AST skeletons) currently hardcodes the Anthropic SDK and `claude-haiku-4-5-20251001`. Users who want enrichment must have an `ANTHROPIC_API_KEY`, which is a separate billing mechanism from their Claude Code subscription. There's no way to use alternative providers like OpenAI or OpenRouter.

## Solution

Add provider/model selection to the install flow. Users pick from Anthropic, OpenAI, or OpenRouter during `npx rlm-navigator install`, select a model, and receive OS-specific instructions for setting their API key. The choice is saved to `.rlm/config.json`. The daemon reads this config and dispatches enrichment calls to the correct SDK.

## Approach

**Provider-specific SDK calls (Approach B)**: The `anthropic` SDK handles Anthropic direct. The `openai` SDK handles both OpenAI and OpenRouter (same format, different `base_url`). Two clean code paths, native API usage for each.

## Install Flow UX

After Python deps and MCP server build, the installer presents:

```
  ┌─ Enrichment Provider ─────────────────────────────┐
  │                                                    │
  │  Enrichment adds semantic summaries to code        │
  │  skeletons using a small LLM. Requires an API key. │
  │                                                    │
  │  Select a provider:                                │
  │                                                    │
  │  1) Anthropic   (Claude Haiku 4.5)                 │
  │  2) OpenAI      (GPT-4o-mini, GPT-4o, GPT-4.1-mini)│
  │  3) OpenRouter  (Multi-provider proxy)             │
  │  4) Skip        (No enrichment)                    │
  │                                                    │
  └────────────────────────────────────────────────────┘
```

After selecting a provider (1-3), the user picks a model from a curated list. Then OS-specific instructions for setting the API key are displayed:

**Windows (PowerShell)**:
```
$env:ANTHROPIC_API_KEY = "sk-ant-..."                                          # current session
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")  # permanent
```

**macOS/Linux (bash/zsh)**:
```
export ANTHROPIC_API_KEY="sk-ant-..."                  # current session
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.bashrc  # permanent
```

The API key is **never** stored on disk.

## Config File Format

`.rlm/config.json`:

```json
{
  "enrichment": {
    "provider": "anthropic",
    "model": "claude-haiku-4-5-20251001",
    "api_key_env": "ANTHROPIC_API_KEY"
  }
}
```

| Field | Values | Purpose |
|-------|--------|---------|
| `provider` | `"anthropic"`, `"openai"`, `"openrouter"`, `null` | Which SDK to use |
| `model` | Model ID string | Passed directly to the SDK |
| `api_key_env` | Env var name | Which env var the daemon reads for the API key |

### Provider-to-env-var mapping

| Provider | `api_key_env` |
|----------|---------------|
| Anthropic | `ANTHROPIC_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |

### Model Options

| Provider | Models | Default |
|----------|--------|---------|
| Anthropic | `claude-haiku-4-5-20251001`, `claude-sonnet-4-5-20250514` | `claude-haiku-4-5-20251001` |
| OpenAI | `gpt-4o-mini`, `gpt-4o`, `gpt-4.1-mini` | `gpt-4o-mini` |
| OpenRouter | `anthropic/claude-haiku-4-5`, `openai/gpt-4o-mini`, `google/gemini-2.0-flash`, `meta-llama/llama-3.3-70b-instruct` | `anthropic/claude-haiku-4-5` |

## Daemon Config Changes (`config.py`)

`RLMConfig` gains a `root` parameter and reads `.rlm/config.json`:

- `.rlm/config.json` is the **primary** config source
- If the file doesn't exist or `enrichment` is null/missing, fall back to current behavior (check `ANTHROPIC_API_KEY` env var directly)
- New properties: `enrichment_provider`, `enrichment_model`, `enrichment_api_key`
- `enrichment_enabled` checks: provider set + API key env var resolves to a non-empty value
- Existing `anthropic_api_key` / `anthropic_available` properties remain for backward compatibility

## Enricher Dispatch (`node_enricher.py`)

A new `call_enrichment_api(prompt, config)` function dispatches to the correct SDK:

- **Anthropic**: `anthropic.Anthropic(api_key=...).messages.create(...)`
- **OpenAI**: `openai.OpenAI(api_key=...).chat.completions.create(...)`
- **OpenRouter**: `openai.OpenAI(api_key=..., base_url="https://openrouter.ai/api/v1").chat.completions.create(...)`

Both `enrich_file()` and `EnrichmentWorker.process_one()` call this function instead of inline SDK code. Error handling remains best-effort (catch-all `except Exception`).

## Files Changed

| File | Change |
|------|--------|
| `bin/cli.js` | Add provider/model selection prompts, write `.rlm/config.json`, show OS-specific API key instructions |
| `daemon/config.py` | Accept `root` param, read `.rlm/config.json`, new enrichment properties, backward compat |
| `daemon/node_enricher.py` | Extract `call_enrichment_api()` dispatch, update `enrich_file()` and `EnrichmentWorker.process_one()` |
| `daemon/rlm_daemon.py` | Pass `root` to `RLMConfig(root)` |
| `daemon/requirements.txt` | Add `openai` (optional, needed for openai/openrouter providers) |
| `daemon/tests/test_config.py` | Test config file loading, provider resolution, fallback behavior |
| `daemon/tests/test_node_enricher.py` | Test `call_enrichment_api` dispatch for each provider (mocked) |

No changes to the MCP server (`server/src/index.ts`) — it already displays `enrichment_available` from the daemon status.
