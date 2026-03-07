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
        for key in ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CHATGPT_API_KEY", "PAGEINDEX_MODEL"]:
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

    def test_anthropic_auth_token_enables_enrichment(self):
        """ANTHROPIC_AUTH_TOKEN alone should enable enrichment."""
        old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "token-test-123"
        try:
            cfg = self._fresh_config()
            assert cfg.enrichment_enabled is True
            assert cfg.enrichment_provider == "anthropic"
            assert cfg.enrichment_api_key == "token-test-123"
        finally:
            del os.environ["ANTHROPIC_AUTH_TOKEN"]
            if old_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = old_key

    def test_anthropic_auth_token_takes_precedence(self):
        """When both ANTHROPIC_AUTH_TOKEN and ANTHROPIC_API_KEY are set, token wins."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api-key"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "token-preferred"
        try:
            cfg = self._fresh_config()
            assert cfg.enrichment_api_key == "token-preferred"
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
            del os.environ["ANTHROPIC_AUTH_TOKEN"]

    def test_fallback_to_api_key_when_no_auth_token(self):
        """Without auth token, should fall back to ANTHROPIC_API_KEY."""
        old_token = os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-fallback-key"
        try:
            cfg = self._fresh_config()
            assert cfg.enrichment_api_key == "sk-ant-fallback-key"
            assert cfg.enrichment_enabled is True
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
            if old_token is not None:
                os.environ["ANTHROPIC_AUTH_TOKEN"] = old_token


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

    def test_anthropic_auth_token_with_config_file(self, tmp_path):
        """Auth token should work when config file specifies anthropic provider."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key_env": "ANTHROPIC_API_KEY"
            }
        }))
        old_key = os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "token-from-config"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_provider == "anthropic"
            assert cfg.enrichment_api_key == "token-from-config"
            assert cfg.enrichment_enabled is True
        finally:
            del os.environ["ANTHROPIC_AUTH_TOKEN"]
            if old_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = old_key

    def test_anthropic_auth_token_precedence_with_config_file(self, tmp_path):
        """Auth token should take precedence over API key even with config file."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "anthropic",
                "model": "claude-haiku-4-5-20251001",
                "api_key_env": "ANTHROPIC_API_KEY"
            }
        }))
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-config-key"
        os.environ["ANTHROPIC_AUTH_TOKEN"] = "token-wins"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_api_key == "token-wins"
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
            del os.environ["ANTHROPIC_AUTH_TOKEN"]

    def test_openai_auth_token_enables_enrichment(self, tmp_path):
        """OPENAI_AUTH_TOKEN should enable enrichment for openai provider."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY"
            }
        }))
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        old_token = os.environ.pop("OPENAI_AUTH_TOKEN", None)
        os.environ["OPENAI_AUTH_TOKEN"] = "token-test-123"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_provider == "openai"
            assert cfg.enrichment_enabled is True
            assert cfg.enrichment_api_key == "token-test-123"
        finally:
            del os.environ["OPENAI_AUTH_TOKEN"]
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key
            if old_token is not None:
                os.environ["OPENAI_AUTH_TOKEN"] = old_token

    def test_openai_auth_token_takes_precedence(self, tmp_path):
        """When both OPENAI_AUTH_TOKEN and OPENAI_API_KEY are set, auth token wins."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY"
            }
        }))
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        old_token = os.environ.pop("OPENAI_AUTH_TOKEN", None)
        os.environ["OPENAI_API_KEY"] = "sk-api-key"
        os.environ["OPENAI_AUTH_TOKEN"] = "token-wins"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_api_key == "token-wins"
            assert cfg.enrichment_enabled is True
        finally:
            del os.environ["OPENAI_API_KEY"]
            del os.environ["OPENAI_AUTH_TOKEN"]
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key
            if old_token is not None:
                os.environ["OPENAI_AUTH_TOKEN"] = old_token

    def test_openai_auth_token_no_effect_on_other_providers(self, tmp_path):
        """OPENAI_AUTH_TOKEN should not affect non-openai providers."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "openrouter",
                "model": "anthropic/claude-haiku-4-5",
                "api_key_env": "OPENROUTER_API_KEY"
            }
        }))
        old_or = os.environ.pop("OPENROUTER_API_KEY", None)
        old_token = os.environ.pop("OPENAI_AUTH_TOKEN", None)
        os.environ["OPENAI_AUTH_TOKEN"] = "token-should-not-apply"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_enabled is False
            assert cfg.enrichment_api_key is None
        finally:
            del os.environ["OPENAI_AUTH_TOKEN"]
            if old_or is not None:
                os.environ["OPENROUTER_API_KEY"] = old_or
            if old_token is not None:
                os.environ["OPENAI_AUTH_TOKEN"] = old_token

    def test_gemini_provider(self, tmp_path):
        """Gemini provider should work with GEMINI_API_KEY env var."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()
        (rlm_dir / "config.json").write_text(json.dumps({
            "enrichment": {
                "provider": "gemini",
                "model": "gemini-2.0-flash",
                "api_key_env": "GEMINI_API_KEY"
            }
        }))
        os.environ["GEMINI_API_KEY"] = "gemini-test-key"
        try:
            from config import RLMConfig
            cfg = RLMConfig(root=str(tmp_path))
            assert cfg.enrichment_provider == "gemini"
            assert cfg.enrichment_model == "gemini-2.0-flash"
            assert cfg.enrichment_api_key == "gemini-test-key"
            assert cfg.enrichment_enabled is True
        finally:
            del os.environ["GEMINI_API_KEY"]
