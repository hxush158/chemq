"""W4：把取数能力做成 MCP server（stdio）。

在终端里挂进 Claude Code：
    claude mcp add chemq -- uv run --directory <仓库>/ai python -m chemq.mcp_server
然后问 Claude Code「上个月哪个装置单位能耗最高」，它会自己调这里的工具查库。

工具的实现全在 tools.py（和 Agent SDK 编排共用一份），这里只做"接线"。
暴露的工具刻意只有三个，都是只读；run_semantic_query 照样过 sqlguard。

行级权限：region 从环境变量 CHEMQ_REGION 读，**不是工具参数** —— 见 tools.py 的说明。

⚠ stdio 模式下 stdout 是协议通道，这个进程里任何 print 都会把协议打坏。要输出调试信息用 stderr。
"""
from __future__ import annotations

import os
from typing import Any

from . import tools

INSTRUCTIONS = (
    "示例化工经营数据的只读查询工具。写 SQL 之前先调 list_metrics 看指标口径和语义层目录；"
    "数字只能来自 run_semantic_query 的结果；制度、口径解释用 search_knowledge，"
    "found=false 时如实说知识库里没有。"
)


def _server_class() -> Any:
    """mcp 2.x 把 FastMCP 改名为 MCPServer，导入路径也换了；三种写法依次试。"""
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: PLC0415  # 2.x
        return MCPServer
    except ImportError:
        pass
    try:
        from mcp.server import MCPServer  # type: ignore[attr-defined]  # noqa: PLC0415
        return MCPServer
    except ImportError:
        from mcp.server.fastmcp import FastMCP  # noqa: PLC0415  # 1.x
        return FastMCP


def build_server(region: str | None = None) -> Any:
    server = _server_class()("chemq", instructions=INSTRUCTIONS)

    @server.tool()
    def list_metrics() -> str:
        """返回指标口径字典和语义层目录（能查哪些视图、有哪些列、维度取值）。写 SQL 前先调这个。"""
        return tools.list_metrics()

    @server.tool()
    def run_semantic_query(sql: str) -> dict:
        """执行一条只读 SQL（PostgreSQL）。只能查语义层目录里的视图和维度表，会自动加 LIMIT。
        返回 ok/rows；ok=false 时看 violations 或 error，改了再试。"""
        return tools.run_semantic_query(sql, region=region)

    @server.tool()
    def search_knowledge(question: str) -> dict:
        """检索公司制度、指标口径、月度经营分析报告。found=false 表示知识库里没有相关资料。"""
        return tools.search_knowledge(question)

    return server


def main() -> None:
    build_server(region=os.environ.get("CHEMQ_REGION") or None).run(transport="stdio")


if __name__ == "__main__":
    main()
