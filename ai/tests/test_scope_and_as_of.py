"""W1 第 3 步：数据权限改名 scope、基准时间从领域包读。不连数据库、不调 API。"""
from types import SimpleNamespace as NS

import pytest

from chemq import db, pipeline
from chemq.domain import domain


def test_chem_domain_has_scope_var_and_as_of():
    d = domain()
    assert d.scope_session_var == "chemq.region"     # 化工 RLS 策略读的会话变量，数据库没改
    assert d.as_of == "2026-09-14"


def test_no_scope_means_no_session_var():
    assert db.scope_session_var(None) is None
    assert db.scope_session_var("") is None


def test_scope_without_domain_config_fails_closed(monkeypatch):
    """领域包没配 scope 却传了 scope：必须报错，不能当没传（那等于看到全部数据）。"""
    monkeypatch.setattr(db, "domain", lambda: NS(id="x", scope_session_var=None))
    with pytest.raises(ValueError, match="scope"):
        db.scope_session_var("华南")


def test_postgres_engine_checks_scope_before_connecting(monkeypatch):
    monkeypatch.setattr(db, "domain", lambda: NS(id="x", scope_session_var=None, schema="chem"))
    with pytest.raises(ValueError, match="scope"):
        db.PostgresEngine().run_query("SELECT 1", scope="华南")


def test_missing_as_of_is_an_error(monkeypatch):
    from chemq.domain import Domain

    d = Domain(id="x", root=domain().root, config={})
    with pytest.raises(KeyError, match="eval.as_of"):
        _ = d.as_of


def test_pipeline_uses_domain_as_of_when_not_given(monkeypatch):
    seen = {}

    def fake_plan(text):
        seen["text"] = text
        return NS(parsed=NS(intent="chitchat", reasoning="闲聊"), usage=NS(cost_usd=0.0))

    monkeypatch.setattr(pipeline, "plan", fake_plan)
    pipeline.answer("你好")
    assert seen["text"].startswith(f"基准时间：{domain().as_of}\n")
