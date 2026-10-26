"""确定性的时间覆盖检查：配置错误报错，查询越界则裁剪或拒答。"""

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


def check_time(plan: QueryPlan, coverage: tuple[date, date]) -> TimeVerdict:
    """普通查询可取交集；两期对比要求两期完整覆盖。始终不修改原计划。"""
    coverage_start, coverage_end = coverage
    if coverage_start > coverage_end:
        raise ValueError("数据覆盖的开始日期不能晚于结束日期")

    # 这里只检查需要取数的计划，不替其他意图决定是否拒答。
    if plan.intent not in ("metric_query", "mixed"):
        return TimeVerdict("ok", plan)
    if plan.time_range is None:
        return TimeVerdict("refuse", plan, "查询计划缺少本期时间范围")

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
