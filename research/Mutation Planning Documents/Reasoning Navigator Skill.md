## **name: reasoning\_navigator description: Implements AlphaGo-style tree search for deep codebase navigation.**

# **Reasoning Navigator Skill (MCTS Protocol)**

## **Phase 1: Selection (The Root)**

1. Call get\_status to ensure the live index is ready.  
2. Call rlm\_reason\_tree on the project root.  
3. **Heuristic**: Assign a relevance score (0.0 \- 1.0) to each directory/module based on the user's query.

## **Phase 2: Expansion (The Branch)**

1. For the highest-scoring module, call rlm\_reason\_tree on that specific file path.  
2. Analyze the **Class/Function signatures**.  
3. If a symbol's docstring or name matches the "State Goal," proceed to Extraction.

## **Phase 3: Extraction (The Leaf)**

1. Use rlm\_surgical\_fetch for the identified line ranges.  
2. If the logic is insufficient, **Backtrack**: Return to Phase 2 and select the second-highest scoring symbol.

## **Phase 4: Verification**

* Once the solution is found, verify it against the project's global dependencies using a final rlm\_reason\_tree check on the imports.

## **Token Safety**

* Never expand more than 3 branches simultaneously.  
* If the tree depth exceeds 4, summarize current findings and ask the user for a "Heuristic Pivot."