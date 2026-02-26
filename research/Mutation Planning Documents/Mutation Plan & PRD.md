# **Mutation Plan: RLM Navigator (Reasoning-Centric Integration)**

## **1\. Executive Summary**

This document defines the transition of the **RLM Navigator** from a basic retrieval tool into a reasoning-based navigation engine. By mutating the **PageIndex** vectorless RAG paradigm, we treat codebases as hierarchical search trees, replacing traditional vector indexing with a live, AST-driven structural index.

## **2\. Structural Mutations**

| Component | Legacy State | Target Mutation (PageIndex Hybrid) |
| :---- | :---- | :---- |
| **Logic Engine** | Greedy Search | **AlphaGo-style MCTS** tree-search |
| **Data Provider** | Full-text cat | **Maps.py AST Squeezer** (Symbolic Trees) |
| **Interface** | Standard MCP | **Reasoning-Aware MCP** (Structured JSON) |
| **Persistence** | Static Index | **Live Daemon Watcher** (Cache Invalidation) |

## **3\. The Reasoning Architecture**

1. **The Symbolic Tree:** A PageIndex-compatible hierarchy representing folders, files, classes, and methods.  
2. **The Squeezer (Maps.py):** Generates high-fidelity metadata (signatures/docstrings) for each node.  
3. **The Surgical Driller:** Provides just-in-time access to function implementation via line-range extraction.  
4. **The Health Daemon:** Manages a Unix Domain Socket to ensure the reasoning engine is working with "Live" state.

## **4\. Implementation Manifest**

* **Maps.py**: Now exports structured JSON nodes optimized for tree-traversal reasoning.  
* **index.ts**: An MCP server that maps high-level reasoning steps to surgical sed operations.  
* **install.sh**: A unified script for cross-platform dependency management and MCP registration.

## **5\. MCTS Navigation Logic**

The RLM employs **Monte Carlo Tree Search (MCTS)** heuristics to navigate the codebase:

* **Selection (Policy):** Use rlm\_reason\_tree to evaluate the most probable module branches.  
* **Expansion (Discovery):** Drill into symbols where docstrings or signatures match the requirement intent.  
* **Simulation (Validation):** Perform a "shallow read" of function signatures to verify data flow compatibility.  
* **Backpropagation (Learning):** Dynamically mark irrelevant branches as "Low Signal" to prune the search space for the remainder of the session.

## **6\. Multi-Agent Orchestration (The Triad)**

To achieve maximum reliability and token efficiency, the system utilizes a tripartite agent configuration:

* **The Explorer (Policy Agent):** Responsible for **Selection** and **Expansion**. It prioritizes breadth, proposing symbolic paths based on AST skeletons.  
* **The Validator (Value Agent):** Responsible for **Simulation**. It surgically analyzes fetched implementation code against technical constraints to verify the Explorer's "guesses."  
* **The Orchestrator (Control Agent):** The central state-manager. It maintains the "Search History," manages the backtracking queue, and decides when to pivot strategies. It prevents "infinite loops" and ensures the Explorer and Validator stay aligned with the original user query.  
* **The Handshake Protocol:** 1\. **Orchestrator** sets the target and depth limit.  
  2\. **Explorer** proposes a node.  
  3\. **Validator** critiques the leaf implementation.  
  4\. **Orchestrator** evaluates the critique; if rejected, it updates the "Blacklist" and triggers a backtrack to the last viable symbolic branch.