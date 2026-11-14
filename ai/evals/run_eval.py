#!/usr/bin/env python3
"""评测跑分器 —— W1 就要建起来的东西。

学习闭环的核心：你改一次 prompt，跑一次这个，立刻看到分数怎么动。
没有它，你对"好不好"的判断只能靠感觉，而感觉在 LLM 上非常不可靠。

用法:
    python ai/evals/run_eval.py --week 1              # 只跑 W1 纳入的题
    python ai/evals/run_eval.py --week 2 --json out.json
    python ai/evals/run_eval.py --week 1 --dry        # 不调 API，检查评测集本身

指标:
    intent_acc      意图分类准确率        W1 目标 ≥ 0.90
    exec_acc        执行准确率(结果一致)  W2 目标 ≥ 0.80
    refuse_acc      该拒答时拒答          全程目标 = 1.00   ← 这个必须 100%
    citation_acc    引用正确率            W3 目标 ≥ 0.90
    cost_usd/p95_ms 成本与延迟
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ai" / "src"))

from chemq.config import settings  # noqa: E402
from chemq.domain import domain  # noqa: E402  —— 要先改 sys.path 才能 import

GOLDEN = domain().golden_path
HOLDOUT = domain().holdout_path
TOL_PCT = 0.5   # 数值容差 0.5%：浮点和四舍五入的差异不算错

# 每类断言从第几周起「到期」。没到期的不计分、不算失败，只记为 pending。
# 没有这个映射，一个满分的 W1 会显示 7 个 failure —— 而到了 W2 你就分不清
# 「新坏的」和「本来还没做的」。可测性的一部分是：指标要能区分这两件事。
DUE_FROM = {"refuse": 1, "sql": 2, "citation": 3}


@dataclass
class Score:
    total: int = 0
    hits: int = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.hits += bool(ok)

    @property
    def rate(self) -> float:
        return round(self.hits / self.total, 4) if self.total else float("nan")


@dataclass
class Report:
    intent: Score = field(default_factory=Score)
    execution: Score = field(default_factory=Score)
    refusal: Score = field(default_factory=Score)
    citation: Score = field(default_factory=Score)
    cost_usd: float = 0.0
    latencies: list[int] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    cases: dict = field(default_factory=dict)   # 逐题结果，对比两次运行时靠它找"翻转"的题
    pending: list[dict] = field(default_factory=list)  # 本周还没到期的断言

    # 到期的断言种类 -> Report 上对应的 Score 字段名
    DUE_SCORES = {"refuse": "refusal", "sql": "execution", "citation": "citation"}

    @property
    def errors(self) -> list[dict]:
        """抛了异常的题。和「答错」是两回事：答错是模型的问题，
        异常是跑不起来 —— 数据库没开、key 失效、方言没实现。"""
        return [f for f in self.failures if "error" in f]

    def unscored_due(
            self, week: int, case_counts: dict[str, int]
    ) -> list[str]:
        """本周该计分、却一题都没计上的指标。

        这是最危险的一类「假绿」：全线连不上数据库时 exec_acc 是 nan，
        而 nan < 1.0 为 False，所有基于阈值的门禁都会悄悄放行。
        """
        return [
            kind
            for kind, attr in self.DUE_SCORES.items()
            if week >= DUE_FROM[kind] 
            and case_counts.get(kind, 0) > 0
            and getattr(self, attr).total == 0
        ]

    def summary(self) -> dict:
        lat = sorted(self.latencies)
        p95 = lat[int(len(lat) * 0.95)] if lat else 0
        return {
            "intent_acc": self.intent.rate,
            "exec_acc": self.execution.rate,
            "refuse_acc": self.refusal.rate,
            "citation_acc": self.citation.rate,
            "cost_usd": round(self.cost_usd, 4),
            "p95_ms": p95,
            "n_cases": self.intent.total,
            "n_failures": len(self.failures) - len(self.errors),
            "n_errors": len(self.errors),
            "n_pending": len(self.pending),
        }


def num_close(a: str, b: str, typed: bool = False) -> bool:
    """typed 按标准答案 b 的写法选容差；默认保留原来的相对误差规则。"""
    if typed:
        expected_text = str(b).strip()
        try:
            da, db = Decimal(str(a).strip()), Decimal(expected_text)
        except InvalidOperation:
            return str(a).strip() == expected_text
        if not da.is_finite() or not db.is_finite():
            return False
        if "." not in expected_text:
            return da == db
        if len(expected_text.partition(".")[2]) <= 2:
            return abs(da - db) <= Decimal("0.01")

    # 未开启 typed，或标准答案有至少三位小数：沿用原来的规则。
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()
    if fb == 0:
        return abs(fa) < 1e-9
    return abs(fa - fb) / abs(fb) * 100 <= TOL_PCT


def rows_match(actual: list[dict], expected: list[dict], typed: bool = False) -> bool:
    """结果集比对：无序、只看 (k, v)。刻意不比较列名 —— 列名叫什么不重要，数字对才重要。"""
    if len(actual) != len(expected):
        return False
    remaining = list(expected)
    for row in actual:
        vals = list(row.values())
        for i, exp in enumerate(remaining):
            ek = exp.get("k")
            ev = exp["v"]
            key_ok = ek is None or any(str(v).strip() == ek for v in vals)
            val_ok = any(num_close(v, ev, typed=typed) for v in vals)
            if key_ok and val_ok:
                remaining.pop(i)
                break
        else:
            return False
    return not remaining


def load_cases(
    week: int,
    path: Path = GOLDEN,
    *,
    content: bytes | None = None,
    strict_citation: bool = False,
) -> list[dict]:
    text = (path.read_bytes() if content is None else content).decode("utf-8")
    cases = []
    line_numbers = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue

        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{path}:{line_no} [题号 <无法解析>] "
                f"JSON 格式错误：{exc.msg}"
            ) from exc

        cases.append(case)
        line_numbers.append(line_no)

    validate_cases(cases, path, line_numbers=line_numbers, strict_citation=strict_citation)
    return [case for case in cases if case["week"] <= week]

def validate_must_contain(value: object) -> None:
    """外层是列表；每项是非空字符串，或非空的字符串同义词组。"""
    if not isinstance(value, list):
        raise ValueError("must_contain 必须是列表")

    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            if not item.strip():
                raise ValueError(f"must_contain 第 {index} 项不能是空字符串")
        elif isinstance(item, list):
            if not item:
                raise ValueError(f"must_contain 第 {index} 项的同义词组不能为空")
            if not all(isinstance(word, str) and word.strip() for word in item):
                raise ValueError(
                    f"must_contain 第 {index} 项的同义词组只能包含非空字符串"
                )
        else:
            raise ValueError(
                f"must_contain 第 {index} 项必须是字符串或同义词组"
            )


def validate_cases(
    cases: list,
    source: str | Path,
    *,
    line_numbers: list[int] | None = None,
    strict_citation: bool = True,
) -> None:
    """校验整个文件；首个错误立即抛出，带文件、行号和题号。

    strict_citation：引用题必须写非空的 must_contain。只对仓库外的题目文件（--file，
    比如 NZ holdout）开启 —— 那是一次性测量，不许"只要有引用就算对"。
    仓库内的老题（chem Q009 / Q020）没写 must_contain，含义是"有引用即可"，保持原样。
    """
    required = ("id", "week", "intent", "question", "assert_type")
    allowed_types = {
        "sql", "refuse", "citation", "sql_and_citation", "no_data",
    }
    seen: dict[str, int] = {}

    for index, case in enumerate(cases):
        line_no = line_numbers[index] if line_numbers is not None else index + 1
        case_id = case.get("id", "<缺失>") if isinstance(case, dict) else "<未知>"

        def error(message: str) -> ValueError:
            return ValueError(
                f"{source}:{line_no} [题号 {case_id!r}] {message}"
            )

        if not isinstance(case, dict):
            raise error("每行必须是一个 JSON 对象")

        missing = [key for key in required if key not in case]
        if missing:
            raise error(f"缺少必填字段：{', '.join(missing)}")

        for key in ("id", "intent", "question"):
            if not isinstance(case[key], str) or not case[key].strip():
                raise error(f"{key} 必须是非空字符串")

        # bool 也是 int 的子类，因此这里用 type，拒绝 true / false。
        if type(case["week"]) is not int or case["week"] < 1:
            raise error("week 必须是正整数")

        if case_id in seen:
            raise error(f"id 重复，首次出现在第 {seen[case_id]} 行")
        seen[case_id] = line_no

        at = case["assert_type"]
        if not isinstance(at, str) or at not in allowed_types:
            raise error(f"不支持的 assert_type：{at!r}")

        if at in ("sql", "sql_and_citation"):
            expected = case.get("expected")
            if not isinstance(expected, list):
                raise error("expected 必须是列表")

            for row_no, row in enumerate(expected, start=1):
                if not isinstance(row, dict):
                    raise error(f"expected 第 {row_no} 行必须是对象")
                if "v" not in row:
                    raise error(f"expected 第 {row_no} 行缺少 v")
                for key, value in row.items():
                    if not isinstance(value, str):
                        raise error(
                            f"expected 第 {row_no} 行的 {key!r} 必须是字符串"
                        )

        if at in ("citation", "sql_and_citation"):
            if strict_citation and "must_contain" not in case:
                raise error("引用题缺少 must_contain")

            must_contain = case.get("must_contain", [])

            # 公共函数只负责格式检查。
            try:
                validate_must_contain(must_contain)
            except ValueError as exc:
                raise error(str(exc)) from exc

            # 外部文件的引用题额外要求外层列表非空。
            if strict_citation and not must_contain:
                raise error("引用题的 must_contain 不能为空")

        if at == "no_data" and case.get("expected"):
            raise error("no_data 题的 expected 必须为空或不写")

        # 原先缩进在引用题分支里，sql 题的 notes_must_contain 没被校验。
        if "notes_must_contain" in case:
            try:
                validate_must_contain(case["notes_must_contain"])
            except ValueError as exc:
                raise error(
                    f"notes_must_contain 格式错误：{exc}"
                ) from exc
                

def evaluate(
    case: dict, week: int, rep: Report, dry: bool, engine: str = "pipeline",
    typed: bool = False,
) -> str:
    """TODO(W1): 接上 router.plan()
       TODO(W2): 接上 text2sql.generate_and_run()
       TODO(W3): 接上 rag.hybrid_search() 并校验 citations
       TODO(W4): 接上 agents.answer() 走完整编排
    在 stub 阶段本函数直接记为未实现，分数为 0 —— 这是对的，你还没写。"""
    if dry:
        # 自检：标准答案能不能和自己比中？比不中说明 rows_match 或评测集有问题。
        exp = case.get("expected")
        if exp is not None and not rows_match(exp, exp, typed=typed):
            rep.failures.append({"id": case["id"], "error": "评测集自比对失败"})
            return "x"
        return "\u2713"

    # W4：两种做法签名一样，同一套题并排比。
    #   pipeline  固定流程（Client SDK）：router → text2sql → 知识库
    #   agent     分析员 Agent（Agent SDK）：自己决定调哪个工具、调几次
    if engine == "agent":
        from chemq.agents import answer  # noqa: PLC0415
    else:
        from chemq.pipeline import answer  # noqa: PLC0415  这是你要实现的入口

    t0 = time.perf_counter()
    result = answer(case["question"], as_of=domain().as_of)
    rep.latencies.append(int((time.perf_counter() - t0) * 1000))
    rep.cost_usd += getattr(result, "cost_usd", 0.0)

    intent_ok = getattr(result, "intent", None) == case["intent"]
    rep.intent.add(intent_ok)

    at = case.get("assert_type", "sql")
    due_failed = False
    waiting: list[str] = []

    rows_ok: bool | None = None
    notes_ok: bool | None = None

    if at == "refuse":
        if week >= DUE_FROM["refuse"]:
            r = bool(getattr(result, "refused", False))
            rep.refusal.add(r)
            due_failed = due_failed or not r
        else:
            waiting.append("refuse")

    if at in ("sql", "sql_and_citation"):
        if week >= DUE_FROM["sql"]:
            rows_ok = rows_match(
                result.rows, case["expected"], typed=typed
            )

            if "notes_must_contain" in case:
                notes_text = (
                    " ".join(getattr(result, "notes", []))
                    + " "
                    + (getattr(result, "answer", "") or "")
                )
                notes_ok = contains_all(
                    notes_text, case["notes_must_contain"]
                )

            # 结果行必须正确；有说明要求时，说明也必须通过。
            r = rows_ok and (notes_ok is not False)
            rep.execution.add(r)
            due_failed = due_failed or not r
        else:
            waiting.append("sql")

    if at == "no_data":
        # 计入 exec_acc：查询要跑通、结果为 0 行、不能拒答、要明确标出"没有数据"。
        if week >= DUE_FROM["sql"]:
            rows_ok = not getattr(result, "rows", [])
            r = (
                rows_ok
                and bool(getattr(result, "no_data", False))
                and not bool(getattr(result, "refused", False))
            )
            rep.execution.add(r)
            due_failed = due_failed or not r
        else:
            waiting.append("sql")

    if at in ("citation", "sql_and_citation"):
        if week >= DUE_FROM["citation"]:
            text = (result.answer or "") + json.dumps(
                [c.model_dump() for c in result.citations], ensure_ascii=False
            )
            cited = bool(result.citations) and contains_all(text, case.get("must_contain", []))
            rep.citation.add(cited)
            due_failed = due_failed or not cited
        else:
            waiting.append("citation")

    if waiting:
        rep.pending.append({"id": case["id"], "waiting_for": waiting,
                            "due_week": max(DUE_FROM[w] for w in waiting)})
    ok = not due_failed

    rep.cases[case["id"]] = {
        "intent_ok": intent_ok,
        "assert_ok": ok,
        "assert_type": at,
        "rows_ok": rows_ok,
        "notes_ok": notes_ok,
        "cost_usd": round(getattr(result, "cost_usd", 0.0), 6),
        "latency_ms": rep.latencies[-1] if rep.latencies else 0,
    }

    if not (ok and intent_ok):
        rep.failures.append({
            "id": case["id"], "question": case["question"],
            "expected_intent": case["intent"], "got_intent": getattr(result, "intent", None),
            "sql": getattr(result, "sql", None),
            "rows_ok": rows_ok,
            "notes_ok": notes_ok,
        })
        return "x"
    return "\u25e6" if waiting else "\u2713"    # ◦ = 有断言还没到期；✓ = 本周该过的都过了


def _normalize(text: str) -> str:
    ### 转小写：text.lower()；
    lower_text = text.lower()
    ### 去掉数字里的千位分隔符：re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)。
    ###这样 79,291 会变成 79291；
    ###但 1,2,3 这种列表不受影响，因为逗号后面不是正好 3 位数字。##
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", lower_text)


def _hit(text: str, item: str | list[str]) -> bool:
    if isinstance(item, str):
        return _normalize(item) in text

    return any(_normalize(word) in text for word in item)


def contains_all(text: str, must_contain: list) -> bool:
    validate_must_contain(must_contain)
    norm = _normalize(text)
    return all(_hit(norm, item) for item in must_contain)

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--dry", action="store_true", help="不调 API，只检查评测集能否加载")
    ap.add_argument("--only", help="只跑指定的题，逗号分隔：--only Q009,Q010")
    ap.add_argument("--model", help="覆盖 router 模型：--model claude-sonnet-5")
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                    help="output_config.effort。Haiku 4.5 不支持")
    ap.add_argument("--label", help="这次运行的名字，写进报告，对比时用")
    ap.add_argument("--set", dest="case_set", choices=["golden", "holdout"], default="golden",
                    help="golden=主评测 20 题（默认）；holdout=W4 新题验证集，跑完不许再拿来调规则")
    ap.add_argument("--file", type=Path, 
                    help="从仓库外的文件读题（holdout 交付用），不读 golden/holdout.jsonl")
    ap.add_argument("--engine", choices=["pipeline", "agent"], default="pipeline",
                    help="pipeline=固定流程（默认）；agent=Agent SDK 分析员（W4）")
    args = ap.parse_args()

    # 必须在 import chemq 之前设 —— settings() 有 lru_cache，读一次就定了
    if args.model:
        os.environ["CHEMQ_MODEL_ROUTER"] = args.model
    if args.effort:
        os.environ["CHEMQ_EFFORT"] = args.effort

    typed = (domain().config.get("eval") or {}).get("tolerance") == "typed"
    case_path = args.file or (
        HOLDOUT if args.case_set == "holdout" else GOLDEN
    )
    try:
        case_path = case_path.resolve()
        # 解析和哈希使用同一份字节，避免评测期间文件变化导致报告记错版本。
        case_bytes = case_path.read_bytes()
        cases_sha256 = hashlib.sha256(case_bytes).hexdigest()
        cases = load_cases(
            args.week, case_path, content=case_bytes,
            strict_citation=args.file is not None,
        )
    except (OSError, ValueError) as exc:
        print(f"评测集校验失败：{exc}", file=sys.stderr)
        return 2
    
    if args.only:
        want = {x.strip().upper() for x in args.only.split(",")}
        cases = [c for c in cases if c["id"].upper() in want]
        missing = want - {c["id"].upper() for c in cases}
        if missing:
            print(f"⚠ 这些题号在 week<={args.week} 的范围里不存在：{sorted(missing)}")
    rep = Report()

    case_counts = {"refuse": 0, "sql": 0, "citation": 0}
    
    for case in cases:
        at = case.get("assert_type", "sql")

        if at == "refuse":
            case_counts["refuse"] +=1

        if at in ("sql", "sql_and_citation", "no_data"):
            case_counts["sql"] +=1

        if at in ("citation", "sql_and_citation"):
            case_counts["citation"] +=1
    print(f"领域ID: {domain().id}  当前SQL方言: {settings().sql_dialect} 评测集: {args.case_set} {len(cases)} 题 (week<={args.week})  做法: {args.engine}\n")

    for c in cases:
        try:
            mark = evaluate(c, args.week, rep, args.dry, args.engine, typed=typed) or "·"
        except ModuleNotFoundError:
            print("chemq.pipeline 还没实现 —— 这是 W1 的第一个任务。")
            print("先跑 `python ai/evals/run_eval.py --week 1 --dry` 确认评测集本身没问题。")
            return 2
        except Exception as e:  # noqa: BLE001
            rep.failures.append({"id": c["id"], "error": repr(e)})
            mark = "x"
        print(f"  {mark} {c['id']} {c['question'][:34]}")

    if args.dry:
        bad = len(rep.failures)
        print(f"\n评测集自检: {len(cases) - bad}/{len(cases)} 通过")
        for f in rep.failures:
            print("  -", f)
        return 1 if bad else 0

    s = rep.summary()
    print("\n" + "=" * 52)
    for k, v in s.items():
        print(f"  {k:<14} {v}")
    print("=" * 52)
    if rep.errors:
        # 异常按类型聚合 —— 11 题挂在同一个 OperationalError 上，
        # 和 11 题各挂在不同地方，是完全不同的两件事，一眼要能看出来。
        import collections  # noqa: PLC0415
        buckets: dict[str, list[str]] = collections.defaultdict(list)
        for f in rep.errors:
            kind = str(f["error"]).split("(", 1)[0].strip() or "UnknownError"
            buckets[kind].append(f["id"])
        print(f"\n⚠️  {len(rep.errors)} 题抛异常（跑不起来，不是答错）:")
        for kind, ids in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            print(f"  {kind} ×{len(ids)}: {', '.join(sorted(ids)[:8])}"
                  + (" …" if len(ids) > 8 else ""))
        print("  " + str(rep.errors[0]["error"])[:200])

    real_failures = [f for f in rep.failures if "error" not in f]
    if real_failures:
        print("\n真正未通过（本周该做到却没做到）:")
        for f in real_failures[:10]:
            print("  -", json.dumps(f, ensure_ascii=False)[:160])
    elif not rep.errors:
        print("\n本周该做到的全部通过 ✓")

    if rep.pending:
        by_week: dict[int, list[str]] = {}
        for x in rep.pending:
            by_week.setdefault(x["due_week"], []).append(x["id"])
        print("\n还没到期（不计分，不是失败）:")
        for wk in sorted(by_week):
            print(f"  W{wk} 起: {', '.join(sorted(by_week[wk]))}")

    if args.json:
        from datetime import datetime, timezone  # noqa: PLC0415
        label = args.label or os.environ.get("CHEMQ_MODEL_ROUTER", "default")
        label += f"+{domain().id}"
        if args.effort:
            label += f"+{args.effort}"
        if args.engine != "pipeline":
            label += f"+{args.engine}"
        if args.case_set != "golden":
            label += f"+{args.case_set}"
        report = {
            "label": label,
            "config": {
                "week": args.week,
                "engine": args.engine,
                "set": args.case_set,
                "model_router": os.environ.get("CHEMQ_MODEL_ROUTER", "(用 .env 的值)"),
                "effort": args.effort or os.environ.get("CHEMQ_EFFORT", "(默认)"),
                "only": args.only,
                "cases_file": str(case_path),
                "cases_sha256": cases_sha256,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "domain": domain().display_name,
                "dialect":settings().sql_dialect,
            },
            "summary": s,
            "cases": rep.cases,
            "pending": rep.pending,
            "failures": rep.failures,
        }
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入 {args.json}（label={label}）")

    # ---- CI 门禁 ----
    # 退出码分两档，因为这两件事的处理方式完全不同：
    #   2 = 没跑起来（环境问题）—— 该修环境/workflow，revert 代码没用
    #   1 = 跑起来了但分不够（代码退化）—— 该 revert 或继续改
    #
    # 判「跑起来了没有」必须在「分够不够」之前。nan 不会 < 1.0，
    # 所以任何基于阈值的门禁都挡不住「全线异常」这种假绿 ——
    # 这个洞在本项目里真实出现过两次（API key 失效 17 题、数据库没开 11 题），
    # 两次 CI 都是绿的。
    unscored = rep.unscored_due(args.week, case_counts)
    if unscored and not args.only:
        names = {"refuse": "refuse_acc", "sql": "exec_acc", "citation": "citation_acc"}
        print(
            f"\n❌ W{args.week} 该计分的指标一题都没计上："
            f"{', '.join(names[k] for k in unscored)} 全是 nan。"
        )
        print("   这不是分数低，是根本没跑成。先看上面的异常类型。")
        return 2

    if rep.refusal.total and rep.refusal.rate < 1.0:
        print("\n❌ 拒答准确率 < 100%，不允许合并")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
