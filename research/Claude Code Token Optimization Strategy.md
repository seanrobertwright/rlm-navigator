# **Session Log: Claude Code Token Optimization & Skill Framework**

**Date:** February 19, 2026

**Participants:** User & Hal (Gemini)

## **1\. Initial Inquiry: Strategies to Minimize Token Usage**

**User:** Requested a comprehensive list of strategies to minimize/improve token usage in Claude Code due to high burn rates.

**Hal:** Identified the "Agent Tax" (autonomous exploration) as the primary cause and provided a five-pillar strategy:

### **A. Surgical Command-Line Habits**

* **/clear Ruthlessly:** Clear session history after every successful fix.  
* **Manual /compact:** Force a summary of history to reduce context bloat.  
* **Kill Planning Loops:** Use /plan or Shift+Tab to approve roadmaps before execution.  
* **Interrupt with Esc:** Stop Claude immediately if it starts reading massive, irrelevant files.

### **B. Context Management & Indexing**

* **Targeted File References:** Use specific paths (e.g., @src/auth/service.ts:10-50) instead of vague descriptions.  
* **Codebase Indexing:** Use tools like qmd or MCP indexers to avoid expensive grep calls.

### **C. MCP and Tool Optimization**

* **Prune MCPs:** Disable unused MCP servers (e.g., GitHub, Slack) to reduce system prompt "tax."  
* **Tool Search:** Enable ENABLE\_TOOL\_SEARCH=auto:5 to load tool definitions only when needed.

### **D. Configuration & Model Choice**

* **\--dangerously-skip-permissions:** Reduce turn count by skipping permission prompts.  
* **Model Switching:** Use Haiku for exploration and Sonnet/Opus only for implementation.  
* **Limit Thinking:** Set MAX\_THINKING\_TOKENS to a lower budget (e.g., 4000).

### **E. Structured Knowledge (CLAUDE.md)**

* Move non-essential documentation to separate files.  
* Add "Conciseness" rules to instructions.

## **2\. The High-Efficiency CLAUDE.md Template**

**Hal** provided a template designed to prevent exploration loops by acting as a map for Claude.

\# Project Context: \[Project Name\]

\#\# Strategy: Token Conservation  
\- \*\*Surgical Edits:\*\* Use \`sed\` or targeted line-edits. Avoid rewriting files \>100 lines.  
\- \*\*Minimalist Responses:\*\* Omit conversational filler and code explanations.  
\- \*\*Context Management:\*\* If context exceeds 30k tokens, prompt user to \`/clear\` or \`/compact\`.

\#\# Tech Stack & Architecture  
\- \*\*Language:\*\* Python 3.11+ / TypeScript  
\- \*\*Entry Point:\*\* \`src/main.py\`  
\- \*\*Key Files:\*\* (List specifically where 'brains' live to stop exploration)

\#\# Common Surgical Commands  
\- \*\*Check Types:\*\* \`mypy .\`  
\- \*\*Fast Search:\*\* \`grep \-r "pattern" src/\`

## **3\. The "Skill System" Approach**

**User:** Suggested building a skills system to reduce token usage and context issues.

**Hal:** Proposed a modular architecture using **DSPy** to compile "Surgical Skills" that move reasoning out of the high-cost chat context and into local execution scripts.

## **4\. Implementation: The Context Squeezer (squeezer.py)**

**Hal** provided a Python script using the ast module to create a "skeleton" of a file (signatures only), allowing Claude to "see" 1,000 lines of code for the token cost of 50\.

import ast  
import sys  
import os

def squeeze\_file(filepath):  
    if not os.path.exists(filepath):  
        return f"Error: File {filepath} not found."  
    with open(filepath, "r") as f:  
        tree \= ast.parse(f.read())  
    skeleton \= \[\]  
    for node in tree.body:  
        if isinstance(node, ast.ClassDef):  
            skeleton.append(f"class {node.name}:")  
            for item in node.body:  
                if isinstance(item, ast.FunctionDef):  
                    args \= ast.unparse(item.args)  
                    skeleton.append(f"    def {item.name}({args}): ...")  
        elif isinstance(node, ast.FunctionDef):  
            args \= ast.unparse(node.args)  
            skeleton.append(f"def {node.name}({args}): ...")  
    return "\\n".join(skeleton)

if \_\_name\_\_ \== "\_\_main\_\_":  
    if len(sys.argv) \> 1:  
        print(squeeze\_file(sys.argv\[1\]))

## **5\. The Unified Navigator (Maps.py)**

**Hal** integrated the Squeezer with a **DSPy Signature** to create a tool that predicts the exact surgical edit point and provides a shell command to fetch the lines.

import ast  
import sys  
import os  
import dspy

class TargetedCodeFetch(dspy.Signature):  
    """Analyzes a code skeleton and task to find the surgical edit point."""  
    squeezed\_context \= dspy.InputField(desc="The output from the AST squeezer.")  
    task \= dspy.InputField(desc="The coding task or bug description.")  
    rationale \= dspy.OutputField(desc="Why these lines matter.")  
    target\_function \= dspy.OutputField(desc="The specific function/class to read.")  
    shell\_command \= dspy.OutputField(desc="A grep command to fetch the lines.")

def main(file\_path, task):  
    skeleton \= squeeze\_file(file\_path)  
    \# Configure with local LLM (Ollama/Claude)  
    lm \= dspy.LM('openai/gpt-4o-mini')   
    dspy.configure(lm=lm)  
    navigator \= dspy.TypedPredictor(TargetedCodeFetch)  
    prediction \= navigator(squeezed\_context=skeleton, task=task)  
      
    print(f"RATIONALE: {prediction.rationale}")  
    print(f"EXECUTE THIS: {prediction.shell\_command}")

if \_\_name\_\_ \== "\_\_main\_\_":  
    main(sys.argv\[1\], " ".join(sys.argv\[2:\]))

## **6\. Conclusion**

The combination of a **Surgical Navigator**, a **Context Squeezer**, and a **Minimalist CLAUDE.md** effectively shifts the burden of "finding the code" from the expensive LLM chat turns to cheap local execution and targeted data retrieval.