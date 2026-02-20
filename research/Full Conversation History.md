# **RLM Navigator: Full Development Transcript**

This document contains the complete record of the conversation and technical development for the **RLM Navigator** project—a codebase-agnostic, recursive context management system for AI agents.

## **1\. Initial Inquiry: The Problem of Re-Indexing**

**User:** Is using RLMs an issue with codebases? I'm thinking every time the code changes, the RLM needs to re-index everything. Is this true? Are there ways around it?

**Hal:** RLMs (Recursive Language Models) actually solve the re-indexing problem common in traditional RAG. Unlike vector-based systems that require massive embedding updates, RLMs treat the codebase as a live environment. By using incremental indexing—where only modified files are re-parsed—the cost of a change is reduced from ![][image1] to ![][image2].

## **2\. Defining the Strategy: Codebase as a Large Document**

**User:** I'm thinking of implementing a mutation of the repo claude\_code\_RLM toward an entire codebase INSTEAD of a large document... treat the entire codebase as a LARGE DOCUMENT. Does that make sense?

**Hal:** Yes, it makes perfect sense. Code is inherently hierarchical (Folders → Files → Classes → Functions), which fits the RLM "Tree" structure better than flat text. Instead of "reading" the codebase, the AI "navigates" it.

* **Root Node:** High-level symbolic map.  
* **Branch Nodes:** Module/File signatures.  
* **Leaf Nodes:** Implementation logic (Functions/Methods).

## **3\. Architecture: The MCP Bridge & Skills**

**User:** I'm looking to make this a plugin for Claude Code, Gemini, Codex, etc... I am also VERY concerned about token usage and MCPs can be token hogs.

**Hal:** To save tokens, we shift from standard tool-calls to **Skills**.

* **Offload Discovery:** The local machine ranks files/symbols *before* the LLM sees them.  
* **Summary-First:** Never send full code in the discovery phase.  
* **Depth-Limited Context:** Each recursive call resets the context window, preventing "Context Rot."

## **4\. The "Squeezer" and "Navigator"**

**User:** (Regarding the integration of AST and DSPy logic)

**Hal:** We use a two-part engine:

1. **The Squeezer (Maps.py):** Uses Python's ast module to strip implementations, leaving only signatures and docstrings.  
2. **The Navigator (DSPy):** Predicts the minimal line ranges needed for a specific task using the TargetedCodeFetch signature.

## **5\. Implementation: The RLM Watchtower**

**User:** How does the skill know the daemon is running? I want to initialize the system \-\> index codebase... when a file is modified, it gets re-indexed.

**Hal:** We implement a **Unix Domain Socket** handshake.

* **Daemon (rlm\_daemon.py):** A background process using watchdog to update the index and listening on /tmp/rlm\_daemon.sock.  
* **MCP Server (index.ts):** Checks the socket health before providing tools to the AI Host.

## **6\. Final Project File Definitions**

### **daemon/Maps.py**

import ast  
import argparse  
from pathlib import Path

class CodeSqueezer(ast.NodeVisitor):  
    def \_\_init\_\_(self):  
        self.skeleton \= \[\]

    def visit\_ClassDef(self, node):  
        self.skeleton.append(f"class {node.name}:")  
        self.generic\_visit(node)

    def visit\_FunctionDef(self, node):  
        args \= ast.unparse(node.args)  
        doc \= ast.get\_docstring(node)  
        self.skeleton.append(f"    def {node.name}({args}):")  
        if doc: self.skeleton.append(f'        """{doc}"""')  
        self.skeleton.append(f"        pass")

def squeeze(path):  
    try:  
        tree \= ast.parse(Path(path).read\_text())  
        s \= CodeSqueezer()  
        s.visit(tree)  
        return "\\n".join(s.skeleton)  
    except Exception as e:  
        return f"Error squeezing {path}: {str(e)}"

def find\_symbol(path, name):  
    tree \= ast.parse(Path(path).read\_text())  
    for node in ast.walk(tree):  
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name \== name:  
            return f"{node.lineno}-{node.end\_lineno}"  
    return None

if \_\_name\_\_ \== "\_\_main\_\_":  
    parser \= argparse.ArgumentParser()  
    parser.add\_argument("--file", required=True)  
    parser.add\_argument("--mode", choices=\["squeeze", "find"\], default="squeeze")  
    parser.add\_argument("--symbol")  
    args \= parser.parse\_args()

    if args.mode \== "squeeze":  
        print(squeeze(args.file))  
    elif args.mode \== "find" and args.symbol:  
        print(find\_symbol(args.file, args.symbol))

### **daemon/rlm\_daemon.py**

import os  
import socket  
from watchdog.observers import Observer  
from watchdog.events import FileSystemEventHandler

SOCKET\_PATH \= "/tmp/rlm\_daemon.sock"

class RLMHandler(FileSystemEventHandler):  
    def on\_modified(self, event):  
        if event.src\_path.endswith('.py'):  
            print(f"\[\*\] Live Update: {event.src\_path}")

def run\_server():  
    if os.path.exists(SOCKET\_PATH): os.remove(SOCKET\_PATH)  
    server \= socket.socket(socket.AF\_UNIX, socket.SOCK\_STREAM)  
    server.bind(SOCKET\_PATH)  
    server.listen(5)  
    while True:  
        conn, \_ \= server.accept()  
        conn.sendall(b"ALIVE")  
        conn.close()

if \_\_name\_\_ \== "\_\_main\_\_":  
    observer \= Observer()  
    observer.schedule(RLMHandler(), ".", recursive=True)  
    observer.start()  
    run\_server()

### **server/src/index.ts (MCP Server)**

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";  
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";  
import { z } from "zod";  
import { execSync } from "child\_process";  
import net from "net";

const server \= new McpServer({ name: "rlm-navigator", version: "1.0.0" });

server.tool("get\_status", "Check RLM daemon health", {}, async () \=\> {  
    return new Promise((resolve) \=\> {  
        const client \= net.createConnection({ path: "/tmp/rlm\_daemon.sock" });  
        client.on('connect', () \=\> { client.end(); resolve({ content: \[{ type: "text", text: "ONLINE" }\] }); });  
        client.on('error', () \=\> resolve({ content: \[{ type: "text", text: "OFFLINE" }\] }));  
    });  
});

server.tool("rlm\_map", "Get structural skeleton", { path: z.string() }, async ({ path }) \=\> {  
    const out \= execSync(\`python3 ../daemon/Maps.py \--file ${path} \--mode squeeze\`).toString();  
    return { content: \[{ type: "text", text: out }\] };  
});

server.tool("rlm\_drill", "Surgical read", { path: z.string(), symbol: z.string() }, async ({ path, symbol }) \=\> {  
    const range \= execSync(\`python3 ../daemon/Maps.py \--file ${path} \--mode find \--symbol ${symbol}\`).toString().trim();  
    const \[start, end\] \= range.split('-');  
    const code \= execSync(\`sed \-n '${start},${end}p' ${path}\`).toString();  
    return { content: \[{ type: "text", text: code }\] };  
});

const transport \= new StdioServerTransport();  
await server.connect(transport);

### **skills/recursive\_navigator.md**

\---  
name: recursive\_navigator  
description: Enforces a recursive, token-efficient RLM workflow for large codebases.  
\---  
\# Recursive Navigator Skill  
1\. \*\*Check Health\*\*: Always run \`get\_status\` first.  
2\. \*\*Map Structure\*\*: Use \`rlm\_map\` to see signatures only. NEVER \`cat\` files \> 100 lines.  
3\. \*\*Drill Down\*\*: Use \`rlm\_drill\` to fetch implementation logic.

### **install.sh**

\#\!/bin/bash  
echo "Installing RLM Navigator..."  
cd daemon && pip install watchdog dspy-ai  
cd ../server && npm install && npm run build  
claude config add-mcp rlm-navigator node $(pwd)/build/index.js  
echo "Installation Complete."

## **7\. Summary of Philosophy**

The RLM Navigator moves away from "Vibe Coding" toward "Structural Navigation." By separating the daemon (live index) from the MCP server (interface), we ensure that the AI always has a thin, accurate slice of the codebase in its context window.

[image1]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACoAAAAYCAYAAACMcW/9AAACKUlEQVR4Xu2WPS9DURjHKyoxkBBt6eutjURiECaTiLCYDBI+gr2JyWcgFovJJxCJwScwMzEYJAZDZ8TL/7me0zz333No3dr6S256/Z6X89zT0xuZTJ8+fsrlcqVara6xDzAQRVGTZado7RD7IChYwfVZq9V2ncP9vTjcDpjUBBpPRcc9kPiKod7ZC/BX0kh22RO7xs5vs++WSqWyjl537BPIgL89kcQ5R5qzS8OPvdxu2a/bh29Q1DyyS4POscBeyPoG8OHLU3dhnQPHZAKxWfd3vV4fxhBb+ByzeRbkn+F6YN/aEXxecswii4QGxde/YZ36l1KpNIO+b7jOtW5QYnLMcN1QSQzO+iKvEeMWx2JljlnQuMGD5vP5EXVFmysg/1Q/33hhd9Ssc+DhchJDzngiwIuHcHm49owripOBbW7m+zUWv8q0ZscG1T1Z5wg+vBsgIT348qSZOM+gLSTOu6OuYZ0j1aBoeq05WeuDTRXULXNvGZqdJZfLjUq8UChMJgKQTQngEM8lAkqxKJsWn+Eljglau8heQOyBh7IOQ9/amLoFrmmhu9IWxACb4vGLn+KYQ2vP2Gf0tYeFD63UB9uXe98vX/KjwPmNQfGRG9hcx5zHoPGH5LLHO3RefeK4wM3qAzxb75B+uFbZtxEFzlsIND3wDfpXetmrDTR/CZ3xbkCfaenFvqf0Yid60eNX5O2AY3DFvlNQeyI92P8Xnf+H3k6a2j5d8QV1ML+W0jCK7wAAAABJRU5ErkJggg==>

[image2]: <data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAC8AAAAYCAYAAABqWKS5AAACKklEQVR4Xu2WPUsDQRCGE0hAwU5zZ3KXSxA7BSsFWwvRxlqIP8DCysbWnyCK2Nja2AsW+QGSWrBQOzsLOyGCH++cu2HzMveRD8EiDyxc3pmdmZ3b3UuhMGHC+AmCIKzX65usJ1BsNBpvLCqUoih6Z1GhDL9nFlNBARsY35i4ZzU8P4mGx6Lj2oexZyILFF80ZpZtDBq3j9wXrKsg6AecP1kXoLdN0lCxdZBol3UGfieI0ZU4Ayw220+KznLUkoZhuMVaEsZPts2XPFcqlRn2YeB7DN8H1nvYrrpbRUMrHnNeWNPAIhfhd2me4wVj7i37MbVabS4tfkkrSkPzM9qNq2kkzMvMKST62c5ldaHZbE5pCeU3OrntaozneT7iX7uanBGTt0/XMH4LrPc6gAICtrlg8hEXL3vWaFXXl+EFW2w8jHW2uRifFuu5X5+T6MDRqqKlHTy5EpPeqsTKk1/s0jzWBy6etMziYb/DW51m3eLETf2GDF08JnaMT8nV82ybHLHtmXtlm0XsOCOHrIsh/uLBuMw2oVqV5sZnYo1tgpm7yrog2yXHl1T+VqQ2UGyIs8J6TNJkFLUjOm6aebZZzNwr1gUbN+/AYh85hiA21vpAoWccDOOc/ZjIfC0V/VSJlzk4jr2iWVdppOxfjej3850v+BAgdkvOBetjAwm6SWdmVExj+i6KsfMX3UfHlzDuWR87cishUZv1UfiLhqRRZmFYfN/3WJvwH/gBT5jm6eqkOF0AAAAASUVORK5CYII=>