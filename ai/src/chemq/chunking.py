"""知识文档 -> 检索单元（chunk）。只用标准库，可单独测试。

切法：
  · 制度类（doc_type=制度）按条款切，也就是 `###` 标题。条款本身就是语义单元，
    很多只有一两百 token，比"400~800 token"的通用建议小 —— 对制度类这是对的，
    不要为了凑长度把两个条款拼在一起，那会让一个 chunk 同时回答两个问题、引用不准。
  · 其他文档按 `##` 切，`###` 小节留在所属的 `##` 里（例如"回款率"下面的
    "回款率与财务应收账龄表为什么对不上"，拆开就丢了它在解释谁）。
  · 第一个切分标题之前的正文（导言）单独成一块，标题记作"概述"。

每个 chunk 的正文前面加一行「《文档标题》 › 章 › 条」：
单独看"检修期间合格产量为零……"这段，不带上下文，向量和模型都不知道说的是哪份制度。
这行同时参与向量化和 BM25 —— 这就是所谓 contextual chunking 的最简版本。

按标题切本身就没有重叠，所以不做 overlap。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

REQUIRED_META = ("doc_id", "title", "doc_type")


@dataclass
class Chunk:
    doc_id: str
    ord: int
    path: list[str]          # [文档标题, 章, 条]，供评测按章节判断命中
    body: str

    @property
    def content(self) -> str:
        """写进 kb_chunk.content、送去向量化的文本：路径 + 正文。"""
        return "《" + self.path[0] + "》 › " + " › ".join(self.path[1:]) + "\n" + self.body

    @property
    def heading(self) -> str:
        return " › ".join(self.path[1:])


@dataclass
class Document:
    meta: dict[str, str]
    text: str
    source: str = ""
    chunks: list[Chunk] = field(default_factory=list)

    @property
    def doc_id(self) -> str:
        return self.meta["doc_id"]


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """只支持扁平的 `key: value`，够用且不依赖 yaml 库。"""
    m = _FRONT.match(raw)
    if not m:
        raise ValueError("文档开头缺少 front matter（--- ... ---）")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        k, sep, v = line.partition(":")
        if not sep:
            raise ValueError(f"front matter 这一行看不懂：{line!r}")
        meta[k.strip()] = v.strip()
    missing = [k for k in REQUIRED_META if not meta.get(k)]
    if missing:
        raise ValueError(f"front matter 缺少字段：{missing}")
    return meta, raw[m.end():]


def chunk_document(meta: dict[str, str], text: str) -> list[Chunk]:
    title = meta["title"]
    split_level = 3 if meta["doc_type"] == "制度" else 2

    chunks: list[Chunk] = []
    heads: dict[int, str] = {}          # 当前所处的各级标题
    cur_path: list[str] | None = None
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body and cur_path is not None:
            chunks.append(Chunk(meta["doc_id"], len(chunks), cur_path, body))
        buf.clear()

    for line in text.splitlines():
        m = _HEAD.match(line)
        level = len(m.group(1)) if m else 0
        if m and level == 1:
            continue                                   # 一级标题就是文档标题，已在 meta 里
        if m and level <= split_level:
            flush()
            heads = {k: v for k, v in heads.items() if k < level}
            heads[level] = m.group(2)
            cur_path = [title] + [heads[k] for k in sorted(heads)]
            continue
        if cur_path is None and line.strip():
            cur_path = [title, "概述"]                 # 第一个切分标题之前的导言
        buf.append(line)
    flush()
    return chunks


def load_document(path: str | Path) -> Document:
    path = Path(path)
    meta, text = parse_front_matter(path.read_text(encoding="utf-8"))
    doc = Document(meta=meta, text=text, source=str(path))
    doc.chunks = chunk_document(meta, text)
    return doc


def load_dir(folder: str | Path) -> list[Document]:
    docs = [load_document(p) for p in sorted(Path(folder).glob("*.md"))]
    ids = [d.doc_id for d in docs]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"doc_id 重复：{sorted(dup)}")
    return docs
