import json
import dspy
from Maps import get_mcts_tree
from navigator_prompts import get_navigator

def simulate_mcts_step(file_path, user_query):
    print(f"[*] Navigating: {file_path}")
    
    # 1. Squeeze the file into the hierarchical tree
    tree_json = get_mcts_tree(file_path)
    tree_data = json.loads(tree_json)
    
    # 2. Initialize the Reasoning Engine
    # Note: Ensure you have an API key set in your environment for dspy to work
    # dspy.settings.configure(lm=dspy.Google("models/gemini-2.0-flash")) 
    navigator = get_navigator()
    
    # 3. Perform a Selection Step
    print(f"[*] Query: '{user_query}'")
    result = navigator(
        query=user_query,
        context_path=file_path,
        tree_data=tree_data
    )
    
    print("\n--- REASONING RESULTS ---")
    print(f"Thought: {result.thought_process}")
    print(f"Target:  {result.best_child_node}")
    print(f"Action:  {result.action_type}")
    print(f"Score:   {result.relevance_score}")

if __name__ == "__main__":
    # Test this on Maps.py itself to see if it can find the 'squeeze' logic
    simulate_mcts_step("Maps.py", "How does the system extract symbols from the AST?")