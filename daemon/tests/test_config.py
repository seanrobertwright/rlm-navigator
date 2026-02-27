import os
import importlib
import pytest


class TestConfig:
    def _fresh_config(self):
        """Import RLMConfig fresh (bypass module cache after env changes)."""
        import config
        importlib.reload(config)
        return config.RLMConfig()

    def test_config_loads_defaults(self):
        """Config should have sensible defaults when no env vars set."""
        old_vals = {}
        for key in ["ANTHROPIC_API_KEY", "CHATGPT_API_KEY", "PAGEINDEX_MODEL"]:
            old_vals[key] = os.environ.pop(key, None)

        try:
            cfg = self._fresh_config()
            assert cfg.anthropic_api_key is None
            assert cfg.openai_api_key is None
            assert cfg.pageindex_model == "gpt-4o-2024-11-20"
            # enrichment/doc_indexing depend on SDK availability AND key,
            # but with no key they should always be False
            assert cfg.enrichment_enabled is False
            assert cfg.doc_indexing_enabled is False
        finally:
            for key, val in old_vals.items():
                if val is not None:
                    os.environ[key] = val

    def test_config_detects_anthropic_key(self):
        """Config should detect Anthropic key from environment."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
        try:
            cfg = self._fresh_config()
            assert cfg.anthropic_api_key == "sk-ant-test-key"
            # enrichment_enabled depends on SDK being installed too
            if cfg.anthropic_available:
                assert cfg.enrichment_enabled is True
            else:
                assert cfg.enrichment_enabled is False
        finally:
            del os.environ["ANTHROPIC_API_KEY"]

    def test_config_detects_openai_key(self):
        """Config should detect OpenAI key from environment."""
        os.environ["CHATGPT_API_KEY"] = "sk-test-key"
        try:
            cfg = self._fresh_config()
            assert cfg.openai_api_key == "sk-test-key"
            # doc_indexing_enabled depends on pageindex being installed too
            if cfg.pageindex_available:
                assert cfg.doc_indexing_enabled is True
            else:
                assert cfg.doc_indexing_enabled is False
        finally:
            del os.environ["CHATGPT_API_KEY"]

    def test_pageindex_available(self):
        """Should detect whether pageindex is importable."""
        cfg = self._fresh_config()
        assert isinstance(cfg.pageindex_available, bool)

    def test_anthropic_available(self):
        """Should detect whether anthropic SDK is importable."""
        cfg = self._fresh_config()
        assert isinstance(cfg.anthropic_available, bool)

    def test_custom_pageindex_model(self):
        """Should pick up custom model from env."""
        os.environ["PAGEINDEX_MODEL"] = "gpt-4o-mini"
        try:
            cfg = self._fresh_config()
            assert cfg.pageindex_model == "gpt-4o-mini"
        finally:
            del os.environ["PAGEINDEX_MODEL"]
