"""MCTS session state for codebase navigation.

Tracks visited nodes, blacklisted branches, relevance scores,
and search depth for the Triad agent architecture.
"""

import uuid
import threading
from typing import Optional


class MCTSSession:
    """State for one MCTS navigation session (one user query)."""

    def __init__(self, query: str, max_depth: int = 5):
        self.session_id = str(uuid.uuid4())
        self.query = query
        self.max_depth = max_depth
        self.visited: list[str] = []
        self.blacklist: set[str] = set()
        self.scores: dict[str, float] = {}
        self.context_accumulated: list[str] = []

    @property
    def depth(self) -> int:
        return len(self.visited)

    @property
    def at_max_depth(self) -> bool:
        return self.depth >= self.max_depth

    def visit(self, node_id: str) -> None:
        """Record a node as visited."""
        if node_id not in self.visited:
            self.visited.append(node_id)

    def blacklist_node(self, node_id: str) -> None:
        """Mark a node/branch as irrelevant for this session."""
        self.blacklist.add(node_id)

    def set_score(self, node_id: str, score: float) -> None:
        """Set relevance score for a node (0.0 - 1.0)."""
        self.scores[node_id] = score

    def get_score(self, node_id: str) -> float:
        return self.scores.get(node_id, 0.0)

    def add_context(self, snippet: str) -> None:
        """Accumulate a context snippet."""
        self.context_accumulated.append(snippet)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "query": self.query,
            "depth": self.depth,
            "max_depth": self.max_depth,
            "visited": list(self.visited),
            "blacklist": list(self.blacklist),
            "scores": dict(self.scores),
            "context_count": len(self.context_accumulated),
        }


class MCTSSessionManager:
    """Manages multiple MCTS sessions (one per active query)."""

    def __init__(self):
        self._sessions: dict[str, MCTSSession] = {}
        self._lock = threading.Lock()

    def create(self, query: str, max_depth: int = 5) -> str:
        """Create a new session. Returns session ID."""
        session = MCTSSession(query, max_depth)
        with self._lock:
            self._sessions[session.session_id] = session
        return session.session_id

    def get(self, session_id: str) -> Optional[MCTSSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [s.to_dict() for s in self._sessions.values()]
