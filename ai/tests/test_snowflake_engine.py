from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chemq import db


@pytest.fixture
def sf_config(tmp_path, monkeypatch):
    key_path = tmp_path / "chemq_reporter.p8"
    key_path.touch()

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
