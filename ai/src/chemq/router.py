"""W1 交付物：意图路由（NL -> QueryPlan）。

验收标准（见 evals/golden.jsonl 的 intent 字段）：
  · 意图分类准确率 ≥ 90%
  · 单次调用成本 < $0.002，P95 延迟 < 1.5s（用 haiku）
  · 缓存命中时 cache_read_tokens > 0（证明 prompt caching 生效）

TODO(W1):
  1. 读 kb/metric_dictionary.yaml，渲染成 system 前缀
  2. 用 Llm.parse(schema=QueryPlan) 拿结构化结果
  3. 写 5 条 few-shot（含 1 条该拒答的），放在 system 里而不是 user 里 —— 才能进缓存
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .config import settings
from .llm import Llm, LlmResult
from .schemas import QueryPlan

ROOT = Path(__file__).resolve().parents[3]
KB_DIR = ROOT / "kb"

SYSTEM_ROLE = """你是示例化工经营分析系统的问题解析器。
你的唯一职责是把用户的中文问题解析成结构化查询计划，不要回答问题本身、不要写 SQL。

数据覆盖范围：{DATA_RANGE}。超出这个区间的时间段一律 out_of_scope —— 
模型不知道"库里没有"就会硬算，这是企业问数最常见的一类错答。

判定规则：
- metric_query：能用指标字典里的指标 + 时间范围回答
- knowledge_lookup：问的是制度、口径定义、规程（"综合能耗怎么算"）
- mixed：既要数也要口径解释
- out_of_scope：指标字典里没有的指标（成本、利润、人工），或非经营分析问题
未指明时间时，默认取最近一个完整自然月，并在 reasoning 中说明。"""

FEW_SHOT_EXAMPLES = """
# 路由解析示例
[示例 1]
用户提问：1月份乙烯装置的综合能耗是多少？
解析结果：{"intent": "metric_query", "metrics": ["综合能耗"], "reasoning": "询问特定指标的具体数值，时间明确。"}

[示例 2]
用户提问：综合能耗的计算口径是什么？包含分摊的电费吗？
解析结果：{"intent": "knowledge_lookup", "metrics": ["综合能耗"], "reasoning": "不涉及数值查询，仅询问指标的业务定义和计算规则。"}

[示例 3]
用户提问：查一下上个月的一级品率，顺便告诉我一级品是怎么界定的。
解析结果：{"intent": "mixed", "metrics": ["一级品率"], "reasoning": "既要求查询数值，又要求解释指标口径。"}

[示例 4] (拒答场景：超纲/无数据)
用户提问：预测一下下个月的人工成本。
解析结果：{"intent": "out_of_scope", "metrics": ["人工成本"], "reasoning": "人工成本不在当前指标字典中，且系统不支持预测类问题。"}

[示例 5] (默认时间推断)
用户提问：看一下原油加工量。
解析结果：{"intent": "metric_query", "metrics": ["原油加工量"], "reasoning": "未提供时间，默认取最近一个完整自然月。"}
"""

def data_range() -> str:
    """从 naming.yaml 读数据覆盖区间，不要在 prompt 里写死 —— 换数据就会忘记改。"""
    cfg = yaml.safe_load((ROOT / "naming.yaml").read_text(encoding="utf-8"))
    d = cfg["scenarios"]["date_range"]
    return f"{d['start']} 至 {d['end']}"


def load_metric_dictionary() -> str:
    raw = (KB_DIR / "metric_dictionary.yaml").read_text(encoding="utf-8")
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
