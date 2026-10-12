"""切块单测 —— 只用标准库和仓库里的 kb/docs，不连数据库、不调 AWS。"""
import json
from pathlib import Path

import pytest

from chemq.chunking import chunk_document, load_dir, parse_front_matter

ROOT = Path(__file__).resolve().parents[2]
KB = ROOT / "kb" / "docs"
GOLDEN = ROOT / "ai" / "evals" / "kb_golden.jsonl"


@pytest.fixture(scope="module")
def docs():
    return {d.doc_id: d for d in load_dir(KB)}


def test_chunk_counts(docs):
    """制度按条款（###）切，其他按 ## 切。数量变了说明切法或文档变了，要有意识地改这里。"""
    assert {k: len(d.chunks) for k, d in docs.items()} == {
        "KB-ENE-2026": 11, "KB-OPS-STOP": 7, "KB-MET-2026": 11, "KB-RPT-202603": 5,
    }


def test_every_golden_label_resolves(docs):
    """评测集标的每个 (doc_id, section) 都必须对得上某个 chunk，否则 Recall 永远算不对。"""
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        q = json.loads(line)
        for r in q.get("relevant", []):
            heads = [c.heading for c in docs[r["doc_id"]].chunks]
            assert any(r["section"] in h for h in heads), (q["id"], r)


def test_content_carries_path_prefix(docs):
    c = next(c for c in docs["KB-ENE-2026"].chunks if "第八条" in c.heading)
    assert c.content.startswith("《示例化工能耗考核管理办法（2026 版）》 › 第三章 考核 › 第八条")
    assert "不作为能效下降的考核依据" in c.body


def test_subsection_stays_with_parent(docs):
    """「回款率」下面的「账龄表」小节不能被拆走 —— 拆开就不知道它在解释谁了。"""
    c = next(c for c in docs["KB-MET-2026"].chunks if c.heading == "回款率")
    assert "收付实现" in c.body and "账龄表" in c.body


def test_intro_becomes_overview_and_h1_is_skipped():
    meta = {"doc_id": "X", "title": "T", "doc_type": "口径字典"}
    chunks = chunk_document(meta, "# T\n\n导言一句。\n\n## 甲\n甲的内容\n\n## 乙\n乙的内容\n")
    assert [c.heading for c in chunks] == ["概述", "甲", "乙"]
    assert chunks[0].body == "导言一句。"


def test_empty_sections_are_dropped():
    meta = {"doc_id": "X", "title": "T", "doc_type": "制度"}
    chunks = chunk_document(meta, "## 第一章\n### 第一条\n内容\n### 第二条\n\n")
    assert [c.heading for c in chunks] == ["第一章 › 第一条"]


@pytest.mark.parametrize("raw, msg", [
    ("没有 front matter", "front matter"),
    ("---\ndoc_id: A\ntitle: B\n---\n正文", "doc_type"),
])
def test_front_matter_errors(raw, msg):
    with pytest.raises(ValueError, match=msg):
        parse_front_matter(raw)
