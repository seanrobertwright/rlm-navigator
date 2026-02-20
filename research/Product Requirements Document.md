# **PRD: RLM Navigator (Codebase-Agnostic Plugin)**

## **1\. Executive Summary**

The RLM Navigator is a toolset for Claude Code, Gemini, and Codex that implements the Recursive Language Model (RLM) paradigm for software development. It solves the "Context Crisis" by treating the codebase as a hierarchical tree of skeletons. It uses a background daemon for reactive indexing and a TypeScript MCP server to bridge local AST-based "squeezing" to the LLM.

## **2\. Goals & Objectives**

* **Zero Context Bloat:** Never load more than 500 tokens of file structure at once.  
* **Eliminate Context Rot:** Use a file-watcher daemon to ensure the AI always sees the "Live" state of the code.  
* **Cross-Platform:** An installable set that works with any host supporting the Model Context Protocol (MCP).  
* **Token Efficiency:** 90% reduction in token usage for codebase exploration compared to standard cat or ls commands.

## **3\. Target Workflow**

1. **Initialize:** rlm init creates a local symbol map and starts the daemon.  
2. **Watch:** The daemon monitors for file changes and incrementally updates the AST skeletons.  
3. **Navigate:** The LLM uses the recursive\_navigator skill to map directories.  
4. **Drill Down:** The LLM surgicaly fetches implementation logic for specific symbols using the Squeezer.

## **4\. Technical Architecture**

* **Language:** TypeScript (MCP Server), Python (Daemon/Squeezer).  
* **Protocol:** Model Context Protocol (MCP) via Stdio.  
* **Parsing:** Python ast module (Squeezer) \+ watchdog (Indexing).  
* **Handshake:** Unix Domain Sockets for Daemon-to-MCP communication.