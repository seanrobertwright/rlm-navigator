# **Project Summary: RLM Navigator**

This document summarizes the collaborative design and architecture of the **RLM Navigator**, a codebase-agnostic system built to implement Recursive Language Model (RLM) workflows within modern agentic environments like Claude Code and Gemini.

## **1\. Core Problem & Solution**

* **The Problem:** Large codebases cause "Context Bloat" (exceeding token limits) and "Context Rot" (the AI viewing stale or hallucinated code).  
* **The Solution:** A recursive strategy that treats a codebase as a hierarchical tree. Instead of reading files linearly, the system "Squeezes" them into structural skeletons and only "Drills" into implementation logic when surgically necessary.

## **2\. Technical Stack**

| Component | Technology | Responsibility |
| :---- | :---- | :---- |
| **Watcher Daemon** | Python (watchdog) | Monitors file changes and maintains a live index via Unix Sockets. |
| **Squeezer Engine** | Python (ast, dspy) | Parses code into skeletons and predicts relevant lines for navigation. |
| **MCP Server** | TypeScript (Node.js) | Bridges the local Python tools to the AI Host (Claude/Gemini). |
| **AI Host** | Claude Code / Gemini | Executes the high-level recursive strategy via specific "Skills." |

## **3\. Key Architectural Features**

* **Incremental Indexing:** Avoids the cost of "re-indexing everything" by using file watchers to update only modified AST nodes in milliseconds.  
* **Daemon-Server Handshake:** Uses a Unix Domain Socket (/tmp/rlm\_daemon.sock) to verify the indexer is alive before the AI attempts to navigate.  
* **Symbolic Mapping:** Replaces raw text ingestion with symbolic headers (Class/Function signatures), preserving context window space.  
* **Surgical Drill-Down:** Uses sed and AST line-mapping to fetch exact implementation blocks rather than fuzzy RAG snippets.

## **4\. Workflow Lifecycle**

1. **Init:** The user runs install.sh to set up dependencies and register the MCP server.  
2. **Watch:** The Python daemon runs in the background, keeping the "Live" state of the code ready.  
3. **Query:** The user asks a question in Claude Code.  
4. **Recursive Loop:**  
   * **Check Health:** Verify daemon is active.  
   * **Map:** Call rlm\_map to get the module skeleton.  
   * **Navigate:** The LLM decides which symbol is relevant based on signatures.  
   * **Drill:** Call rlm\_drill to read the specific code lines.  
   * **Update:** If code changes, the daemon updates the skeleton instantly.

## **5\. Deployment Status**

The project is currently structured as an "installable skill set" with a unified installer script, making it ready for use across any Python-based codebase.