"""提示词快照：发给模型的提示词，渲染结果必须和 tests/snapshots/chem_zh/ 里存的逐字一致。

为什么要有它（W1 第 2 步）：提示词从代码里的字符串搬进模板 + 领域包空位，
"搬完一个字都没变"不能靠肉眼看，也不该靠花钱跑评测来证明。快照测试一秒钟就能回答。

快照不是"不许改提示词"，而是"不许无意中改提示词"。真要改的时候：
    cd ai && uv run python tests/test_prompt_snapshots.py --update
然后在提交说明里写清楚改了什么、为什么，并重跑一次评测。
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

import pytest

SNAP = Path(__file__).parent / "snapshots" / "chem_zh"
AS_OF = "2026-09-14"          # 和评测的基准时间一致


def rendered() -> dict[str, str]:
    """当前代码实际会发给模型的提示词（化工领域、中文）。"""
    from chemq import agents, kb_answer, mcp_server, router, text2sql, tools

    return {
        "router_role.txt": router.SYSTEM_ROLE.replace("{DATA_RANGE}", router.data_range()),
        "router_examples.txt": router.FEW_SHOT_EXAMPLES,
        "text2sql_postgres.txt": text2sql.system_role("postgres"),
        "text2sql_doris.txt": text2sql.system_role("doris"),
        "agent.txt": agents.SYSTEM_PROMPT.format(as_of=AS_OF, data_range=router.data_range()),
        "kb_answer.txt": kb_answer.SYSTEM_PROMPT,
        "tool_list_metrics.txt": tools.DESC_LIST_METRICS,
        "tool_run_sql.txt": tools.DESC_RUN_SQL,
        "tool_search.txt": tools.DESC_SEARCH,
        "mcp_instructions.txt": mcp_server.INSTRUCTIONS,
    }


def _is_chem() -> bool:
    from chemq.domain import domain

    return domain().id == "chem"


@pytest.mark.parametrize("name", sorted(rendered()) if SNAP.exists() else [])
def test_prompt_matches_snapshot(name: str) -> None:
    if not _is_chem():
        pytest.skip("快照是化工领域的；CHEMQ_DOMAIN 不是 chem")
    expected = (SNAP / name).read_text(encoding="utf-8")
    got = rendered()[name]
    if got != expected:
        diff = "\n".join(difflib.unified_diff(
            expected.splitlines(), got.splitlines(), "快照", "现在渲染的", lineterm="", n=1))
        pytest.fail(f"{name} 和快照不一致（有意修改就跑 --update）：\n{diff[:3000]}")


def test_every_prompt_has_a_snapshot() -> None:
    """加了新提示词却没拍快照，等于这条提示词不受保护。"""
    assert sorted(p.name for p in SNAP.glob("*.txt")) == sorted(rendered())


if __name__ == "__main__":
    if "--update" not in sys.argv:
        sys.exit("用法：uv run python tests/test_prompt_snapshots.py --update")
    SNAP.mkdir(parents=True, exist_ok=True)
    for name, text in rendered().items():
        (SNAP / name).write_text(text, encoding="utf-8")
        print(f"  写入 {SNAP.name}/{name}（{len(text)} 字符）")
