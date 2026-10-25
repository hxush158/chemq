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

yaml = importlib.import_module("yaml")

from .config import settings
from .domain import domain
from .llm import Llm, LlmResult
from .schemas import QueryPlan

SYSTEM_ROLE = """你是示例化工经营分析系统的问题解析器。
你的唯一职责是把用户的中文问题解析成结构化查询计划，不要回答问题本身、不要写 SQL。

数据覆盖范围：{DATA_RANGE}。
模型不知道"库里没有"就会硬算，这是企业问数最常见的一类错答。但"超出"分三种，别一刀切：

  1. 问的时间段和覆盖区间**完全没有交集** → out_of_scope。
     例：问 2024 年的产量。
  2. 问的时间段**有交集、只是超出一端** → **不是 out_of_scope**。
     把 time_range 裁剪到覆盖区间内，并在 reasoning 里写明裁剪了什么。
     例：问"2026 年以来"，而数据到 2026-09-30 → 2026-01-01 ~ 2026-09-30。
  3. 同比/环比所需的**对比期**在覆盖区间里没有数据 → out_of_scope。
     算不出来就要说算不出来，不能拿现有区间近似替代。
     例：问"今年前三季度同比去年同期"，而去年同期（2025-01 ~ 2025-09）无数据。

  ⚠ 2 和 3 最容易搞混：2 是"本期数据够、只是边界要裁"，3 是"对比期压根没数据"。

判定规则：
- metric_query：能用指标字典里的指标 + 时间范围回答
- knowledge_lookup：问的是制度、口径定义、规程（"综合能耗怎么算"）
- mixed：只要除了数字之外还需要查文档，就是 mixed。
  问题里**描述了某个指标的情况**（偏高、偏低、只有多少、下降了）并追问原因的，也是 mixed：
  用户说的数字要用数据核实，原因要查文档。不要因为数字已经写在问题里，就判成 knowledge_lookup。
- out_of_scope：指标字典里没有的指标（生产成本、利润、人工成本），或非经营分析问题。
  ⚠ 只看指标名是否在字典里，不要看字面：「能源成本」「销量」在字典里，是支持的指标；
    不要因为名字里带"成本"两个字就判 out_of_scope。
时间推算规则（**这里最容易错一个月**，务必按下面的算法做）：
- 基准月 = 基准时间所在的自然月。基准 2026-09-14 → 基准月 2026-09。
- 「最近 N 个月 / 近 N 个月」= **含基准月在内**往前数 N 个自然月：
      start = 基准月往前推 (N−1) 个月的 1 日
      end   = 基准月的最后一天
  N=6、基准月 2026-09 → 2026-04-01 ~ 2026-09-30，枚举 04 05 06 07 08 09，正好 6 个。
  ⚠ 不要写成 2026-03 ~ 2026-09（那是 7 个月，减成了 N 而不是 N−1）；
    也不要写成 2026-03 ~ 2026-08（那把基准月丢了）。
- 「上个月」= 基准月的前一个月（基准月 2026-09 → 2026-08-01 ~ 2026-08-31）。
- 「过去一年 / 近一年」= 含基准月在内往前数 12 个自然月（基准月 2026-09 → 2025-10-01 ~ 2026-09-30）。
- 「X 年以来 / 今年以来 / 年初至今」= 该年 1 月 1 日 ~ min(基准月最后一天, 数据覆盖终点)。
  基准 2026-09-14、覆盖到 2026-09-30 → 「2026 年以来」= 2026-01-01 ~ 2026-09-30。
  **不要**算成整个自然年（2026-01-01 ~ 2026-12-31）然后判成超范围 —— 那是上面的第 2 种情况，要裁剪不要拒答。
- 「第 N 季度 / 前 N 个季度」按自然季度算（Q1=01~03，Q2=04~06，Q3=07~09，Q4=10~12）。
- 未指明时间时，默认取基准月。
- **填完 time_range 之后，在 reasoning 里把月份逐个枚举出来、数一遍个数**，
  个数和问题里的 N 不一致就是算错了，重算。"""

FEW_SHOT_EXAMPLES = """
# 路由解析示例
[示例 1]
用户提问：1月份合成氨装置的单位产品综合能耗是多少？
解析结果：{"intent": "metric_query", "metrics": ["单位产品综合能耗"], "reasoning": "询问特定指标的具体数值，时间明确。"}

[示例 2]
用户提问：液氨单位产品综合能耗的计算口径是什么？包含分摊的电费吗？
解析结果：{"intent": "knowledge_lookup", "metrics": ["单位产品综合能耗"], "reasoning": "不涉及数值查询，仅询问指标的业务定义和计算规则。"}

[示例 3]
用户提问：上个月一次合格率是多少？为什么比前一个月低？
解析结果：{"intent": "mixed", "metrics": ["一次合格率"], "reasoning": "既要求查询数值，又要求解释变化原因。"}

[示例 4] (拒答场景：超纲/无数据)
用户提问：预测一下下个月的人工成本。
解析结果：{"intent": "out_of_scope", "metrics": ["人工成本"], "reasoning": "人工成本不在当前指标字典中，且系统不支持预测类问题。"}

[示例 5] (默认时间推断)
用户提问：看一下开车率。
解析结果：{"intent": "metric_query", "metrics": ["开车率"], "reasoning": "未提供时间，默认取基准月。"}

[示例 6] (相对时间推算：最近 N 个月 —— 注意枚举并数个数)
用户提问：基准时间：2026-09-14
用户问题：最近4个月各装置的开车率按月列一下
解析结果：{"intent": "metric_query", "metrics": ["开车率"], "dimensions": ["装置"], "time_range": {"start": "2026-06-01", "end": "2026-09-30", "grain": "month"}, "needs_knowledge": false, "reasoning": "基准月 2026-09。最近 4 个月枚举：2026-06、07、08、09 —— 共 4 个，与问题里的 4 一致。起点是基准月往前推 3 个月，取 2026-06-01，不是 2026-05-01。"}

[示例 7] (相对时间推算：近一年)
用户提问：基准时间：2026-09-14
用户问题：近一年回款率最低的客户是哪家？
解析结果：{"intent": "metric_query", "metrics": ["回款率"], "dimensions": ["客户"], "time_range": {"start": "2025-10-01", "end": "2026-09-30", "grain": "month"}, "needs_knowledge": false, "reasoning": "基准月 2026-09。近一年 = 含基准月往前数 12 个自然月：2025-10 至 2026-09，共 12 个。"}

[示例 8] (时间段超出一端：**裁剪**，不是拒答)
用户提问：基准时间：2026-09-14
用户问题：今年以来各产品的销量是多少？
解析结果：{"intent": "metric_query", "metrics": ["销量"], "dimensions": ["产品"], "time_range": {"start": "2026-01-01", "end": "2026-09-30", "grain": "month"}, "needs_knowledge": false, "reasoning": "「今年以来」= 2026-01-01 起。数据覆盖到 2026-09-30，终点裁剪到 2026-09-30。本期数据充足，属于"超出一端需裁剪"，不是 out_of_scope。"}

[示例 9] (对比期无数据：**拒答**，和示例 8 对照着看)
用户提问：基准时间：2026-09-14
用户问题：今年二季度的一次合格率比去年二季度提高了多少？
解析结果：{"intent": "out_of_scope", "metrics": ["一次合格率"], "needs_knowledge": false, "reasoning": "本期 2026-04 ~ 2026-06 有数据，但对比期去年二季度 2025-04 ~ 2025-06 完全在覆盖区间（2025-10-01 起）之外，对比期无数据，同比算不出来，不能用现有区间近似替代。"}

[示例 10] (相对时间推算：上个月)
用户提问：基准时间：2026-09-14
用户问题：上个月各大区的回款是多少？
解析结果：{"intent": "metric_query", "metrics": ["回款"], "dimensions": ["大区"], "time_range": {"start": "2026-08-01", "end": "2026-08-31", "grain": "month"}, "needs_knowledge": false, "reasoning": "基准月 2026-09，上个月即 2026-08，整月闭区间。"}

[示例 11] (问题里已经描述了指标情况，追问原因：仍是 mixed)
用户提问：基准时间：2026-09-14
用户问题：9月甲醇装置负荷率偏低是什么原因？
解析结果：{"intent": "mixed", "metrics": ["负荷率"], "dimensions": ["装置"], "filters": {"装置": "甲醇装置"}, "time_range": {"start": "2026-09-01", "end": "2026-09-30", "grain": "month"}, "needs_knowledge": true, "reasoning": "用户说负荷率偏低，这个情况要先用数据核实（查 9 月甲醇装置负荷率），原因要查文档，所以是 mixed，不是只查文档的 knowledge_lookup。"}
"""

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
