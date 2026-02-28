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
