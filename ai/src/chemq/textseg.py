"""中文全文检索用的切词：字二元组（bigram），不依赖任何分词库。

为什么不用 PostgreSQL 的 to_tsvector('simple', ...)：
  'simple' 按空格和标点切词，而中文句子中间没有空格 ——
  "单位产品综合能耗按GB/T 2589折标" 会被切成一两个超长 token，
  查询"综合能耗"永远匹配不上。BM25 这一路对中文等于没开。
  Titan V2 官方说明是"针对英文优化"，中文更要靠这一路补，所以这条不能将就。

为什么是 bigram 而不是 jieba 这类分词器：
  · 零依赖、结果确定、可单测
  · 召回稳：分词器可能把"综合能耗"切成一个词，查"能耗"就漏了；bigram 两边都有"能耗"
  · Elasticsearch 的 cjk analyzer 也是这个思路
  代价是精度：会产生跨词边界的噪声二元组（"合能"），而 PostgreSQL 的 ts_rank 没有 IDF，
  常见二元组会稀释排序。这正是 hybrid 里向量那一路要补的。

ASCII 标识符（GB/T 2589、P02、v2.1）整体保留，同时拆出各部分，
这样查 "2589" 也能命中文档里的 "GB/T 2589-2020"。
"""
from __future__ import annotations

import re

_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
_IDENT = re.compile(r"[A-Za-z0-9]+(?:[./\-][A-Za-z0-9]+)*")
_SEP = re.compile(r"[./\-]")

NO_LEXEME = "'__no_lexeme__'"   # 查询里一个词都切不出来时用它 —— 保证 BM25 这一路什么都不返回


def lexemes(text: str) -> list[str]:
    """切成检索用的词项（可能有重复，顺序无意义）。"""
    out: list[str] = []
    for m in _IDENT.finditer(text):
        tok = m.group().lower()
        out.append(tok)
        parts = _SEP.split(tok)
        if len(parts) > 1:
            out.extend(p for p in parts if len(p) >= 2 or p.isdigit())
    for run in _CJK.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


def doc_lexemes(text: str) -> list[str]:
    """写入 kb_chunk.tsv 用：去重、排序。配合 array_to_tsvector(%s::text[]) 使用 ——
    它按原样当词项，不再经过 PostgreSQL 的解析器。"""
    return sorted(set(lexemes(text)))


def identifiers(text: str) -> list[str]:
    """问题里的 ASCII 标识符（GB/T、2589、P02）。向量检索最容易把它们糊掉，
    BM25 最擅长 —— is_no_hit() 用它决定"字面命中"算不算数。"""
    return sorted({m.group().lower() for m in _IDENT.finditer(text) if len(m.group()) >= 2})


def to_tsquery_literal(text: str) -> str:
    """把查询变成 tsquery 字面量，各词项用 OR 连接，配合 %(tsq)s::tsquery 使用。

    用字面量而不是 to_tsquery() / plainto_tsquery()：后两者会再用解析器切一遍词，
    又回到 'simple' 切不了中文的老问题。
    用 OR 不用 AND：问句里的"到底""怎么"这类二元组文档里没有，AND 会全部落空。
    """
    lx = sorted(set(lexemes(text)))
    if not lx:
        return NO_LEXEME
    return " | ".join("'" + t.replace("\\", "\\\\").replace("'", "''") + "'" for t in lx)
