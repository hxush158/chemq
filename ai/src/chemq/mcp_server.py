"""W4 交付物：把取数能力做成 MCP server。

做完这个之后，你可以在终端里直接：
    claude mcp add chemq -- uv run python -m chemq.mcp_server
然后问 Claude Code「上个月哪个装置能耗最高」，它会自己调你的工具查库。
这一刻你会真正理解 MCP 是什么 —— 它是把"你的系统"接进"任意 Agent"的标准插头。

暴露的工具刻意只有三个，且都是只读：
    list_metrics()           返回指标口径字典
    run_semantic_query(sql)  过 sqlguard 后执行
    search_knowledge(q)      检索制度文档

TODO(W4): 用 mcp 包的 Server 实现上述三个 tool。
注意：MCP server 里同样要过 sqlguard —— 不要因为"调用方是 Claude Code"就放松校验。
参考实现见 ai/solutions/mcp_server_w4.py
"""

if __name__ == "__main__":
    raise SystemExit("W4 任务：实现 MCP server")
