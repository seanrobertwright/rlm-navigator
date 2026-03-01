"""Node enricher — generates semantic summaries for AST skeleton nodes.

Parses skeleton output from squeezer.py, batches symbols, calls the configured
LLM provider for 1-line summaries, caches results by file path + mtime.
"""

import importlib
import queue
import re
import json
import threading
from typing import Optional

# Lazy-loaded SDK modules and client instances
_sdk_cache: dict[str, object] = {}
_client_cache: dict[tuple, object] = {}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"


def _get_sdk(name: str):
    """Lazy-import an SDK module by name."""
    if name not in _sdk_cache:
        _sdk_cache[name] = importlib.import_module(name)
    return _sdk_cache[name]


def _get_client(provider: str, api_key: str, base_url: str | None = None):
    """Get or create a cached SDK client instance."""
    key = (provider, api_key, base_url)
    if key not in _client_cache:
        if provider == "anthropic":
            sdk = _get_sdk("anthropic")
            _client_cache[key] = sdk.Anthropic(api_key=api_key)
        else:
            sdk = _get_sdk("openai")
            _client_cache[key] = sdk.OpenAI(api_key=api_key, base_url=base_url)
    return _client_cache[key]


def _parse_enrichment_response(text: str) -> Optional[dict]:
    """Parse LLM response text into enrichment dict. Handles markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)


def call_enrichment_api(prompt: str, config) -> Optional[str]:
    """Dispatch enrichment call to the configured provider. Returns raw text or None."""
    provider = config.enrichment_provider
    api_key = config.enrichment_api_key
    model = config.enrichment_model

    if not provider or not model:
        return None
    if not api_key and provider != "ollama":
        return None

    if provider == "anthropic":
        client = _get_client("anthropic", api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    elif provider in ("openai", "openrouter"):
        base_url = OPENROUTER_BASE_URL if provider == "openrouter" else None
        client = _get_client(provider, api_key, base_url)
        response = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    elif provider == "ollama":
        client = _get_client("ollama", "ollama", OLLAMA_BASE_URL)
        response = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content

    return None


# Enrichment prompt template
ENRICHMENT_PROMPT = """You are a code analyst. Given these code signatures from {filename}, provide a concise 1-line semantic summary for each symbol describing what it does (not what it is).

Symbols:
{symbols_text}

Respond with a JSON object mapping each symbol name to its 1-line summary.
Example: {{"process_data": "Transforms raw CSV rows into normalized database records."}}

JSON response:"""


def parse_skeleton_symbols(skeleton: str) -> list[dict]:
    """Extract symbol entries from a skeleton string.

    Each entry has: name, type (class/function), signature, range.
    """
    symbols = []
    line_pattern = re.compile(
        r"^(\s*)(class |def |async def |function |export |interface |struct |impl |type |fn )(.+?)(?:\s+#\s*L(\d+)-(\d+))?$"
    )

    for line in skeleton.split("\n"):
        m = line_pattern.match(line)
        if not m:
            continue
        keyword = m.group(2).strip()
        rest = m.group(3)
        start = m.group(4)
        end = m.group(5)

        # Extract the symbol name (first word/identifier after keyword)
        name_match = re.match(r"(\w+)", rest)
        if not name_match:
            continue

        name = name_match.group(1)
        sig_type = "class" if keyword == "class" else "function"
        signature = f"{keyword} {rest}".rstrip(":")

        symbols.append({
            "name": name,
            "type": sig_type,
            "signature": signature.strip(),
            "range": f"L{start}-{end}" if start else None,
        })

    return symbols


def build_enrichment_prompt(filename: str, symbols: list[dict]) -> str:
    """Build the Haiku prompt for a batch of symbols."""
    symbols_text = "\n".join(
        f"- {s['signature']}  {s['range'] or ''}" for s in symbols
    )
    return ENRICHMENT_PROMPT.format(filename=filename, symbols_text=symbols_text)


class EnrichmentCache:
    """Thread-safe cache for enrichment results, keyed by file path + mtime."""

    def __init__(self):
        self._cache: dict[str, tuple[float, dict]] = {}  # path -> (mtime, enrichments)
        self._lock = threading.Lock()

    def get(self, file_path: str, mtime: float) -> Optional[dict]:
        with self._lock:
            entry = self._cache.get(file_path)
            if entry and entry[0] == mtime:
                return entry[1]
            return None

    def put(self, file_path: str, mtime: float, enrichments: dict) -> None:
        with self._lock:
            self._cache[file_path] = (mtime, enrichments)

    def invalidate(self, file_path: str) -> None:
        with self._lock:
            self._cache.pop(file_path, None)

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


def merge_enrichments(skeleton: str, enrichments: dict[str, str]) -> str:
    """Annotate skeleton lines with enrichment summaries.

    Adds '# <summary>' after the line range comment for each enriched symbol.
    """
    lines = skeleton.split("\n")
    result = []

    for line in lines:
        # Check if this line has a symbol we can enrich
        for name, summary in enrichments.items():
            # Match lines like "class Foo:  # L1-14" or "  def bar(...):  # L3-8"
            pattern = re.compile(
                rf"(?:class |def |async def |fn |function ){re.escape(name)}\b.*#\s*L\d+-\d+"
            )
            if pattern.search(line):
                line = f"{line}  # {summary}"
                break
        result.append(line)

    return "\n".join(result)


async def enrich_file(file_path: str, skeleton: str, config) -> Optional[dict]:
    """Call enrichment API to generate enrichments for a file's skeleton.

    Returns dict mapping symbol names to summaries, or None on failure.
    """
    if not config or not config.enrichment_enabled:
        return None

    symbols = parse_skeleton_symbols(skeleton)
    if not symbols:
        return None

    prompt = build_enrichment_prompt(file_path, symbols)

    try:
        text = call_enrichment_api(prompt, config)
        if text is None:
            return None
        return _parse_enrichment_response(text)
    except Exception:
        return None


MAX_ENRICHMENT_QUEUE = 500


class EnrichmentWorker:
    """Background worker that processes files for enrichment."""

    def __init__(self, cache: EnrichmentCache, config=None):
        self._cache = cache
        self._config = config
        self._queue: queue.Queue = queue.Queue(maxsize=MAX_ENRICHMENT_QUEUE)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def enqueue(self, file_path: str, skeleton: str, mtime: float) -> None:
        """Add a file to the enrichment queue. Drops silently if queue is full."""
        if self._cache.get(file_path, mtime) is not None:
            return
        try:
            self._queue.put_nowait((file_path, skeleton, mtime))
        except queue.Full:
            pass  # Drop oldest-style: queue is bounded, skip new items

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def process_one(self) -> bool:
        """Process one item from the queue. Returns True if an item was processed."""
        try:
            file_path, skeleton, mtime = self._queue.get_nowait()
        except queue.Empty:
            return False

        if not self._config or not getattr(self._config, 'enrichment_enabled', False):
            return True

        symbols = parse_skeleton_symbols(skeleton)
        if not symbols:
            return True

        prompt = build_enrichment_prompt(file_path, symbols)

        try:
            text = call_enrichment_api(prompt, self._config)
            if text is None:
                return True
            enrichments = _parse_enrichment_response(text)
            self._cache.put(file_path, mtime, enrichments)
        except Exception:
            pass  # Enrichment is best-effort

        return True

    def start(self) -> None:
        """Start the background worker thread."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if not self.process_one():
                self._stop_event.wait(timeout=1)

    def stop(self) -> None:
        """Stop the worker and drain remaining queue items."""
        self._running = False
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        # Drain remaining items to avoid memory leak
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
