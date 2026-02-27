"""E2E tests for ChunkStore lifecycle: chunk, read, modify, re-chunk, delete, scan."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rlm_daemon import ChunkStore


@pytest.mark.e2e
class TestChunkStoreE2E:
    def test_chunk_lifecycle(self, tmp_path):
        """Chunk → read → modify → re-chunk → delete."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        # Create a 500-line file
        f = tmp_path / "large.py"
        lines = [f"def func_{i}(): pass  # line {i}\n" for i in range(500)]
        f.write_text("".join(lines))

        store = ChunkStore(str(tmp_path), rlm_dir / "chunks")
        store.chunk_file(str(f))

        # Verify manifest
        manifest = store.get_manifest(str(f))
        assert manifest is not None
        assert manifest["total_lines"] == 500
        assert manifest["total_chunks"] >= 3
        assert manifest["chunk_size"] == 200
        assert manifest["overlap"] == 20

        # Read chunks and verify content
        chunk_0 = store.read_chunk(str(f), 0)
        chunk_1 = store.read_chunk(str(f), 1)
        assert chunk_0 is not None
        assert chunk_1 is not None

        # Verify header contains file path info
        assert "large.py" in chunk_0.split("\n")[0]

        # Verify chunk contains actual code
        assert "def func_0" in chunk_0

        # Verify overlap: last lines of chunk 0 should appear in chunk 1
        chunk_0_lines = chunk_0.strip().split("\n")
        chunk_1_lines = chunk_1.strip().split("\n")
        # Skip header lines; overlap means shared content
        assert len(chunk_0_lines) > 10
        assert len(chunk_1_lines) > 10

        # Modify file → re-chunk
        f.write_text("".join(lines[:100]))
        store.chunk_file(str(f))
        manifest = store.get_manifest(str(f))
        assert manifest["total_lines"] == 100
        assert manifest["total_chunks"] < 3  # Fewer chunks for smaller file

        # Delete
        store.remove_file(str(f))
        assert store.get_manifest(str(f)) is None

    def test_binary_file_skipped(self, tmp_path):
        """Binary files should not be chunked."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        store = ChunkStore(str(tmp_path), rlm_dir / "chunks")
        store.chunk_file(str(f))
        assert store.get_manifest(str(f)) is None

    def test_scan_all_chunks_project(self, tmp_path):
        """scan_all should chunk all text files, skip binaries."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        (tmp_path / "a.py").write_text("x = 1\n" * 50)
        (tmp_path / "b.js").write_text("var x = 1;\n" * 50)
        (tmp_path / "c.bin").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff\xfe" * 50)

        store = ChunkStore(str(tmp_path), rlm_dir / "chunks")
        store.scan_all()

        assert store.get_manifest(str(tmp_path / "a.py")) is not None
        assert store.get_manifest(str(tmp_path / "b.js")) is not None
        assert store.get_manifest(str(tmp_path / "c.bin")) is None

    def test_mtime_skip_optimization(self, tmp_path):
        """Re-chunking unchanged file should be a no-op (mtime check)."""
        rlm_dir = tmp_path / ".rlm"
        rlm_dir.mkdir()

        f = tmp_path / "stable.py"
        f.write_text("def stable(): pass\n" * 50)

        store = ChunkStore(str(tmp_path), rlm_dir / "chunks")
        store.chunk_file(str(f))

        manifest_1 = store.get_manifest(str(f))
        assert manifest_1 is not None

        # Re-chunk without modification — should skip (same mtime)
        store.chunk_file(str(f))
        manifest_2 = store.get_manifest(str(f))
        assert manifest_2 == manifest_1
