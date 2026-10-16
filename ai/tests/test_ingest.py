"""ingest 单测 —— 用假的 Embedder 和假游标，不连数据库、不调 AWS。"""
import io
import json
from typing import Any

import pytest

from chemq import ingest
from chemq.chunking import Document, chunk_document
from chemq.embed import Embedding, TitanEmbedder
from chemq.textseg import doc_lexemes

META = {"doc_id": "D1", "title": "测试制度", "doc_type": "制度", "effective_from": "2026-01-01"}
TEXT = "## 第一章\n### 第一条 目的\n规范能耗考核。\n### 第二条 范围\n适用于 P01 装置。\n"


def _doc() -> Document:
    d = Document(meta=META, text=TEXT, source="kb/docs/d1.md")
    d.chunks = chunk_document(META, TEXT)
    return d


class FakeEmbedder:
    model, dim = "fake", 4

    def __init__(self):
        self.seen: list[str] = []

    def embed(self, text):
        self.seen.append(text)
        return Embedding(vector=[0.5, 0.5, 0.5, 0.5], tokens=len(text))


class FakeCursor:
    def __init__(self, existing=()):
        self.calls: list[tuple[str, Any]] = []
        self._rows = [{"doc_id": x} for x in existing]

    def execute(self, sql, params=None):
        self.calls.append((sql.strip().split()[0], params))

    def executemany(self, sql, rows):
        self.calls.append(("INSERT_MANY", list(rows)))

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


def test_embeds_the_prefixed_content_not_the_bare_body():
    emb = FakeEmbedder()
    ingest.embed_all([_doc()], emb, log=lambda *a, **k: None)
    assert emb.seen[0].startswith("《测试制度》 › 第一章 › 第一条 目的\n")


def test_chunk_rows_shape():
    d = _doc()
    rows = ingest.chunk_rows(d, [Embedding([0.1] * 4, 7), Embedding([0.2] * 4, 9)])
    assert [r["ord"] for r in rows] == [0, 1]
    assert rows[1]["token_count"] == 9
    assert rows[0]["emb"].startswith("[") and rows[0]["emb"].endswith("]")
    assert rows[1]["lex"] == doc_lexemes(rows[1]["content"])
    assert "p01" in rows[1]["lex"]                         # 标识符进了 BM25


def test_chunk_rows_rejects_count_mismatch():
    with pytest.raises(ValueError, match="2 个 chunk"):
        ingest.chunk_rows(_doc(), [Embedding([0.1] * 4, 1)])


def test_write_prunes_then_deletes_before_insert():
    cur = FakeCursor(existing=["OLD"])
    d = _doc()
    pruned = ingest.write(cur, [d], [[Embedding([0.1] * 4, 1)] * 2])
    assert pruned == ["OLD"]
    assert [c[0] for c in cur.calls] == ["DELETE", "DELETE", "INSERT", "INSERT_MANY"]
    assert cur.calls[0][1] == {"keep": ["D1"]}
    assert len(cur.calls[3][1]) == 2


def test_doc_row_uses_relative_source_and_nulls_empty_date(tmp_path):
    d = _doc()
    d.meta = {**META, "effective_from": ""}
    d.source = str(tmp_path / "kb" / "docs" / "d1.md")
    row = ingest.doc_row(d, root=tmp_path)
    assert row["source_uri"] == "kb/docs/d1.md"
    assert row["effective_from"] is None


def test_dry_run_needs_no_aws_or_db(capsys):
    assert ingest.main(["--dry"]) == 0
    assert "共 4 篇，34 块" in capsys.readouterr().out


# ---------------- TitanEmbedder：假的 boto3 client ----------------

class FakeBedrock:
    def __init__(self, dim=1024, fail=None):
        self.dim, self.fail = dim, fail
        self.req: dict[str, Any] = {}

    def invoke_model(self, **kw):
        if self.fail:
            raise self.fail
        self.req = kw
        out = {"embedding": [0.0] * self.dim, "inputTextTokenCount": 12}
        return {"body": io.BytesIO(json.dumps(out).encode())}


def test_titan_request_body():
    fake = FakeBedrock()
    e = TitanEmbedder(client=fake).embed("能耗怎么算")
    body = json.loads(fake.req["body"])
    assert body == {"inputText": "能耗怎么算", "dimensions": 1024, "normalize": True}
    assert fake.req["modelId"] == "amazon.titan-embed-text-v2:0"
    assert len(e.vector) == 1024 and e.tokens == 12


def test_titan_wrong_dim_is_caught_before_db():
    with pytest.raises(ValueError, match="维度"):
        TitanEmbedder(client=FakeBedrock(dim=512)).embed("x")


def test_titan_credential_error_points_to_aws_login():
    class NoCredentialsError(Exception):
        pass

    with pytest.raises(RuntimeError, match="aws login --profile chemq"):
        TitanEmbedder(client=FakeBedrock(fail=NoCredentialsError("Unable to locate credentials"))).embed("x")
