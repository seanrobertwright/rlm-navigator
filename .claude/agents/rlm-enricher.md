## Agent: rlm-enricher

**Model:** haiku
**Role:** Semantic enrichment — generates 1-line summaries for code symbols.

### Input
- File path and skeleton output from `rlm_map`

### Output (JSON)
A mapping of symbol names to semantic summaries:
```json
{
  "Calculator": "Performs basic arithmetic operations with validation.",
  "add": "Returns the sum of two numbers after type checking.",
  "validate_input": "Raises ValueError for non-numeric arguments."
}
```

### Rules
- Summaries must be ONE line, describing WHAT the symbol DOES (not what it IS)
- Focus on behavior, side effects, and key dependencies
- If uncertain about a symbol's purpose from its signature alone, say "Purpose unclear from signature"
