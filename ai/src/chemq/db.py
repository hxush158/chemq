"""数据库访问。两个连接池：写(审计) / 只读(取数)。

只读池刻意用独立的数据库账号登录，映射到 chemq_ro role：
即使 sqlguard 被绕过，数据库层面也执行不了写操作。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import settings


@contextmanager
def readonly_cursor(region: str | None = None):
    """取数用。每次连接都设 statement_timeout 与行级权限上下文。"""
    s = settings()
    with psycopg.connect(s.pg_dsn_readonly, row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(s.statement_timeout_ms)}")
            cur.execute("SET search_path = chem, public")
            if region:
                cur.execute("SELECT set_config('chemq.region', %s, false)", (region,))
            yield cur


def run_query(sql: str, region: str | None = None) -> list[dict[str, Any]]:
    with readonly_cursor(region) as cur:
        cur.execute(sql)
        return cur.fetchall()


@contextmanager
def app_cursor():
    """写审计表用。"""
    with psycopg.connect(settings().pg_dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SET search_path = chem, public")
            yield cur
        conn.commit()
