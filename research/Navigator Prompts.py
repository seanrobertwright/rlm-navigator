import dspy
import json
from typing import List, Dict, Any, Optional

# --- AGENT 1: THE EXPLORER (Policy Network) ---
class ExplorerSignature(dspy.Signature):
    """
    Acts as the 'Policy Network'. Analyzes the tree and proposes
    the next branch or symbol to explore.
    """
    query = dspy.InputField(desc="The user's coding request.")
    node_data = dspy.InputField(desc="Structural JSON of the current code node/children.")
    blacklist = dspy.InputField(desc="List of symbols already rejected by the Validator.")
    
    thought_process = dspy.OutputField(desc="Reasoning for selecting a path.")
    selected_node = dspy.OutputField(desc="The name of the child node to investigate.")
    action = dspy.OutputField(desc="EXPAND (go deeper) or FETCH (ready to read implementation).")

# --- AGENT 2: THE VALIDATOR (Value Network) ---
class ValidatorSignature(dspy.Signature):
    """
    Acts as the 'Value Network'. Critiques a code implementation 
    against the user query to ensure it actually solves the problem.
    """
    query = dspy.InputField(desc="Original user request.")
    code_snippet = dspy.InputField(desc="The implementation code fetched by the Explorer.")
    
    critique = dspy.OutputField(desc="Detailed analysis of why the code is or isn't correct.")
    is_valid = dspy.OutputField(desc="Boolean: True if this is the correct logic, False if we need to backtrack.")
    suggestions = dspy.OutputField(desc="If invalid, what should the Explorer look for instead?")

# --- AGENT 3: THE ORCHESTRATOR (The Controller) ---
class OrchestratorSignature(dspy.Signature):
    """
    Acts as the 'Pre-frontal Cortex'. Manages the state of the search,
    handles backtracking, and decides when to pivot to a different module.
    """
    query = dspy.InputField(desc="Original user request.")
    navigation_history = dspy.InputField(desc="Log of previous steps, selected nodes, and validator results.")
    current_status = dspy.InputField(desc="Current location in the tree and available symbols.")
    
    next_step = dspy.OutputField(desc="Instruction: 'CONTINUE_EXPLORATION', 'BACKTRACK', or 'FINALIZE'.")
    target_node = dspy.OutputField(desc="The specific node name or directory to focus on next.")
    pivot_reasoning = dspy.OutputField(desc="Logic behind the current search strategy or backtracking decision.")

class MultiAgentNavigator(dspy.Module):
    def __init__(self):
        super().__init__()
        self.orchestrator = dspy.ChainOfThought(OrchestratorSignature)
        self.explorer = dspy.ChainOfThought(ExplorerSignature)
        self.validator = dspy.ChainOfThought(ValidatorSignature)
        
        # In-memory state management
        self.history = []
        self.blacklist = []

    def forward(self, query: str, node_json: str, code_to_validate: Optional[str] = None):
        """
        Coordinates the triad of agents to navigate the codebase.
        """
        # 1. ORCHESTRATION PHASE
        # The Orchestrator reviews the current state before deciding who acts next
        control = self.orchestrator(
            query=query,
            navigation_history=json.dumps(self.history),
            current_status=node_json
        )
        
        # If the Orchestrator decides we are done
        if control.next_step == "FINALIZE":
            return {"status": "COMPLETE", "data": control.pivot_reasoning}

        # 2. VALIDATION PHASE (If code was provided)
        if code_to_validate:
            validation = self.validator(query=query, code_snippet=code_to_validate)
            self.history.append({
                "action": "VALIDATE",
                "result": validation.is_valid,
                "critique": validation.critique
            })
            
            if validation.is_valid.lower() == 'false':
                # Update blacklist based on validator rejection
                self.blacklist.append(control.target_node)
                return {"status": "REJECTED", "feedback": validation.suggestions}
            
            return {"status": "ACCEPTED", "analysis": validation.critique}

        # 3. EXPLORATION PHASE
        exploration = self.explorer(
            query=query, 
            node_data=node_json, 
            blacklist=json.dumps(self.blacklist)
        )
        
        self.history.append({
            "action": "EXPLORE",
            "selected": exploration.selected_node,
            "reasoning": exploration.thought_process
        })
        
        return exploration

def get_multi_agent_system():
    # Example config: dspy.settings.configure(lm=dspy.OpenAI(model="gpt-4o"))
    return MultiAgentNavigator()