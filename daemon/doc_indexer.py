"""Document indexer — routes document files through PageIndex or local fallback.

Produces unified node trees compatible with the code skeleton format.
"""

import re
from pathlib import Path
from typing import Optional

DOC_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".pdf"}


def is_document_file(file_path: str) -> bool:
    """Check if a file is a document type (not code)."""
    return Path(file_path).suffix.lower() in DOC_EXTENSIONS


def index_document(file_path: str, config=None) -> Optional[dict]:
    """Index a document file. Uses PageIndex if available, else local fallback.

    Returns a unified node tree dict or None on failure.
    """
    ext = Path(file_path).suffix.lower()

    # Try PageIndex for richer indexing (requires API key + library)
    if config and config.doc_indexing_enabled and ext in (".md", ".markdown"):
        try:
            return _index_with_pageindex_md(file_path, config)
        except Exception:
            pass  # Fall through to local

    if config and config.doc_indexing_enabled and ext == ".pdf":
        try:
            return _index_with_pageindex_pdf(file_path, config)
        except Exception:
            pass

    # Local fallback for markdown
    if ext in (".md", ".markdown"):
        return index_markdown_local(file_path)

    # Local fallback for plain text / rst
    if ext in (".txt", ".rst"):
        return _index_plaintext_local(file_path)

    return None


def index_markdown_local(file_path: str) -> Optional[dict]:
    """Parse markdown into a hierarchical tree using header structure. No LLM needed."""
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    filename = Path(file_path).name
    root = _make_node(filename, "document", "local_md")

    if not text.strip():
        return root

    lines = text.split("\n")
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$")

    # Build flat list of headings with their line numbers and levels
    headings = []
    in_code_block = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        m = heading_pattern.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            headings.append({"title": title, "level": level, "line": i + 1})

    if not headings:
        return root

    # Assign end lines
    for i, h in enumerate(headings):
        if i + 1 < len(headings):
            h["end_line"] = headings[i + 1]["line"] - 1
        else:
            h["end_line"] = len(lines)

    # Build tree using stack-based algorithm (same as PageIndex's build_tree_from_nodes)
    stack = [(root, 0)]  # (node, level) — root is level 0
    for h in headings:
        node = _make_node(
            h["title"], "section", "local_md",
            range_start=h["line"], range_end=h["end_line"],
        )
        # Pop until we find a parent with lower level
        while len(stack) > 1 and stack[-1][1] >= h["level"]:
            stack.pop()
        stack[-1][0]["children"].append(node)
        stack.append((node, h["level"]))

    return root


def _index_plaintext_local(file_path: str) -> Optional[dict]:
    """Minimal indexing for plain text — just line count and first few lines as summary."""
    try:
        text = Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    lines = text.split("\n")
    filename = Path(file_path).name
    node = _make_node(filename, "document", "local_txt")
    node["metadata"]["line_count"] = len(lines)
    node["metadata"]["preview"] = "\n".join(lines[:10])
    return node


def _index_with_pageindex_md(file_path: str, config) -> Optional[dict]:
    """Use PageIndex library to index a markdown file."""
    import asyncio
    from pageindex.page_index_md import md_to_tree

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(md_to_tree(
            md_path=file_path,
            model=config.pageindex_model,
            if_add_node_summary="yes",
            if_add_node_id="yes",
        ))
    finally:
        loop.close()

    if not result:
        return None

    return _adapt_pageindex_tree(result, Path(file_path).name, "pageindex_md")


def _index_with_pageindex_pdf(file_path: str, config) -> Optional[dict]:
    """Use PageIndex library to index a PDF file."""
    import asyncio
    from pageindex import page_index as pi_module

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(pi_module.page_index(
            pdf_path=file_path,
            model=config.pageindex_model,
            if_add_node_summary="yes",
            if_add_node_id="yes",
        ))
    finally:
        loop.close()

    if not result:
        return None

    return _adapt_pageindex_tree(result, Path(file_path).name, "pageindex_pdf")


def _adapt_pageindex_tree(pi_tree, filename: str, source: str) -> dict:
    """Transform PageIndex output into RLM unified node format."""
    root = _make_node(filename, "document", source)

    nodes = pi_tree if isinstance(pi_tree, list) else pi_tree.get("nodes", [pi_tree])
    for pi_node in nodes:
        root["children"].append(_adapt_node(pi_node, source))

    return root


def _adapt_node(pi_node: dict, source: str) -> dict:
    """Recursively adapt a single PageIndex node."""
    title = pi_node.get("title", "Untitled")
    node = _make_node(title, "section", source)

    if "start_index" in pi_node:
        node["range"] = {"start": pi_node["start_index"], "end": pi_node.get("end_index")}
    if "node_id" in pi_node:
        node["metadata"]["node_id"] = pi_node["node_id"]
    if "summary" in pi_node:
        node["summary"] = pi_node["summary"]
    if "text" in pi_node:
        node["metadata"]["text_preview"] = pi_node["text"][:200]

    for child in pi_node.get("nodes", []):
        node["children"].append(_adapt_node(child, source))

    return node


def _make_node(name: str, node_type: str, source: str,
               range_start: int = None, range_end: int = None) -> dict:
    """Create a unified node dict."""
    node = {
        "name": name,
        "type": node_type,
        "source": source,
        "summary": None,
        "metadata": {},
        "children": [],
    }
    if range_start is not None:
        node["range"] = {"start": range_start, "end": range_end}
    return node
