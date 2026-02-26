## **name: recursive\_navigator description: Enforces a recursive, token-efficient RLM workflow for large codebases.**

# **Recursive Navigator Skill**

1. **Check Health**: Always run get\_status first.  
2. **Map Structure**: Use rlm\_map to see signatures only. NEVER cat files \> 100 lines.  
3. **Drill Down**: Use rlm\_drill to fetch implementation only when logic is identified as relevant.  
4. **Context Rule**: Clear context between unrelated module deep-dives.