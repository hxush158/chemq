"""数据库访问 —— 一个 Engine 抽象，两个实现。

为什么要抽这一层：W5 要接 Doris，而 Doris 和 Postgres 有两处**语义**不同，
不是换个驱动就完事的：

  1. 协议不同。Doris 说 MySQL 协议（FE 查询口 9030），psycopg 连不上。
  2. 行级权限（scope，数据权限范围；化工包里是"大区"）的机制不同。
     Postgres：一个只读账号 + 每次请求 set_config(<会话变量>, scope)
               会话变量名来自领域包 domain.yaml 的 scope.session_var（化工是 chemq.region）
     Doris   ：谓词是静态的、绑在 role 上（CREATE ROW POLICY ... TO ROLE x USING (...)），
               读不了会话变量 —— 所以 scope 得靠「选哪个连接」来表达。

第 2 条才是这个抽象存在的理由。调用方 text2sql.generate_and_run(plan, scope=...)
的签名两边完全一样，差异全吃在 Engine 实现里。
业务代码里不该出现 if dialect == ...。
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from threading import Timer
from typing import Any, Iterator, Protocol

from .config import settings
from .domain import domain


class Engine(Protocol):
    """取数引擎。W5 加 Doris 实现时，只要满足这个协议，上层一行都不用改。"""

    dialect: str

    def readonly_cursor(self, scope: str | None = None) -> Any: ...
    def run_query(
        self, sql: str, scope: str | None = None, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...
    def app_cursor(self) -> Any: ...


def scope_session_var(scope: str | None) -> str | None:
    """scope 要写进哪个会话变量。scope 为空 → None；给了 scope 但领域包没配 → 报错。"""
    if not scope:
        return None
    var = domain().scope_session_var
    if not var:
        raise ValueError(f"领域包 {domain().id} 没有配置 scope.session_var，不能按 scope={scope!r} 过滤")
    return var


class PostgresEngine:
    """W1-W4 的学习环境。行级权限走会话变量 + RLS 策略。"""

    dialect = "postgres"

    @contextmanager
    def readonly_cursor(self, scope: str | None = None) -> Iterator[Any]:
        """取数用。每次连接都设 statement_timeout 与行级权限上下文。"""
        # 会话变量名由领域包决定（化工：chemq.region，RLS 策略读它）。
        # 领域包没配却传了 scope → 连库之前就报错：不能悄悄忽略权限范围（fail-closed）。
        var = scope_session_var(scope)

        import psycopg  # noqa: PLC0415
        from psycopg.rows import dict_row  # noqa: PLC0415

        s = settings()
        with psycopg.connect(s.pg_dsn_readonly, row_factory=dict_row) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {int(s.statement_timeout_ms)}")
                cur.execute(f"SET search_path = {domain().schema}, public")   # schema 已校验过是标识符
                if var:
                    cur.execute("SELECT set_config(%s, %s, false)", (var, scope))
                yield cur

    def run_query(
        self, sql: str, scope: str | None = None, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        with self.readonly_cursor(scope) as cur:
            # params 为 None 时 psycopg 不解析 % 占位符 —— 模型生成的 SQL 里可能有
            # LIKE '%甲醇%' 或 Doris 的 date_format('%Y-%m')，千万别给它传 {}。
            cur.execute(sql, params)
            return cur.fetchall()

    @contextmanager
    def app_cursor(self) -> Iterator[Any]:
        """写审计表用。"""
        import psycopg  # noqa: PLC0415
        from psycopg.rows import dict_row  # noqa: PLC0415

        with psycopg.connect(settings().pg_dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET search_path = {domain().schema}, public")   # schema 已校验过是标识符
                yield cur
            conn.commit()


class DorisEngine:
    """W5 交付物。现在只是个占位，故意抛错而不是静默降级。

    TODO(W5) 实现时要解决的四件事，按难度排：

      1. 驱动：pymysql / mysqlclient，cursorclass=DictCursor。
         DSN 在 settings().doris_dsn_readonly。

      2. 超时：Doris 是 `SET query_timeout = <秒>`，不是 statement_timeout 的毫秒。
         settings().statement_timeout_ms 要除以 1000 —— 别直接传，会变成 8000 秒。

      3. search_path 没有对应物。Doris 没有 schema 这一层，是 <database>.<table>。
         但只要把 Doris 侧的 database 也起名叫 `chem`，
         领域包的白名单（domain().allowed_relations）一个字都不用改 ——
         sqlguard._qualified() 取的是 table.text("db")，两边输出一致。

      4. scope：**这里是真正的设计改动，不是翻译。**
         Doris 的 row policy 谓词是静态的、绑在 user/role 上，读不了会话变量。
         所以不能「一个账号 + 每次 SET 会话变量」，得改成一个 scope（大区）一个 role + 一个连接：
             CREATE ROW POLICY rls_huanan ON fact_sales
               AS RESTRICTIVE TO ROLE ro_huanan USING (region = '华南');
         这个方法里要按 scope 挑 DSN，而不是连上之后再设变量。
         反过来有个好消息：Doris 的 policy 是按**查询者**生效的，
         所以 Postgres 那个「视图以 owner 身份执行 → 超管绕过 RLS」的坑不存在。
         （另：Doris 明确不能给 root/admin 建 row policy，别用 root 测。）
    """

    dialect = "doris"

    _TODO = (
        "DorisEngine 还没实现（W5 交付物）。"
        "现在把 CHEMQ_SQL_DIALECT 改回 postgres，"
        "或者见 db.py 里 DorisEngine 的 TODO(W5) 四条。"
    )

    def readonly_cursor(self, scope: str | None = None) -> Any:
        raise NotImplementedError(self._TODO)

    def run_query(
        self, sql: str, scope: str | None = None, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        raise NotImplementedError(self._TODO)

    def app_cursor(self) -> Any:
        raise NotImplementedError(self._TODO)


class DuckDBEngine:
    """只读访问预先构建好的 DuckDB serving 库。"""

    dialect = "duckdb"

    @contextmanager
    def readonly_cursor(self, scope: str | None = None) -> Iterator[Any]:
        # 先复用领域包校验
        scope_session_var(scope)
        # 即使领域包声明了 scope，DuckDB 目前也没有实现权限过滤。
        if scope:
            raise ValueError("DuckDB 暂不支持 scope，不能忽略数据权限范围")
        s = settings()
        if not s.duckdb_path.strip():
            raise ValueError("请配置 CHEMQ_DUCKDB_PATH，指向 DuckDB serving 库")

        path = Path(s.duckdb_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"DuckDB serving 库不存在：{path}")

        import duckdb  # noqa: PLC0415

        conn = duckdb.connect(
            str(path),
            read_only=True,
            config={
                "enable_external_access": False,
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
                "memory_limit": "1GB",
                "threads": 2,
                "lock_configuration": True,
            },
        )
        try:
            # DuckDB 的连接对象本身支持 execute / fetchall。
            yield conn
        finally:
            conn.close()

    def run_query(
        self,
        sql: str,
        scope: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        timeout_ms = settings().statement_timeout_ms
        if timeout_ms <= 0:
            raise ValueError("DuckDB 的 statement_timeout_ms 必须大于 0")

        with self.readonly_cursor(scope) as conn:
            # Timer 的单位是秒；传方法本身，到时间再调用。
            timer = Timer(timeout_ms / 1000.0, conn.interrupt)
            timer.daemon = True
            timer.start()

            try:
                if params is None:
                    conn.execute(sql)
                else:
                    conn.execute(sql, params)

                description = conn.description
                if description is None:
                    raise ValueError("查询没有返回结果集")

                columns = [column[0] for column in description]
                if len(columns) != len(set(columns)):
                    raise ValueError("查询返回了重复列名，请使用唯一的列别名")

                rows = conn.fetchall()
            finally:
                # 无论成功、SQL 错误还是被中断，都清理计时器。
                timer.cancel()
                # cancel 不会停止已经开始的回调；等它结束后才能关闭连接。
                timer.join()

            return [dict(zip(columns, row, strict=True)) for row in rows]

    def app_cursor(self) -> Any:
        raise NotImplementedError(
            "DuckDBEngine 不支持写入审计表，请使用 PostgreSQL 的写入入口"
        )


_ENGINES: dict[str, type] = {
    "postgres": PostgresEngine, "doris": DorisEngine, "duckdb": DuckDBEngine,
}


@lru_cache
def engine() -> Engine:
    """按 settings().sql_dialect 选引擎。lru_cache 是为了别每次查询都新建对象。"""
    dialect = settings().sql_dialect
    # 领域包声明自己能跑在哪些方言上；没声明、或者不在列表里，启动就报错（fail-closed），
    # 不要等查到一半才发现"化工的视图在 DuckDB 里不存在"。
    allowed = domain().config.get("dialects")
    if not allowed or dialect not in allowed:
        raise ValueError(
            f"领域包 {domain().id} 支持的方言是 {allowed}，当前 CHEMQ_SQL_DIALECT={dialect!r}"
        )
    try:
        return _ENGINES[dialect]()
    except KeyError:
        raise ValueError(
            f"未知的 CHEMQ_SQL_DIALECT={dialect!r}，可选：{sorted(_ENGINES)}"
        ) from None


@lru_cache
def kb_engine() -> Engine:
    """知识库固定使用 PostgreSQL（pgvector），不跟随业务取数引擎切换。"""
    return PostgresEngine()


# --- 下面三个是给调用方的门面，签名和 W1 时一模一样，所以 text2sql / pipeline 不用改 ---


@contextmanager
def readonly_cursor(scope: str | None = None) -> Iterator[Any]:
    with engine().readonly_cursor(scope) as cur:
        yield cur


def run_query(
    sql: str, scope: str | None = None, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """params 只给我们自己写的固定 SQL 用（比如 RAG 检索）。模型生成的 SQL 不传参数。"""
    return engine().run_query(sql, scope=scope, params=params)


@contextmanager
def app_cursor() -> Iterator[Any]:
    with engine().app_cursor() as cur:
        yield cur
