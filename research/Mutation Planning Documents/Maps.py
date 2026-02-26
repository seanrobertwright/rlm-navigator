import ast
import json
import argparse
import sys
from pathlib import Path

class CodebaseNode:
    """Represents a node in the reasoning tree (File, Class, or Function)."""
    def __init__(self, name, node_type, lineno=None, end_lineno=None, docstring=None):
        self.name = name
        self.type = node_type
        self.range = f"{lineno}-{end_lineno}" if lineno else None
        self.docstring = docstring or ""
        self.children = []
        self.metadata = {}

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.type,
            "range": self.range,
            "docstring": self.docstring,
            "metadata": self.metadata,
            "children": [c.to_dict() for c in self.children]
        }

class AlphaGoSqueezer(ast.NodeVisitor):
    """
    AST Visitor that builds a hierarchical JSON tree optimized 
    for Monte Carlo Tree Search (MCTS) selection logic.
    """
    def __init__(self, filename):
        self.filename = filename
        self.root = CodebaseNode(filename, "file")
        self.current_stack = [self.root]

    def visit_ClassDef(self, node):
        cls_node = CodebaseNode(
            node.name, "class", node.lineno, node.end_lineno, ast.get_docstring(node)
        )
        # Heuristic: Count methods for complexity scoring
        cls_node.metadata["method_count"] = len([n for n in node.body if isinstance(n, ast.FunctionDef)])
        
        self.current_stack[-1].children.append(cls_node)
        self.current_stack.append(cls_node)
        self.generic_visit(node)
        self.current_stack.pop()

    def visit_FunctionDef(self, node):
        func_node = CodebaseNode(
            node.name, "function", node.lineno, node.end_lineno, ast.get_docstring(node)
        )
        # Heuristic: Extract arguments as metadata for simulation phase
        func_node.metadata["args"] = [a.arg for a in node.args.args]
        func_node.metadata["is_async"] = False
        
        self.current_stack[-1].children.append(func_node)

    def visit_AsyncFunctionDef(self, node):
        # Same as FunctionDef but flagged as async
        func_node = CodebaseNode(
            node.name, "function", node.lineno, node.end_lineno, ast.get_docstring(node)
        )
        func_node.metadata["args"] = [a.arg for a in node.args.args]
        func_node.metadata["is_async"] = True
        self.current_stack[-1].children.append(func_node)

    def visit_Import(self, node):
        for alias in node.names:
            self.root.metadata.setdefault("imports", []).append(alias.name)

    def visit_ImportFrom(self, node):
        self.root.metadata.setdefault("imports", []).append(f"{node.module}")

def get_mcts_tree(path):
    """Generates the high-fidelity JSON tree for the reasoning engine."""
    try:
        source = Path(path).read_text()
        tree = ast.parse(source)
        squeezer = AlphaGoSqueezer(path)
        squeezer.visit(tree)
        return json.dumps(squeezer.root.to_dict(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "path": path})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RLM AlphaGo Squeezer")
    parser.add_argument("--file", required=True, help="Target source file")
    parser.add_argument("--mode", choices=["tree", "find"], default="tree")
    parser.add_argument("--symbol", help="Symbol to find range for")
    
    args = parser.parse_args()

    if args.mode == "tree":
        print(get_mcts_tree(args.file))
    elif args.mode == "find" and args.symbol:
        # Re-using the tree logic to find a specific range
        tree_data = json.loads(get_mcts_tree(args.file))
        
        def find_range(nodes, target):
            for n in nodes:
                if n["name"] == target: return n["range"]
                res = find_range(n.get("children", []), target)
                if res: return res
            return None
            
        print(find_range([tree_data], args.symbol) or "unknown")