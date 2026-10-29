from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import re

import duckdb

from chemq.config import ROOT, settings
from chemq.domain import domain

IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")

READ_CONFIG = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "memory_limit": "1GB",
    "threads": 2,
    "lock_configuration": True,
}


def build(
    src_path: Path,
    tmp_path: Path,
    objects: dict[str, str | None],
    as_of: date,
) -> None:
    """只读源库，将白名单对象复制到新的临时库。"""
    if not src_path.is_file():
        raise FileNotFoundError(src_path)

    if tmp_path.exists():
        raise FileExistsError(f"临时库必须是新文件：{tmp_path}")

    if not objects:
        raise ValueError("白名单不能为空")

    # 表名和列名不能通过 SQL 参数绑定，先严格校验。
    for name, column in objects.items():
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            raise ValueError(f"非法对象名：{name!r}")

        if column is not None and (
            not isinstance(column, str) or not IDENTIFIER.fullmatch(column)
        ):
            raise ValueError(f"非法日期列名：{column!r}")

        # dbt-duckdb 建视图时，引用的库名就是源文件名去掉 .duckdb（dbt_dev.duckdb → dbt_dev）。
    # 挂载时用同一个名字，视图里的 dbt_dev.main.xxx 才找得到。
    alias = src_path.stem

    # 第 1 处：alias 会拼进 ATTACH 和表名里，SQL 参数绑定管不到它，和表名一样先校验
    if not IDENTIFIER.fullmatch(alias):
        raise ValueError(
            f"源库文件名 {src_path.name!r} 不能当库名用（只允许小写字母、数字、下划线）。"
            f"dbt 的视图是按这个名字引用表的"
        )

    # 第 2 处：临时库自己的库名也是它的文件名（serving.duckdb → serving）。
    # 源库要是也叫这个名字，ATTACH 会因为重名失败，报错还看不出原因
    if alias == tmp_path.stem:
        raise ValueError(
            f"源库和临时库同名（都叫 {alias!r}），挂载时会冲突。把源文件改个名字"
        )

    con = duckdb.connect(str(tmp_path))
    try:
        # 路径作为 SQL 字符串字面量，需要转义单引号。
        source = str(src_path.resolve()).replace("'", "''")
        con.execute(f"ATTACH '{source}' AS {alias} (READ_ONLY)")

        # 在同一个事务中读取源数据并完成复制。
        con.execute("BEGIN TRANSACTION")

        for name, column in objects.items():
            source_table = f'{alias}.main."{name}"'
            where = ""
            params = []

            if column is not None:
                types = {
                    row[0]: row[1]
                    for row in con.execute(f"DESCRIBE {source_table}").fetchall()
                }

                if types.get(column) != "DATE":
                    raise ValueError(f"{name}.{column} 必须是 DATE 列")

                where = f' WHERE "{column}" IS NULL OR "{column}" <= ?'
                params = [as_of]

            # CREATE TABLE AS SELECT：
            # 无论源对象是表还是视图，目标对象都是物理表。
            con.execute(
                f'CREATE TABLE main."{name}" AS '
                f"SELECT * FROM {source_table}{where}",
                params,
            )

            source_rows = con.execute(
                f"SELECT count(*) FROM {source_table}{where}",
                params,
            ).fetchone()[0]

            copied_rows = con.execute(f'SELECT count(*) FROM main."{name}"').fetchone()[
                0
            ]

            if copied_rows != source_rows:
                raise RuntimeError(f"{name} 复制前后行数不一致")

        con.execute("COMMIT")

        # 将 WAL 中的数据写入数据库主文件，再关闭连接。
        con.execute("CHECKPOINT")
    finally:
        con.close()


def verify(
    con,
    objects: dict[str, str | None],
    snapshot: dict,
) -> None:
    """检查对象集合、独立可读性，以及固定的 NZ 价格快照。"""
    actual = set(con.execute("""
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_catalog = current_database()
        """).fetchall())

    expected = {("main", name, "BASE TABLE") for name in objects}

    # 多复制了表、漏了表，或留下了视图，都不允许发布。
    if actual != expected:
        raise RuntimeError(f"服务库对象不符：期望 {expected}，实际 {actual}")

    # 验证每个对象都能在独立的受限连接中读取。
    for name in objects:
        if not IDENTIFIER.fullmatch(name):
            raise ValueError(f"非法对象名：{name!r}")

        con.execute(f'SELECT count(*) FROM main."{name}"').fetchone()

    if "mart_current_price" not in objects:
        raise ValueError("NZ 快照校验需要 mart_current_price")

    # 计数、求和、四舍五入全部交给 SQL。
    rows, total = con.execute("""
        SELECT
            count(*),
            sum(CAST(price_nzd_per_mwh AS DECIMAL(18, 2)))
        FROM main.mart_current_price
        """).fetchone()

    expected_rows = int(snapshot["rows"])
    expected_total = Decimal(str(snapshot["price_sum"]))

    if rows != expected_rows or total != expected_total:
        raise RuntimeError(
            f"快照不符：rows={rows}，期望 {expected_rows}；"
            f"price_sum={total}，期望 {expected_total}"
        )


def main() -> None:
    d = domain()

    if d.id != "nz_power" or d.schema != "main":
        raise ValueError("此脚本用于 nz_power 领域包的 main schema")

    raw_source = settings().duckdb_source.strip()
    if not raw_source:
        raise ValueError("请配置 CHEMQ_DUCKDB_SOURCE 为源数据库文件路径")

    src_path = Path(raw_source).expanduser()

    # 本脚本明确规定：相对源路径以仓库根目录为基准。
    if not src_path.is_absolute():
        src_path = ROOT / src_path

    src_path = src_path.resolve()
    if not src_path.is_file():
        raise FileNotFoundError(src_path)

    target = (d.root / "data" / "serving.duckdb").resolve()

    if src_path == target:
        raise ValueError("源数据库不能是目标服务库")

    # 不覆盖可能仍有未合并写入的目标库。
    if Path(str(target) + ".wal").exists():
        raise RuntimeError("目标库存在 WAL，请先停止写入并完成恢复/检查点")

    names = [
        *d.catalog["views"],
        *d.catalog["dims"],
    ]
    objects = d.config["serving"]["date_columns"]

    # 复制规则必须与现有白名单完全一致。
    if (
        not isinstance(objects, dict)
        or not names
        or len(names) != len(set(names))
        or set(objects) != set(names)
    ):
        raise ValueError("date_columns 必须与 catalog 白名单逐项对应，" "且不能重复")

    as_of = date.fromisoformat(d.as_of)
    snapshot = d.config["eval"]["snapshot"]

    target.parent.mkdir(parents=True, exist_ok=True)

    # 放在目标目录内，确保最终替换发生在同一文件系统。
    with TemporaryDirectory(
        prefix=".serving-build-",
        dir=target.parent,
    ) as temp:
        tmp_path = Path(temp) / "serving.duckdb"

        build(src_path, tmp_path, objects, as_of)

        # 构建连接已经关闭，模拟在线引擎重新打开服务库。
        con = duckdb.connect(
            str(tmp_path),
            read_only=True,
            config=READ_CONFIG,
        )
        try:
            verify(con, objects, snapshot)
        finally:
            con.close()

        # 只有 build 和 verify 全部成功，才会执行到这里。
        os.replace(tmp_path, target)

    print(f"服务库构建成功：{target}")


if __name__ == "__main__":
    main()
