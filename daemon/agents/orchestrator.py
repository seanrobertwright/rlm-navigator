"""Orchestrator agent — manages MCTS search state and backtracking decisions.

The Orchestrator is the "Control Agent." It maintains global search state,
decides when to pivot strategies, and prevents circular reasoning.
"""

import json
from typing import Optional

ORCHESTRATOR_PROMPT = """You are a search orchestration agent managing a codebase navigation session.

ORIGINAL QUERY: {query}

SESSION STATE:
{session_state_json}

LAST ACTION RESULT:
{last_result_json}

DECISION RULES:
- If the last result was relevant (is_valid=true), consider if we have enough context to answer
- If the last result was irrelevant, blacklist that branch and suggest the next best node
- If depth >= max_depth, force an answer with available context
- If all promising branches are exhausted, answer with what we have

Respond with JSON:
{{"next_action": "drill|map|answer|backtrack", "target_node": "path::symbol or null", "reasoning": "1-2 sentences", "should_blacklist": "node to blacklist or null"}}"""


def build_orchestrator_prompt(query: str, session_state: dict, last_result: dict) -> str:
    return ORCHESTRATOR_PROMPT.format(
        query=query,
        session_state_json=json.dumps(session_state, indent=2),
        last_result_json=json.dumps(last_result, indent=2),
    )


def parse_orchestrator_output(raw: str) -> Optional[dict]:
    try:
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None
