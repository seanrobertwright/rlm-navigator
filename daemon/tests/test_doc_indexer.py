import json
import pytest
from pathlib import Path


class TestDocumentTypeDetection:
    def test_markdown_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("README.md") is True
        assert is_document_file("guide.markdown") is True

    def test_txt_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("notes.txt") is True

    def test_rst_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("index.rst") is True

    def test_pdf_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("report.pdf") is True

    def test_code_not_detected(self):
        from doc_indexer import is_document_file
        assert is_document_file("main.py") is False
        assert is_document_file("index.ts") is False


class TestMarkdownToUnifiedTree:
    def test_simple_markdown(self, tmp_path):
        """Markdown with headers should produce a hierarchical tree."""
        md_file = tmp_path / "README.md"
        md_file.write_text("# Project\n\nOverview text.\n\n## Installation\n\nRun npm install.\n\n## Usage\n\nImport and use.\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        assert tree is not None
        assert tree["name"] == "README.md"
        assert tree["type"] == "document"
        assert tree["source"] == "local_md"
        assert len(tree["children"]) >= 1

    def test_nested_headings(self, tmp_path):
        """Nested markdown headings should produce nested children."""
        md_file = tmp_path / "guide.md"
        md_file.write_text("# Top\n\n## Sub A\n\nContent A.\n\n### Sub Sub\n\nDeep content.\n\n## Sub B\n\nContent B.\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        # Top level: "Top" heading at level 1
        top = tree["children"]
        assert len(top) == 1  # Single H1
        # Under "Top": Sub A and Sub B at level 2
        subs = top[0]["children"]
        assert len(subs) == 2
        # Sub A should have "Sub Sub" as child
        assert len(subs[0]["children"]) == 1
        assert subs[0]["children"][0]["name"] == "Sub Sub"

    def test_empty_markdown(self, tmp_path):
        """Empty markdown should return a minimal tree."""
        md_file = tmp_path / "empty.md"
        md_file.write_text("")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))
        assert tree is not None
        assert tree["children"] == []

    def test_headings_in_code_blocks_ignored(self, tmp_path):
        """Headings inside code blocks should not be treated as sections."""
        md_file = tmp_path / "code.md"
        md_file.write_text("# Real Heading\n\n```python\n# Not a heading\n## Also not\n```\n\n## Another Real\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        # Should have 2 sections: "Real Heading" and "Another Real"
        all_names = []

        def collect(node):
            all_names.append(node["name"])
            for c in node.get("children", []):
                collect(c)
        collect(tree)

        assert "Real Heading" in all_names
        assert "Another Real" in all_names
        assert "Not a heading" not in all_names

    def test_line_ranges_assigned(self, tmp_path):
        """Each section should have correct line range."""
        md_file = tmp_path / "ranges.md"
        md_file.write_text("# First\n\nLine 2\nLine 3\n\n## Second\n\nLine 7\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        first = tree["children"][0]
        assert first["range"]["start"] == 1
        assert "Second" in [c["name"] for c in first["children"]]


class TestPlaintextIndexing:
    def test_txt_file(self, tmp_path):
        """Plain text should get line count and preview."""
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("Line 1\nLine 2\nLine 3\n")

        from doc_indexer import index_document
        tree = index_document(str(txt_file))
        assert tree is not None
        assert tree["type"] == "document"
        assert tree["source"] == "local_txt"
        assert tree["metadata"]["line_count"] == 4  # 3 lines + trailing empty


class TestUnifiedNodeFormat:
    def test_node_has_required_fields(self, tmp_path):
        """Every node must have name, type, source, children."""
        md_file = tmp_path / "test.md"
        md_file.write_text("# Hello\n\nWorld.\n")

        from doc_indexer import index_markdown_local
        tree = index_markdown_local(str(md_file))

        def check_node(node):
            assert "name" in node
            assert "type" in node
            assert "source" in node
            assert "children" in node
            for child in node["children"]:
                check_node(child)

        check_node(tree)


class TestPageIndexIntegration:
    def test_pageindex_markdown_fallback(self, tmp_path):
        """index_document should work regardless of PageIndex availability."""
        from doc_indexer import index_document
        from config import RLMConfig
        cfg = RLMConfig()

        md_file = tmp_path / "test.md"
        md_file.write_text("# Introduction\n\nThis is a test.\n\n## Methods\n\nDescribed here.\n")

        tree = index_document(str(md_file), cfg)
        assert tree is not None
        assert tree["name"] == "test.md"
        assert len(tree["children"]) >= 1
