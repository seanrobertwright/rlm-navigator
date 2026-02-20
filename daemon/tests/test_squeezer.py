"""Tests for the multi-language AST squeezer."""

import os
import sys
import tempfile
import textwrap

import pytest

# Add daemon directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeezer import squeeze, find_symbol, supported_languages, _detect_language


# ---------------------------------------------------------------------------
# Language detection tests
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    def test_python(self):
        assert _detect_language("main.py") == "python"

    def test_javascript(self):
        assert _detect_language("app.js") == "javascript"
        assert _detect_language("component.jsx") == "javascript"

    def test_typescript(self):
        assert _detect_language("server.ts") == "typescript"
        assert _detect_language("App.tsx") == "tsx"

    def test_go(self):
        assert _detect_language("main.go") == "go"

    def test_rust(self):
        assert _detect_language("lib.rs") == "rust"

    def test_java(self):
        assert _detect_language("Main.java") == "java"

    def test_c_cpp(self):
        assert _detect_language("main.c") == "c"
        assert _detect_language("util.h") == "c"
        assert _detect_language("main.cpp") == "cpp"

    def test_unsupported(self):
        assert _detect_language("style.css") is None
        assert _detect_language("data.json") is None

    def test_supported_languages(self):
        langs = supported_languages()
        assert isinstance(langs, list)
        # At minimum, Python should be available (tree-sitter-python is in requirements)


# ---------------------------------------------------------------------------
# Python squeezing tests
# ---------------------------------------------------------------------------

SAMPLE_PYTHON = textwrap.dedent('''\
    """Module docstring."""

    class Calculator:
        """A simple calculator."""

        def add(self, a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        def subtract(self, a: int, b: int) -> int:
            """Subtract b from a."""
            return a - b

    def standalone_function(x, y):
        """A standalone function."""
        result = x * y
        return result

    async def async_handler(request):
        """Handle async request."""
        data = await request.json()
        return data
''')


class TestPythonSqueeze:
    @pytest.fixture
    def python_file(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text(SAMPLE_PYTHON)
        return str(f)

    def test_squeeze_returns_string(self, python_file):
        result = squeeze(python_file)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_squeeze_contains_class(self, python_file):
        result = squeeze(python_file)
        assert "Calculator" in result

    def test_squeeze_contains_functions(self, python_file):
        result = squeeze(python_file)
        assert "add" in result
        assert "subtract" in result
        assert "standalone_function" in result

    def test_squeeze_contains_async(self, python_file):
        result = squeeze(python_file)
        assert "async_handler" in result

    def test_squeeze_no_implementation(self, python_file):
        result = squeeze(python_file)
        # Should NOT contain implementation details
        assert "result = x * y" not in result
        assert "return a + b" not in result

    def test_squeeze_has_line_refs(self, python_file):
        result = squeeze(python_file)
        # Should contain line references like L3-12
        assert "L" in result

    def test_squeeze_nonexistent_file(self):
        result = squeeze("/nonexistent/file.py")
        assert "Error" in result or "not found" in result


class TestPythonFindSymbol:
    @pytest.fixture
    def python_file(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text(SAMPLE_PYTHON)
        return str(f)

    def test_find_class(self, python_file):
        result = find_symbol(python_file, "Calculator")
        assert result is not None
        start, end = result
        assert isinstance(start, int)
        assert isinstance(end, int)
        assert start < end

    def test_find_function(self, python_file):
        result = find_symbol(python_file, "standalone_function")
        assert result is not None
        start, end = result
        assert start > 0
        assert end > start

    def test_find_method(self, python_file):
        result = find_symbol(python_file, "add")
        assert result is not None

    def test_find_nonexistent(self, python_file):
        result = find_symbol(python_file, "nonexistent_symbol")
        assert result is None

    def test_find_in_nonexistent_file(self):
        result = find_symbol("/nonexistent/file.py", "foo")
        assert result is None


# ---------------------------------------------------------------------------
# JavaScript squeezing tests
# ---------------------------------------------------------------------------

SAMPLE_JS = textwrap.dedent('''\
    class UserService {
        constructor(db) {
            this.db = db;
        }

        async getUser(id) {
            return await this.db.find(id);
        }

        deleteUser(id) {
            return this.db.remove(id);
        }
    }

    function formatName(first, last) {
        return `${first} ${last}`;
    }

    const helper = (x) => x * 2;

    export default UserService;
''')


class TestJavaScriptSqueeze:
    @pytest.fixture
    def js_file(self, tmp_path):
        f = tmp_path / "service.js"
        f.write_text(SAMPLE_JS)
        return str(f)

    def test_squeeze_contains_class(self, js_file):
        result = squeeze(js_file)
        assert "UserService" in result

    def test_squeeze_contains_function(self, js_file):
        result = squeeze(js_file)
        assert "formatName" in result

    def test_squeeze_no_implementation(self, js_file):
        result = squeeze(js_file)
        # Should not have the db.find call
        assert "this.db.find" not in result


# ---------------------------------------------------------------------------
# TypeScript squeezing tests
# ---------------------------------------------------------------------------

SAMPLE_TS = textwrap.dedent('''\
    interface Config {
        host: string;
        port: number;
        debug?: boolean;
    }

    type Handler = (req: Request) => Promise<Response>;

    class Server {
        private config: Config;

        constructor(config: Config) {
            this.config = config;
        }

        async start(): Promise<void> {
            console.log("starting...");
        }
    }

    function createServer(config: Config): Server {
        return new Server(config);
    }

    export { Server, createServer };
''')


class TestTypeScriptSqueeze:
    @pytest.fixture
    def ts_file(self, tmp_path):
        f = tmp_path / "server.ts"
        f.write_text(SAMPLE_TS)
        return str(f)

    def test_squeeze_contains_interface(self, ts_file):
        result = squeeze(ts_file)
        assert "Config" in result

    def test_squeeze_contains_class(self, ts_file):
        result = squeeze(ts_file)
        assert "Server" in result

    def test_squeeze_contains_function(self, ts_file):
        result = squeeze(ts_file)
        assert "createServer" in result


# ---------------------------------------------------------------------------
# Go squeezing tests
# ---------------------------------------------------------------------------

SAMPLE_GO = textwrap.dedent('''\
    package main

    import "fmt"

    type Server struct {
        Host string
        Port int
    }

    func NewServer(host string, port int) *Server {
        return &Server{Host: host, Port: port}
    }

    func (s *Server) Start() error {
        fmt.Printf("Starting on %s:%d", s.Host, s.Port)
        return nil
    }

    func main() {
        s := NewServer("localhost", 8080)
        s.Start()
    }
''')


class TestGoSqueeze:
    @pytest.fixture
    def go_file(self, tmp_path):
        f = tmp_path / "main.go"
        f.write_text(SAMPLE_GO)
        return str(f)

    def test_squeeze_contains_struct(self, go_file):
        result = squeeze(go_file)
        assert "Server" in result

    def test_squeeze_contains_functions(self, go_file):
        result = squeeze(go_file)
        assert "NewServer" in result


# ---------------------------------------------------------------------------
# Fallback tests
# ---------------------------------------------------------------------------

class TestFallback:
    def test_unsupported_extension(self, tmp_path):
        f = tmp_path / "styles.css"
        f.write_text("body { color: red; }\n" * 30)
        result = squeeze(str(f))
        assert "unsupported" in result.lower()
        assert "lines" in result.lower()

    def test_fallback_shows_preview(self, tmp_path):
        f = tmp_path / "data.yaml"
        content = "key: value\n" * 5
        f.write_text(content)
        result = squeeze(str(f))
        assert "key: value" in result
