import json
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
            # Fallback: enrichment_enabled is True when key is set
            assert cfg.enrichment_enabled is True
            assert cfg.enrichment_provider == "anthropic"
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
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_enabled is False
            assert cfg.enrichment_api_key is None
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old

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
