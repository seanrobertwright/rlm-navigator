"""Validator agent — critiques drill results against query requirements.

The Validator is the MCTS "Value Network." It reads implementation code and
determines if it's relevant to the user's query.
"""

import json
from typing import Optional

VALIDATOR_PROMPT = """You are a code validation agent. Analyze this code snippet and determine if it's relevant to the query.

QUERY: {query}

SYMBOL: {symbol_path}

CODE:
{code_snippet}

Respond with JSON:
{{"is_valid": true/false, "confidence": 0.0-1.0, "critique": "1-2 sentence explanation", "dependencies": ["list", "of", "related", "symbols", "to", "investigate"]}}"""


def build_validator_prompt(query: str, code_snippet: str, symbol_path: str) -> str:
    return VALIDATOR_PROMPT.format(
        query=query,
        code_snippet=code_snippet,
        symbol_path=symbol_path,
    )


def parse_validator_output(raw: str) -> Optional[dict]:
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None
