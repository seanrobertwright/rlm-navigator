"""E2E tests for the enrichment pipeline: squeeze → parse → mock API → cache → merge."""

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeezer import squeeze
from node_enricher import (
    parse_skeleton_symbols,
    EnrichmentCache,
    EnrichmentWorker,
    merge_enrichments,
)


def _ensure_anthropic_module():
    """Ensure a fake 'anthropic' module exists in sys.modules for monkeypatching."""
    if "anthropic" not in sys.modules:
        mod = types.ModuleType("anthropic")
        mod.Anthropic = None  # Will be patched per-test
        sys.modules["anthropic"] = mod


@pytest.mark.e2e
class TestEnrichmentPipelineE2E:
    def test_full_enrichment_pipeline(self, tmp_path, monkeypatch):
        """Full enrichment: squeeze → parse → mock API → cache → merge."""
        f = tmp_path / "calc.py"
        f.write_text(
            "class Calculator:\n"
            "    def add(self, a, b):\n"
            "        return a + b\n"
            "    def multiply(self, a, b):\n"
            "        return a * b\n"
        )

        # Step 1: Squeeze to get skeleton
        skeleton = squeeze(str(f))
        assert "Calculator" in skeleton

        # Step 2: Parse symbols from skeleton
        symbols = parse_skeleton_symbols(skeleton)
        symbol_names = [s["name"] for s in symbols]
        assert "Calculator" in symbol_names
        assert "add" in symbol_names
        assert "multiply" in symbol_names

        # Step 3: Mock Anthropic API
        mock_enrichments = {
            "Calculator": "Performs basic arithmetic operations.",
            "add": "Returns the sum of two numbers.",
            "multiply": "Returns the product of two numbers.",
        }

        class MockContent:
            text = json.dumps(mock_enrichments)

        class MockResponse:
            content = [MockContent()]

        class MockMessages:
            def create(self, **kwargs):
                return MockResponse()

        class MockClient:
            messages = MockMessages()

        _ensure_anthropic_module()
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: MockClient())

        class MockConfig:
            enrichment_enabled = True
            anthropic_api_key = "sk-test"

        # Step 4: Enqueue and process
        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=MockConfig())
        mtime = f.stat().st_mtime

        worker.enqueue(str(f), skeleton, mtime)
        assert worker.queue_size == 1
        processed = worker.process_one()
        assert processed is True

        # Step 5: Verify cache is populated
        enrichments = cache.get(str(f), mtime)
        assert enrichments is not None
        assert "Calculator" in enrichments
        assert "add" in enrichments
        assert "multiply" in enrichments

        # Step 6: Merge into skeleton
        enriched = merge_enrichments(skeleton, enrichments)
        assert "# Performs basic arithmetic operations." in enriched
        assert "# Returns the sum of two numbers." in enriched

    def test_api_failure_graceful_degradation(self, tmp_path, monkeypatch):
        """API failure should not crash; cache should remain empty."""
        f = tmp_path / "broken.py"
        f.write_text(
            "class Broken:\n"
            "    def fail(self):\n"
            "        raise Exception('boom')\n"
        )

        skeleton = squeeze(str(f))
        mtime = f.stat().st_mtime

        class MockConfig:
            enrichment_enabled = True
            anthropic_api_key = "sk-test"

        class FailingMessages:
            def create(self, **kwargs):
                raise Exception("API down")

        class FailingClient:
            messages = FailingMessages()

        _ensure_anthropic_module()
        monkeypatch.setattr("anthropic.Anthropic", lambda **kw: FailingClient())

        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=MockConfig())
        worker.enqueue(str(f), skeleton, mtime)

        # Should not raise
        worker.process_one()

        # Cache should remain empty (no partial data)
        assert cache.get(str(f), mtime) is None

        # Original skeleton should still be usable
        assert "Broken" in skeleton

    def test_cache_invalidation_on_mtime_change(self, tmp_path, monkeypatch):
        """Changed mtime should invalidate cached enrichments."""
        import time

        f = tmp_path / "evolving.py"
        f.write_text("class V1:\n    def old(self): pass\n")
        mtime_1 = f.stat().st_mtime

        cache = EnrichmentCache()
        cache.put(str(f), mtime_1, {"V1": "First version."})

        # Verify cache hit
        assert cache.get(str(f), mtime_1) is not None

        # Simulate file modification (different mtime)
        time.sleep(0.1)
        f.write_text("class V2:\n    def new(self): pass\n")
        mtime_2 = f.stat().st_mtime

        # Old mtime → miss (file changed)
        assert cache.get(str(f), mtime_2) is None
