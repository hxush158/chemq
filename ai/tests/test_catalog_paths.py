from dataclasses import replace

import pytest

from chemq import text2sql
from chemq.domain import load_domain


@pytest.mark.parametrize(
    "domain_id, dialect",
    [
        ("nz_power", "duckdb"),
        ("chem", "postgres"),
    ],
)
def test_primary_dialect_reads_root_catalog(monkeypatch, domain_id, dialect):
    d = load_domain(domain_id)

    # 明确测试前提：根目录文件存在，方言专属文件不存在。
    assert d.catalog_path.is_file()
    assert not d.catalog_path_for(dialect).exists()

    monkeypatch.setattr(text2sql, "domain", lambda: d)

    expected = d.catalog_path.read_text(encoding="utf-8")
    assert text2sql.schema_context(dialect) == expected


def test_doris_does_not_fall_back_to_postgres(monkeypatch, tmp_path):
    # 使用真实 chem 配置，但把文件位置换成临时目录。
    d = replace(load_domain("chem"), root=tmp_path)

    # 故意提供根目录文件，验证 Doris 不能借用它。
    d.catalog_path.write_text("Postgres catalog", encoding="utf-8")
    monkeypatch.setattr(text2sql, "domain", lambda: d)

    with pytest.raises(NotImplementedError, match="缺少 doris 的语义层目录"):
        text2sql.schema_context("doris")