"""kb/docs/*.md -> kb_document + kb_chunk。

    make ingest-dry   只切块、打印，不调 AWS、不写库
    make ingest       切块 -> Titan 向量化 -> 写库

两条设计：
  1. **先全部向量化，再一次事务写库。** 向量化要几十次网络调用，中途凭证过期或断网，
     库里不会留下"一半新一半旧"的状态 —— 要么全换，要么不动。
  2. **kb/docs 是唯一来源。** 每次都是整体同步：文件夹里的文档先删后插（重跑结果一样），
     文件夹里已经没有的文档也从库里删掉。

知识库只存在 Postgres（pgvector）里，和取数走 Postgres 还是 Doris 无关，
所以这里直接连 pg_dsn，不经过 db.engine()。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .chunking import Document, load_dir
from .config import settings
from .embed import Embedder, Embedding
from .rag import vector_literal
from .textseg import doc_lexemes

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "kb" / "docs"

SQL_DELETE_DOC = "DELETE FROM kb_document WHERE doc_id = %(doc_id)s"   # chunk 靠 ON DELETE CASCADE
SQL_PRUNE = "DELETE FROM kb_document WHERE NOT (doc_id = ANY(%(keep)s::text[])) RETURNING doc_id"
SQL_INSERT_DOC = """
INSERT INTO kb_document (doc_id, title, doc_type, effective_from, source_uri)
VALUES (%(doc_id)s, %(title)s, %(doc_type)s, %(effective_from)s, %(source_uri)s)
"""
SQL_INSERT_CHUNK = """
INSERT INTO kb_chunk (doc_id, ord, content, token_count, embedding, tsv)
VALUES (%(doc_id)s, %(ord)s, %(content)s, %(token_count)s,
        %(emb)s::vector, array_to_tsvector(%(lex)s::text[]))
"""


def doc_row(doc: Document, root: Path | None = None) -> dict[str, Any]:
    src = Path(doc.source)
    if root is not None:
        try:
            src = src.resolve().relative_to(root.resolve())
        except ValueError:
            pass
    return {
        "doc_id": doc.doc_id,
        "title": doc.meta["title"],
        "doc_type": doc.meta["doc_type"],
        "effective_from": doc.meta.get("effective_from") or None,
        "source_uri": str(src),
    }


def chunk_rows(doc: Document, embeddings: Sequence[Embedding]) -> list[dict[str, Any]]:
    if len(embeddings) != len(doc.chunks):
        raise ValueError(f"{doc.doc_id}: {len(doc.chunks)} 个 chunk，却有 {len(embeddings)} 个向量")
    return [
        {
            "doc_id": c.doc_id,
            "ord": c.ord,
            "content": c.content,               # 带「《标题》 › 章 › 条」前缀，和送去向量化的是同一段
            "token_count": e.tokens,
            "emb": vector_literal(e.vector),
            "lex": doc_lexemes(c.content),
        }
        for c, e in zip(doc.chunks, embeddings)
    ]


def embed_all(docs: Sequence[Document], embedder: Embedder, log=print) -> list[list[Embedding]]:
    total = sum(len(d.chunks) for d in docs)
    out: list[list[Embedding]] = []
    n = 0
    for d in docs:
        embs = []
        for c in d.chunks:
            embs.append(embedder.embed(c.content))
            n += 1
            log(f"  向量化 {n}/{total}  {d.doc_id} #{c.ord}\033[K", end="\r")   # \033[K 清掉上一行残留
        out.append(embs)
    log("")
    return out


def write(cur: Any, docs: Sequence[Document], embedded: Sequence[Sequence[Embedding]],
          root: Path | None = None) -> list[str]:
    """在调用方给的游标上执行；提交与否由调用方决定（测试里用假游标）。返回被清掉的旧文档。"""
    cur.execute(SQL_PRUNE, {"keep": [d.doc_id for d in docs]})
    pruned = [r["doc_id"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
    for d, embs in zip(docs, embedded):
        cur.execute(SQL_DELETE_DOC, {"doc_id": d.doc_id})
        cur.execute(SQL_INSERT_DOC, doc_row(d, root))
        cur.executemany(SQL_INSERT_CHUNK, chunk_rows(d, embs))
    return pruned


def _connect():
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    return psycopg.connect(settings().pg_dsn, row_factory=dict_row)


def _print_plan(docs: Sequence[Document]) -> None:
    for d in docs:
        print(f"{d.doc_id:<14} {d.meta['doc_type']:<8} {len(d.chunks):>3} 块  {d.meta['title']}")
        for c in d.chunks:
            print(f"    #{c.ord:<3} {len(c.content):>4} 字  {c.heading}")
    print(f"共 {len(docs)} 篇，{sum(len(d.chunks) for d in docs)} 块")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 kb/docs 写进知识库")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--dry", action="store_true", help="只切块、打印，不调 AWS、不写库")
    args = ap.parse_args(argv)

    docs = load_dir(args.dir)
    if not docs:
        print(f"{args.dir} 里没有 .md 文件", file=sys.stderr)
        return 2
    _print_plan(docs)
    if args.dry:
        return 0

    from .embed import TitanEmbedder  # noqa: PLC0415

    t0 = time.time()
    try:
        embedded = embed_all(docs, TitanEmbedder())
    except RuntimeError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        return 2
    tokens = sum(e.tokens for embs in embedded for e in embs)
    print(f"✓ 向量化完成：{tokens} tokens，{time.time() - t0:.1f}s")

    root = DEFAULT_DIR.parents[1]
    with _connect() as conn:                         # with 块正常结束才 commit，出错整体回滚
        with conn.cursor() as cur:
            cur.execute("SET search_path = chem, public")
            pruned = write(cur, docs, embedded, root)
            cur.execute(
                "SELECT count(*) AS n, count(embedding) AS n_emb, count(tsv) AS n_tsv FROM kb_chunk"
            )
            stat = cur.fetchone()
    assert stat is not None
    if pruned:
        print(f"  清掉了文件夹里已经没有的文档：{pruned}")
    print(f"✓ 写库完成：kb_chunk {stat['n']} 行，有向量 {stat['n_emb']}，有 tsv {stat['n_tsv']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
