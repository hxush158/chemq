from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from chemq import db


@pytest.fixture(scope="module")
def encrypted_private_key_pem():
    private_key = rsa.generate_private_key(
        public_exponent=65537, 
        key_size=2048
    )
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(
            b"test-passphrase"
        ),
    )
    

@pytest.fixture
def sf_config(tmp_path, monkeypatch, encrypted_private_key_pem):
    key_path = tmp_path / "chemq_reporter.p8"
    key_path.write_bytes(encrypted_private_key_pem)

    config = SimpleNamespace(
        snowflake_account="test-account",
        snowflake_user="test-user",
        snowflake_private_key_path=str(key_path),
        snowflake_private_key_password="test-passphrase",
        snowflake_role="NZ_REPORTER",
        snowflake_warehouse="WH_XS",
        snowflake_database="NZ_POWER",
        statement_timeout_ms=1501,
    )
    monkeypatch.setattr(db, "settings", lambda: config)
    return config


def test_admin_role_rejected_before_connect(sf_config, monkeypatch):
    connect = Mock()
    monkeypatch.setattr("snowflake.connector.connect", connect)
    sf_config.snowflake_role = " sysadmin "

    with pytest.raises(ValueError, match="CHEMQ_SNOWFLAKE_ROLE"):
        with db.SnowflakeEngine().readonly_cursor():
            pytest.fail("高权限角色不应该拿到 cursor")

    connect.assert_not_called()


def test_wrong_current_role_closes_resource(sf_config, monkeypatch):
    connect = Mock()
    conn = connect.return_value
    cur = conn.cursor.return_value
    cur.fetchone.return_value = ("OTHER_ROLE",)
    monkeypatch.setattr("snowflake.connector.connect", connect)
    monkeypatch.setattr(
        db, "domain", lambda: SimpleNamespace(schema_for=lambda dialect: "MART")
    )

    with pytest.raises(PermissionError, match="CHEMQ_SNOWFLAKE_ROLE"):
        with db.SnowflakeEngine().readonly_cursor():
            pytest.fail("角色不匹配时不应该交出 cursor")

    cur.execute.assert_called_once_with("SELECT CURRENT_ROLE()")
    cur.close.assert_called_once_with()
    conn.close.assert_called_once_with()


@pytest.fixture
def sf_connect(sf_config, monkeypatch):
    connect = Mock()
    connect.return_value.cursor.return_value.fetchone.return_value = ("NZ_REPORTER",)
    monkeypatch.setattr("snowflake.connector.connect", connect)
    monkeypatch.setattr(
        db, "domain",
        lambda: SimpleNamespace(schema_for=lambda dialect: "mart", scope_session_var="test.scope"),
    )
    return connect


def test_run_query_returns_rows(sf_connect):
    cur = sf_connect.return_value.cursor.return_value
    cur.description = [("TRADING_PERIOD",), ("PRICE",)]
    cur.fetchall.return_value = [(6, Decimal("45.02"))]
    sql = "SELECT 6 AS trading_period, 45.02 AS price"

    rows = db.SnowflakeEngine().run_query(sql)

    assert rows == [{"trading_period": 6, "price": Decimal("45.02")}]
    assert isinstance(rows[0]["price"], Decimal)
    sf_connect.assert_called_once()
    kwargs = sf_connect.call_args.kwargs
    assert kwargs["authenticator"] == "SNOWFLAKE_JWT"
    assert kwargs["schema"] == "MART"
    assert kwargs["role"] == "NZ_REPORTER"
    assert kwargs["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 2
    assert kwargs["session_parameters"]["QUERY_TAG"] == "chemq"
    assert "password" not in kwargs
    assert cur.execute.call_args_list == [
        call("SELECT CURRENT_ROLE()"),
        call(sql, timeout=2),
    ]
    assert kwargs["enable_server_session_keep_alive_auto_detection"] is False


def test_scope_rejected_before_connect(sf_connect):
    with pytest.raises(ValueError, match="scope"):
        db.SnowflakeEngine().run_query("SELECT 1", scope="test-region")

    sf_connect.assert_not_called()


def test_missing_user_rejected_before_connect(sf_config, sf_connect):
    sf_config.snowflake_user = ""

    with pytest.raises(ValueError, match="CHEMQ_SNOWFLAKE_USER"):
        db.SnowflakeEngine().run_query("SELECT 1")

    sf_connect.assert_not_called()


def test_duplicate_lowercase_columns_rejected(sf_connect):
    cur = sf_connect.return_value.cursor.return_value
    cur.description = [("A",), ("a",)]

    with pytest.raises(ValueError, match="重复列名"):
        db.SnowflakeEngine().run_query('SELECT 1 AS "A", 2 AS "a"')


def test_query_error_closes_resources(sf_connect):
    from snowflake.connector.errors import ProgrammingError

    conn = sf_connect.return_value
    cur = conn.cursor.return_value
    error = ProgrammingError("invalid SQL")
    cur.execute.side_effect = [None, error]

    with pytest.raises(ProgrammingError) as exc:
        db.SnowflakeEngine().run_query("SELECT FROM")

    assert exc.value is error
    cur.close.assert_called_once_with()
    conn.close.assert_called_once_with()

def test_wrong_private_key_password_rejected_before_connect(
        sf_config, sf_connect
):
    sf_config.snowflake_private_key_password = "wrong-passphrase"

    with pytest.raises(
        ValueError, 
        match="CHEMQ_SNOWFLAKE_PRIVATE_KEY_PASSWORD"
    ):
        db.SnowflakeEngine().run_query("SELECT 1")
    sf_connect.assert_not_called()