"""领域包：domains/<id>/ 下的一套业务内容（指标字典、术语表、语义层目录、评测集、知识库文档）。

核心引擎不写死任何业务名称或文件路径，都从这里拿。选哪个领域由 CHEMQ_DOMAIN 决定（默认 chem）。
设计见 docs/11-domain-pack-design.md。

目录约定（文件名固定，领域之间只有内容不同）：
    domain.yaml              引擎要读的领域配置（含提示词空位 prompt_slots）
    router_examples.yaml     Router 的 few-shot 示例
    metric_dictionary.yaml   指标口径字典
    glossary.txt             术语表（RAG 无命中判断用）
    semantic_catalog.md      语义层目录（生成的；<dialect>/semantic_catalog.md 可按方言覆盖）
    coverage.yaml            数据覆盖区间（生成的）
    eval/                    cases.yaml（题目）→ golden.jsonl / holdout.jsonl（生成的）；kb_golden.jsonl
    kb/docs/                 知识库原文
    data/                    建库、造数脚本（每个领域自己的，引擎不读）
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import ROOT, settings

yaml = importlib.import_module("yaml")

DOMAINS_DIR = ROOT / "domains"


@dataclass(frozen=True)
class Domain:
    id: str
    root: Path
    config: dict[str, Any]

    @property
    def locale(self) -> str:
        return self.config.get("locale", "zh")

    @property
    def display_name(self) -> str:
        return self.config.get("display_name", self.id)

    @property
    def schema(self) -> str:
        s = self.config.get("schema", "")
        # 会拼进 SET search_path / 白名单，只允许普通标识符
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", s):
            raise ValueError(f"domains/{self.id}/domain.yaml 的 schema={s!r} 不是合法的小写标识符")
        return s

    @property
    def catalog(self) -> dict[str, Any]:
        cat = self.config.get("catalog")
        if not cat:
            raise KeyError(f"domains/{self.id}/domain.yaml 里没有 catalog")
        return cat

    @property
    def allowed_relations(self) -> tuple[str, ...]:
        """sqlguard 白名单：语义层目录里列出的视图和维度表。目录和白名单同一个来源，不会对不上。"""
        return tuple(f"{self.schema}.{n}" for n in [*self.catalog["views"], *self.catalog["dims"]])

    @property
    def metric_dictionary_path(self) -> Path:
        return self.root / "metric_dictionary.yaml"

    @property
    def router_examples_path(self) -> Path:
        return self.root / "router_examples.yaml"

    @property
    def glossary_path(self) -> Path:
        return self.root / "glossary.txt"

    @property
    def catalog_path(self) -> Path:
        return self.root / "semantic_catalog.md"

    @property
    def coverage_path(self) -> Path:
        return self.root / "coverage.yaml"

    def catalog_path_for(self, dialect: str) -> Path:
        """按方言覆盖的目录：<root>/<dialect>/semantic_catalog.md（不一定存在）。"""
        return self.root / dialect / "semantic_catalog.md"

    @property
    def eval_dir(self) -> Path:
        return self.root / "eval"

    @property
    def golden_path(self) -> Path:
        return self.eval_dir / "golden.jsonl"

    @property
    def holdout_path(self) -> Path:
        return self.eval_dir / "holdout.jsonl"

    @property
    def kb_golden_path(self) -> Path:
        return self.eval_dir / "kb_golden.jsonl"

    @property
    def kb_docs_dir(self) -> Path:
        return self.root / "kb" / "docs"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"


def available() -> list[str]:
    return sorted(p.parent.name for p in DOMAINS_DIR.glob("*/domain.yaml"))


def load_domain(domain_id: str) -> Domain:
    root = DOMAINS_DIR / domain_id
    cfg_path = root / "domain.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"领域包 {domain_id!r} 不存在：缺少 {cfg_path}。可选：{available()}（CHEMQ_DOMAIN 设错了？）"
        )
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if cfg.get("id") != domain_id:
        # 目录名和 id 不一致时宁可启动就炸：复制一个领域包忘了改 id，是最容易出的错
        raise ValueError(f"{cfg_path} 里的 id={cfg.get('id')!r} 和目录名 {domain_id!r} 不一致")
    return Domain(id=domain_id, root=root, config=cfg)


@lru_cache
def domain() -> Domain:
    """当前领域包。lru_cache：一个进程只用一个领域；测试里要换领域就调 load_domain()。"""
    return load_domain(settings().domain)
