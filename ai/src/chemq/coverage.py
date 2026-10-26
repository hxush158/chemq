"""确定性的时间覆盖检查：配置错误报错，查询越界则裁剪或拒答。"""

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from typing import Literal

import yaml

from .domain import domain
from .schemas import QueryPlan


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
        return TimeVerdict("refuse", plan, "查询计划缺少本期时间范围")

    updates = {}

    for field, label, time_range in (
        ("time_range", "本期", plan.time_range),
        ("compare_range", "对比期", plan.compare_range),
    ):
        if time_range is None:
            continue

        try:
            # 开始日期严格解析，不做修正。
            date.fromisoformat(time_range.start)
            normalized_end = normalize_date_string(time_range.end)
        except ValueError:
            return TimeVerdict("refuse", plan, f"{label}包含无效日期")

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
        return TimeVerdict("refuse", plan, "本期包含无效日期")
    if start > end:
        return TimeVerdict("refuse", plan, "本期的开始日期晚于结束日期")

    span = f"{coverage_start.isoformat()} 至 {coverage_end.isoformat()}"
    # 闭区间：恰好碰到边界的那一天仍然有交集。
    if end < coverage_start or start > coverage_end:
        return TimeVerdict("refuse", plan, f"本期与数据覆盖范围 {span} 没有交集")

    if plan.compare_range is not None:
        try:
            compare_start = date.fromisoformat(plan.compare_range.start)
            compare_end = date.fromisoformat(plan.compare_range.end)
        except ValueError:
            return TimeVerdict("refuse", plan, "对比期包含无效日期")
        if compare_start > compare_end:
            return TimeVerdict("refuse", plan, "对比期的开始日期晚于结束日期")
        if compare_start < coverage_start or compare_end > coverage_end:
            return TimeVerdict("refuse", plan, f"对比期未被数据覆盖范围 {span} 完整覆盖")
        if start < coverage_start or end > coverage_end:
            return TimeVerdict("refuse", plan, f"本期未被数据覆盖范围 {span} 完整覆盖，不能进行两期对比")
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
    reason = (
        f"本期从 {start.isoformat()} 至 {end.isoformat()} 裁剪为 "
        f"{clipped_range.start} 至 {clipped_range.end}，仅查询数据覆盖内的部分"
    )
    return TimeVerdict("clip", clipped_plan, reason)
