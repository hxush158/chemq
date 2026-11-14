"""确定性的时间覆盖检查：配置错误报错，查询越界则裁剪或拒答。"""

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Literal

import yaml

from .domain import domain
from .prompts import string
from .schemas import QueryPlan, TimeRange


@dataclass(frozen=True)
class TimeVerdict:
    action: Literal["ok", "clip", "refuse"]
    plan: QueryPlan          # clip 时是裁剪后的
    reason: str | None = None


def load_coverage() -> tuple[date, date]:
    """读取当前领域的数据覆盖闭区间；配置缺失或损坏时明确报错。"""
    path = domain().coverage_path
    if not path.exists():
        raise FileNotFoundError(f"缺少 {path}。请先运行 make catalog")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} 必须包含 start 和 end 字符串")
    try:
        start = date.fromisoformat(raw["start"])
        end = date.fromisoformat(raw["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} 的 start/end 必须是有效的 ISO 日期字符串") from exc
    if start > end:
        raise ValueError(f"{path} 的开始日期不能晚于结束日期")
    return start, end

def normalize_date_string(value: str) -> str:
    """"结束日期的日超过当月天数时，修正为当月最后一天。"""
    try:
        date.fromisoformat(value)
        return value
    except ValueError:
        # 修复 YYYY-MM-DD 中“日过大”的情况
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError(f"无效的结束日期：{value}") from None
        year, month, day = map(int, value.split("-"))
        # 先验证年和月；非法年份、月份不能靠裁剪修复。
        date(year, month, 1)
        last_day = monthrange(year, month)[1]
        # day=0 仍会报错；只有超过月末的日会被缩小。
        return date(year, month, min(day, last_day)).isoformat()
    

def check_time(plan: QueryPlan, coverage: tuple[date, date]) -> TimeVerdict:
    """期规范化，再做覆盖检查：
    结束日期的"日"超过了当月天数（比如 02-29、02-30、04-31），就改成当月最后一天。模型写 02-29，意思显然是"2 月底"。
    开始日期出现这种情况，仍然拒答。开始日期写成 02-30 说不清是什么意思，你现有的测试也是这么要求的，不用改。
    对比期的结束日期也做同样处理。规范化后返回的是新的 plan，原来的 plan 不改动，和裁剪时一样。"""
    
    
    """普通查询可取交集；两期对比要求两期完整覆盖。始终不修改原计划。"""
    coverage_start, coverage_end = coverage
    if coverage_start > coverage_end:
        raise ValueError("数据覆盖的开始日期不能晚于结束日期")

    # 这里只检查需要取数的计划，不替其他意图决定是否拒答。
    if plan.intent not in ("metric_query", "mixed"):
        return TimeVerdict(action="ok", plan=plan)
    if plan.time_range is None:
        # 11/15 Hui 选 A：日期由数据本身决定（比如节点第一次出现那天）、用户又没给时间时，Router 按规则留空。
        # 时间仍由代码管（ad1a5da）：用整个数据覆盖范围。pipeline 会把实际用的日期范围写进口径说明，用户看得到。
        plan = plan.model_copy(update={"time_range": TimeRange(
            start=coverage_start.isoformat(), end=coverage_end.isoformat(), grain="day",
        )})

    updates = {}
    # 给用户看的拒答理由按领域包的语言取（prompts/<locale>/strings.yaml）
    main_label, compare_label = string("time_label_main"), string("time_label_compare")

    for field, label, time_range in (
        ("time_range", main_label, plan.time_range),
        ("compare_range", compare_label, plan.compare_range),
    ):
        if time_range is None:
            continue

        try:
            # 开始日期严格解析，不做修正。
            date.fromisoformat(time_range.start)
            normalized_end = normalize_date_string(time_range.end)
        except ValueError:
            return TimeVerdict("refuse", plan, string("time_invalid_date", extra={"label": label}))

        if normalized_end != time_range.end:
            updates[field] = time_range.model_copy(
                update={"end": normalized_end},
           )

    if updates:
        plan = plan.model_copy(update=updates)

    try:
        start = date.fromisoformat(plan.time_range.start)
        end = date.fromisoformat(plan.time_range.end)
    except ValueError:
        return TimeVerdict("refuse", plan, string("time_invalid_date", extra={"label": main_label}))
    if start > end:
        return TimeVerdict("refuse", plan, string("time_start_after_end", extra={"label": main_label}))

    span = string("data_range", extra={"start": coverage_start.isoformat(), "end": coverage_end.isoformat()})
    # 闭区间：恰好碰到边界的那一天仍然有交集。
    if end < coverage_start or start > coverage_end:
        return TimeVerdict("refuse", plan, string("time_no_overlap", extra={"span": span}))

    if plan.compare_range is not None:
        try:
            compare_start = date.fromisoformat(plan.compare_range.start)
            compare_end = date.fromisoformat(plan.compare_range.end)
        except ValueError:
            return TimeVerdict("refuse", plan, string("time_invalid_date", extra={"label": compare_label}))
        if compare_start > compare_end:
            return TimeVerdict("refuse", plan, string("time_start_after_end", extra={"label": compare_label}))
        if compare_start < coverage_start or compare_end > coverage_end:
            return TimeVerdict("refuse", plan, string("time_compare_not_covered", extra={"span": span}))
        if start < coverage_start or end > coverage_end:
            return TimeVerdict("refuse", plan, string("time_main_partial_with_compare", extra={"span": span}))
        return TimeVerdict("ok", plan)

    clipped_start = max(start, coverage_start)
    clipped_end = min(end, coverage_end)
    if clipped_start == start and clipped_end == end:
        return TimeVerdict("ok", plan)

    # 嵌套的 TimeRange 也复制，避免通过共享对象修改原计划。
    clipped_range = plan.time_range.model_copy(update={
        "start": clipped_start.isoformat(),
        "end": clipped_end.isoformat(),
    })
    clipped_plan = plan.model_copy(update={"time_range": clipped_range})
    reason = string("time_clipped", extra={
        "start": start.isoformat(), "end": end.isoformat(),
        "clip_start": clipped_range.start, "clip_end": clipped_range.end,
    })
    return TimeVerdict("clip", clipped_plan, reason)
