"""Multi-language AST Squeezer using tree-sitter.

Parses source files into structural skeletons (signatures + docstrings only),
enabling token-efficient codebase navigation.
"""

from pathlib import Path
from typing import Optional

try:
    from tree_sitter import Language, Parser, Node
except ImportError:
    Language = Parser = Node = None

# ---------------------------------------------------------------------------
# Language registry — maps file extensions to (module, language_func) pairs
# ---------------------------------------------------------------------------

_LANG_MODULES = {}

def _try_import(module_name: str, lang_name: str):
    """Attempt to import a tree-sitter language module."""
    try:
        mod = __import__(module_name)
        _LANG_MODULES[lang_name] = mod
        return True
    except ImportError:
        return False

# Extension -> language name mapping
EXT_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
}

# Module name -> [language names] (some modules provide multiple languages)
_MODULE_LANG_MAP = {
    "tree_sitter_python": ["python"],
    "tree_sitter_javascript": ["javascript"],
    "tree_sitter_typescript": ["typescript", "tsx"],
    "tree_sitter_go": ["go"],
    "tree_sitter_rust": ["rust"],
    "tree_sitter_java": ["java"],
    "tree_sitter_c": ["c"],
    "tree_sitter_cpp": ["cpp"],
}

# Lazy init flag
_initialized = False


def _init_languages():
    """Lazily initialize available language parsers."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    for module_name, lang_names in _MODULE_LANG_MAP.items():
        try:
            mod = __import__(module_name)
            for lang_name in lang_names:
                _LANG_MODULES[lang_name] = mod
        except ImportError:
            pass


def _get_language(lang_name: str) -> Optional["Language"]:
    """Get a tree-sitter Language object for the given language name."""
    _init_languages()
    if Language is None:
        return None
    mod = _LANG_MODULES.get(lang_name)
    if mod is None:
        return None
    # tree-sitter-typescript exposes language_typescript() and language_tsx()
    if lang_name == "typescript" and hasattr(mod, "language_typescript"):
        return Language(mod.language_typescript())
    if lang_name == "tsx" and hasattr(mod, "language_tsx"):
        return Language(mod.language_tsx())
    # Standard: module.language()
    return Language(mod.language())


def _get_parser(lang_name: str) -> Optional["Parser"]:
    """Create a parser for the given language."""
    lang = _get_language(lang_name)
    if lang is None or Parser is None:
        return None
    return Parser(lang)


def _detect_language(file_path: str) -> Optional[str]:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    return EXT_MAP.get(ext)


# ---------------------------------------------------------------------------
# Node type config per language — which AST nodes to extract as signatures
# ---------------------------------------------------------------------------

# Maps language -> list of (node_type, extraction_style) tuples
# Styles: "function", "class", "method", "interface", "struct", "enum"
SKELETON_NODES: dict[str, list[tuple[str, str]]] = {
    "python": [
        ("class_definition", "class"),
        ("function_definition", "function"),
    ],
    "javascript": [
        ("class_declaration", "class"),
        ("function_declaration", "function"),
        ("method_definition", "method"),
        ("arrow_function", "arrow"),
        ("export_statement", "export"),
    ],
    "typescript": [
        ("class_declaration", "class"),
        ("function_declaration", "function"),
        ("method_definition", "method"),
        ("interface_declaration", "interface"),
        ("type_alias_declaration", "type"),
        ("enum_declaration", "enum"),
        ("arrow_function", "arrow"),
        ("export_statement", "export"),
    ],
    "tsx": [
        ("class_declaration", "class"),
        ("function_declaration", "function"),
        ("method_definition", "method"),
        ("interface_declaration", "interface"),
        ("type_alias_declaration", "type"),
        ("enum_declaration", "enum"),
        ("arrow_function", "arrow"),
        ("export_statement", "export"),
    ],
    "go": [
        ("function_declaration", "function"),
        ("method_declaration", "method"),
        ("type_declaration", "type"),
        ("interface_type", "interface"),
        ("struct_type", "struct"),
    ],
    "rust": [
        ("function_item", "function"),
        ("impl_item", "class"),
        ("struct_item", "struct"),
        ("enum_item", "enum"),
        ("trait_item", "interface"),
        ("type_item", "type"),
    ],
    "java": [
        ("class_declaration", "class"),
        ("method_declaration", "method"),
        ("interface_declaration", "interface"),
        ("enum_declaration", "enum"),
        ("constructor_declaration", "method"),
    ],
    "c": [
        ("function_definition", "function"),
        ("struct_specifier", "struct"),
        ("enum_specifier", "enum"),
        ("type_definition", "type"),
        ("declaration", "declaration"),
    ],
    "cpp": [
        ("function_definition", "function"),
        ("class_specifier", "class"),
        ("struct_specifier", "struct"),
        ("enum_specifier", "enum"),
        ("namespace_definition", "namespace"),
        ("template_declaration", "template"),
    ],
}


# ---------------------------------------------------------------------------
# Skeleton extraction
# ---------------------------------------------------------------------------

def _node_text(node: "Node", source: bytes) -> str:
    """Extract text from a node."""
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _extract_signature(node: "Node", source: bytes, lang: str) -> Optional[str]:
    """Extract a signature line from an AST node."""
    text = _node_text(node, source)
    lines = text.split("\n")

    if lang == "python":
        if node.type == "class_definition":
            # class Name(bases):
            first_line = lines[0].rstrip()
            return first_line
        if node.type == "function_definition":
            # Get the def line(s) — may span multiple lines with long signatures
            sig_lines = []
            for line in lines:
                sig_lines.append(line.rstrip())
                if ":" in line and "):" in line or line.rstrip().endswith(":"):
                    break
            sig = "\n".join(sig_lines)
            # Append docstring if present
            for child in node.children:
                if child.type == "block":
                    for stmt in child.children:
                        if stmt.type == "expression_statement":
                            for expr in stmt.children:
                                if expr.type == "string":
                                    doc = _node_text(expr, source).strip()
                                    # Truncate long docstrings
                                    doc_lines = doc.split("\n")
                                    if len(doc_lines) > 3:
                                        doc = "\n".join(doc_lines[:3]) + '\n    ..."""'
                                    return sig + "\n" + " " * _get_indent(node, source) + "    " + doc
                            break
                    break
            return sig

    # For all other languages, take the first line as signature
    if node.type in ("export_statement",):
        # For exports, show the first meaningful line
        first_line = lines[0].rstrip()
        if len(lines) > 1:
            return first_line + " ..."
        return first_line

    # Generic: first line up to opening brace/body
    first_line = lines[0].rstrip()
    # For multi-line signatures, gather until we hit { or body
    if "{" not in first_line and len(lines) > 1:
        for i, line in enumerate(lines[1:], 1):
            first_line += "\n" + line.rstrip()
            if "{" in line:
                break
            if i >= 3:  # Cap at 4 lines for signature
                first_line += "\n    ..."
                break

    return first_line


def _get_indent(node: "Node", source: bytes) -> int:
    """Get the indentation level of a node."""
    line_start = source.rfind(b"\n", 0, node.start_byte) + 1
    indent = node.start_byte - line_start
    return indent


def _walk_for_skeletons(node: "Node", source: bytes, lang: str, target_types: set[str], depth: int = 0) -> list[dict]:
    """Recursively walk AST to find skeleton-worthy nodes."""
    results = []

    if node.type in target_types:
        sig = _extract_signature(node, source, lang)
        if sig:
            results.append({
                "signature": sig,
                "type": node.type,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "depth": depth,
            })
        # Recurse into children for nested definitions (methods in classes, etc.)
        for child in node.children:
            results.extend(_walk_for_skeletons(child, source, lang, target_types, depth + 1))
    else:
        for child in node.children:
            results.extend(_walk_for_skeletons(child, source, lang, target_types, depth))

    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def squeeze(file_path: str) -> str:
    """Parse a file into its structural skeleton (signatures + docstrings only).

    Returns a string with one signature per structural element, body replaced
    with `...` placeholders. For unsupported languages, returns a summary.
    """
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"

    lang_name = _detect_language(file_path)
    if lang_name is None:
        return _fallback_squeeze(path)

    parser = _get_parser(lang_name)
    if parser is None:
        return _fallback_squeeze(path)

    source = path.read_bytes()
    tree = parser.parse(source)
    root = tree.root_node

    target_types = {nt for nt, _ in SKELETON_NODES.get(lang_name, [])}
    if not target_types:
        return _fallback_squeeze(path)

    skeletons = _walk_for_skeletons(root, source, lang_name, target_types)

    if not skeletons:
        return f"# {path.name} — no structural elements found ({_count_lines(source)} lines)"

    lines = [f"# {path.name} — {len(skeletons)} symbols, {_count_lines(source)} lines"]
    for skel in skeletons:
        indent = "  " * skel["depth"]
        sig_lines = skel["signature"].split("\n")
        lines.append(f"{indent}{sig_lines[0]}  # L{skel['start_line']}-{skel['end_line']}")
        for extra in sig_lines[1:]:
            lines.append(f"{indent}{extra}")
        lines.append(f"{indent}    ...")
        lines.append("")

    return "\n".join(lines)


def find_symbol(file_path: str, symbol_name: str) -> Optional[tuple[int, int]]:
    """Locate a symbol by name in a file.

    Returns (start_line, end_line) or None if not found.
    """
    path = Path(file_path)
    if not path.exists():
        return None

    lang_name = _detect_language(file_path)

    # Try tree-sitter first
    if lang_name:
        parser = _get_parser(lang_name)
        if parser:
            source = path.read_bytes()
            tree = parser.parse(source)
            result = _find_symbol_in_tree(tree.root_node, source, symbol_name)
            if result:
                return result

    # Fallback: Python AST for .py files
    if file_path.endswith(".py"):
        return _find_symbol_python_ast(path, symbol_name)

    return None


def _find_symbol_in_tree(node: "Node", source: bytes, symbol_name: str) -> Optional[tuple[int, int]]:
    """Search tree-sitter AST for a named symbol."""
    # Check if this node has a name child matching the symbol
    for child in node.children:
        if child.type in ("identifier", "name", "type_identifier", "property_identifier"):
            name = _node_text(child, source)
            if name == symbol_name:
                return (node.start_point[0] + 1, node.end_point[0] + 1)
    # Recurse
    for child in node.children:
        result = _find_symbol_in_tree(child, source, symbol_name)
        if result:
            return result
    return None


def _find_symbol_python_ast(path: Path, symbol_name: str) -> Optional[tuple[int, int]]:
    """Fallback: use Python's ast module for .py files."""
    import ast
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == symbol_name:
                    return (node.lineno, node.end_lineno)
    except Exception:
        pass
    return None


def supported_languages() -> list[str]:
    """Return list of languages with available tree-sitter parsers."""
    _init_languages()
    return sorted(_LANG_MODULES.keys())


def _fallback_squeeze(path: Path) -> str:
    """Fallback for unsupported languages: show first N lines + line count."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        total = len(lines)
        preview = "\n".join(lines[:20])
        if total > 20:
            preview += f"\n... ({total - 20} more lines)"
        return f"# {path.name} — unsupported language ({total} lines)\n{preview}"
    except Exception as e:
        return f"Error reading {path}: {e}"


def _count_lines(source: bytes) -> int:
    """Count lines in source bytes."""
    return source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0)
