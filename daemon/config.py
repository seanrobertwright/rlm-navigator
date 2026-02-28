"""Configuration for RLM Navigator — manages API keys, feature flags, and dependency detection."""

import os

# Try loading .env from project root if python-dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Sentinel for "not yet checked"
_UNCHECKED = object()


class RLMConfig:
    """Centralized configuration. Reads from environment variables."""

    def __init__(self):
        self.anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY")
        self.openai_api_key: str | None = os.environ.get("CHATGPT_API_KEY")
        self.pageindex_model: str = os.environ.get("PAGEINDEX_MODEL", "gpt-4o-2024-11-20")
        self._pageindex_available = _UNCHECKED
        self._anthropic_available = _UNCHECKED

    @property
    def enrichment_enabled(self) -> bool:
        """Haiku enrichment requires Anthropic key + SDK."""
        return self.anthropic_api_key is not None and self.anthropic_available

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
