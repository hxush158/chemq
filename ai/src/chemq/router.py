"""W1 交付物：意图路由（NL -> QueryPlan）。

验收标准（见 domains/<领域>/eval/golden.jsonl 的 intent 字段）：
  · 意图分类准确率 ≥ 90%
  · 单次调用成本 < $0.002，P95 延迟 < 1.5s（用 haiku）
  · 缓存命中时 cache_read_tokens > 0（证明 prompt caching 生效）

TODO(W1):
  1. 读领域包的 metric_dictionary.yaml，渲染成 system 前缀
  2. 用 Llm.parse(schema=QueryPlan) 拿结构化结果
  3. 写 5 条 few-shot（含 1 条该拒答的），放在 system 里而不是 user 里 —— 才能进缓存
"""
from __future__ import annotations

import importlib

from .config import settings
from .domain import domain
from .llm import Llm, LlmResult
from .prompts import render, string
from .schemas import QueryPlan

yaml = importlib.import_module("yaml")

# 提示词正文在 prompts/<locale>/router_role.md（通用规则），领域相关的词在 domain.yaml 的 prompt_slots。
# {DATA_RANGE} 是单花括号：不是领域空位，是 plan() 每次调用时才填的数据覆盖区间。
SYSTEM_ROLE = render("router_role")


def few_shot_examples() -> str:
    """把领域包的 router_examples.yaml 渲染成 few-shot 文本。编号按顺序自动生成。

    格式（标签按语言取 prompts/<locale>/strings.yaml）：
        [示例 N] (说明)
        用户提问：…
        解析结果：…
    """
    examples = yaml.safe_load(domain().router_examples_path.read_text(encoding="utf-8")) or []
    label, q, a = string("router_example_label"), string("router_example_question"), string("router_example_plan")
    blocks = []
    for i, ex in enumerate(examples, 1):
        head = f"[{label} {i}]" + (f" {ex['note']}" if ex.get("note") else "")
        blocks.append(f"{head}\n{q}{ex['question']}\n{a}{ex['plan']}\n")
    return "\n" + string("router_examples_title") + "\n" + "\n".join(blocks)


FEW_SHOT_EXAMPLES = few_shot_examples()


def data_range() -> str:
    """从造数配置读数据覆盖区间，不要在 prompt 里写死 —— 换数据就会忘记改。

    ⚠ W1 第 1 步只搬了家，这里仍是化工专用（读 data/seed.yaml）。第 2 步改成从库里查覆盖区间。
    """
    cfg = yaml.safe_load((domain().data_dir / "seed.yaml").read_text(encoding="utf-8"))
    d = cfg["scenarios"]["date_range"]
    return f"{d['start']} 至 {d['end']}"


def load_metric_dictionary() -> str:
    raw = domain().metric_dictionary_path.read_text(encoding="utf-8")
    yaml.safe_load(raw)  # 启动即校验，坏字典不要等到线上才炸
    return raw


def plan(question: str, llm: Llm | None = None) -> LlmResult:
    llm = llm or Llm()
    system = Llm.cached_system(
        SYSTEM_ROLE.replace("{DATA_RANGE}", data_range()),
        FEW_SHOT_EXAMPLES,  # <-- 将 Few-shot 硬编码在 System 块中
        "# 指标口径字典\n" + load_metric_dictionary(), # 最长、最稳定的字典放最后
    )
    return llm.parse(
        model=settings().model_router,
        system=system,
        user=question,
        schema=QueryPlan,
    )
