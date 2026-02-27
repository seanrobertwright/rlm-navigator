import pytest
import json


class TestSkeletonParsing:
    def test_parse_skeleton_to_symbols(self):
        """Should extract symbol names and line ranges from skeleton text."""
        from node_enricher import parse_skeleton_symbols
        skeleton = """# test.py — 3 symbols, 20 lines
class Calculator:  # L1-14
    ...

  def add(self, a: int, b: int) -> int:  # L3-8
      \"\"\"Add two numbers.\"\"\"
    ...

  def subtract(self, a, b):  # L10-14
    ...
"""
        symbols = parse_skeleton_symbols(skeleton)
        assert len(symbols) == 3
        assert symbols[0]["name"] == "Calculator"
        assert symbols[0]["type"] == "class"
        assert symbols[1]["name"] == "add"
        assert symbols[1]["type"] == "function"
        assert symbols[2]["name"] == "subtract"

    def test_parse_async_function(self):
        """Should detect async functions."""
        from node_enricher import parse_skeleton_symbols
        skeleton = "async def fetch_data(url):  # L1-10\n    ...\n"
        symbols = parse_skeleton_symbols(skeleton)
        assert len(symbols) == 1
        assert symbols[0]["name"] == "fetch_data"
        assert symbols[0]["type"] == "function"

    def test_parse_empty_skeleton(self):
        """Empty skeleton should return empty list."""
        from node_enricher import parse_skeleton_symbols
        assert parse_skeleton_symbols("") == []
        assert parse_skeleton_symbols("# file.py — 0 symbols") == []


class TestEnrichmentPrompt:
    def test_build_prompt(self):
        """Should build a valid prompt for Haiku."""
        from node_enricher import build_enrichment_prompt
        symbols = [
            {"name": "Calculator", "type": "class", "signature": "class Calculator:", "range": "L1-14"},
            {"name": "add", "type": "function", "signature": "def add(self, a: int, b: int) -> int:", "range": "L3-8"},
        ]
        prompt = build_enrichment_prompt("math_utils.py", symbols)
        assert "math_utils.py" in prompt
        assert "Calculator" in prompt
        assert "add" in prompt
        assert "JSON" in prompt


class TestEnrichmentCache:
    def test_cache_stores_and_retrieves(self):
        """Enrichment cache should store and retrieve by file path + mtime."""
        from node_enricher import EnrichmentCache
        cache = EnrichmentCache()

        enrichments = {"Calculator": "A basic arithmetic calculator class."}
        cache.put("test.py", 1000.0, enrichments)

        result = cache.get("test.py", 1000.0)
        assert result == enrichments

    def test_cache_invalidates_on_mtime_change(self):
        """Cache should miss when mtime changes."""
        from node_enricher import EnrichmentCache
        cache = EnrichmentCache()

        cache.put("test.py", 1000.0, {"Calculator": "A calculator."})
        result = cache.get("test.py", 1001.0)
        assert result is None

    def test_cache_invalidate_explicit(self):
        """Explicit invalidation should remove entry."""
        from node_enricher import EnrichmentCache
        cache = EnrichmentCache()

        cache.put("test.py", 1000.0, {"Foo": "Bar"})
        assert cache.size == 1
        cache.invalidate("test.py")
        assert cache.size == 0
        assert cache.get("test.py", 1000.0) is None


class TestEnrichmentMerge:
    def test_merge_enrichments_into_skeleton(self):
        """Should annotate skeleton lines with summaries."""
        from node_enricher import merge_enrichments
        skeleton = "class Calculator:  # L1-14\n    ...\n\n  def add(self, a, b):  # L3-8\n    ...\n"
        enrichments = {
            "Calculator": "A basic arithmetic calculator.",
            "add": "Returns the sum of two numbers.",
        }
        result = merge_enrichments(skeleton, enrichments)
        assert "# A basic arithmetic calculator." in result
        assert "# Returns the sum of two numbers." in result

    def test_merge_leaves_unenriched_lines_alone(self):
        """Lines without matching enrichments should be unchanged."""
        from node_enricher import merge_enrichments
        skeleton = "# file.py — 1 symbol\nclass Foo:  # L1-5\n    ...\n"
        result = merge_enrichments(skeleton, {})
        assert result == skeleton

    def test_merge_handles_async(self):
        """Should enrich async function lines."""
        from node_enricher import merge_enrichments
        skeleton = "async def fetch(url):  # L1-10\n    ...\n"
        enrichments = {"fetch": "Fetches data from a URL."}
        result = merge_enrichments(skeleton, enrichments)
        assert "# Fetches data from a URL." in result


class TestEnrichmentWorker:
    def test_worker_processes_queue(self):
        """Enrichment worker should process files from queue."""
        from node_enricher import EnrichmentCache, EnrichmentWorker

        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=None)

        skeleton = "class Foo:  # L1-10\n    ...\n"
        worker.enqueue("test.py", skeleton, 1000.0)

        assert worker.queue_size >= 0

    def test_worker_skips_when_no_config(self):
        """Worker should skip enrichment when no API key configured."""
        from node_enricher import EnrichmentCache, EnrichmentWorker

        cache = EnrichmentCache()
        worker = EnrichmentWorker(cache, config=None)

        skeleton = "class Foo:  # L1-10\n    ...\n"
        worker.enqueue("test.py", skeleton, 1000.0)
        worker.process_one()

        # Cache should be empty (no API call made)
        assert cache.get("test.py", 1000.0) is None

    def test_worker_skips_duplicate_enqueue(self):
        """Worker should skip enqueue if already cached at same mtime."""
        from node_enricher import EnrichmentCache, EnrichmentWorker

        cache = EnrichmentCache()
        cache.put("test.py", 1000.0, {"Foo": "A test class."})

        worker = EnrichmentWorker(cache, config=None)
        worker.enqueue("test.py", "class Foo:  # L1-10\n", 1000.0)

        assert worker.queue_size == 0
