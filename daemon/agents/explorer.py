"""Explorer agent — proposes navigation paths based on tree skeletons.

The Explorer is the MCTS "Policy Network." It examines AST skeletons and document
outlines to propose the most probable symbolic paths for a given query.
"""

import json
from typing import Optional

EXPLORER_PROMPT = """You are a code exploration agent. Your job is to identify the most relevant files and symbols to investigate for a given query.

QUERY: {query}

CODEBASE SKELETON:
{tree_skeleton}

SESSION STATE:
- Previously visited: {visited}
- Blacklisted (skip these): {blacklist}
- Current depth: {depth}

RULES:
- Propose 1-3 nodes to investigate, ranked by relevance (0.0-1.0)
- Never propose blacklisted nodes
- Prefer unexplored branches over revisiting
- If no promising nodes remain, set action to "answer" or "pivot"

Respond with JSON:
{{"selected_nodes": [{{"path": "...", "symbol": "...", "score": 0.0-1.0, "reason": "..."}}], "action": "drill|map|answer|pivot"}}"""


def build_explorer_prompt(query: str, tree_skeleton: str, session_state: dict) -> str:
    return EXPLORER_PROMPT.format(
        query=query,
        tree_skeleton=tree_skeleton,
        visited=json.dumps(session_state.get("visited", [])),
        blacklist=json.dumps(session_state.get("blacklist", [])),
        depth=session_state.get("depth", 0),
    )


def parse_explorer_output(raw: str) -> Optional[dict]:
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None
