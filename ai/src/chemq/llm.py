"""Claude Client SDK 封装 —— W1 的核心产物。

这一个文件里就覆盖了面试会被问到的大部分"Claude 概念"：
  · Messages API 与 system / user / assistant 三种 role
  · prompt caching（cache_control ephemeral）—— 指标字典和 schema 每次都传，
    用缓存把重复输入的成本压到 ~10%
  · structured outputs（messages.parse + pydantic）—— 拿到强类型结果，不再解析 JSON 字符串
  · 模型分级路由（haiku 做分类、sonnet 做生成）—— 成本与质量的工程取舍
  · thinking / effort —— 复杂 SQL 时打开，简单分类时关掉
  · usage 记账 —— 每次调用都算成本，W6 的成本看板靠它
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from anthropic import Anthropic, APIConnectionError
from pydantic import BaseModel

from .config import settings

T = TypeVar("T", bound=BaseModel)

# 单位：USD / 1M tokens。来源：platform.claude.com 定价页，改价时只改这里。
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0     # 思考 token，按 output 计费，含在 output_tokens 里
    latency_ms: int = 0

    @property
    def cached(self) -> bool:
        return bool(self.cache_read_tokens or self.cache_write_tokens)

    def cache_hint(self) -> str:
        """两个缓存计数都是 0 时，说清楚为什么。"""
        if self.cached:
            return ""
        need = min_cache_tokens(self.model)
        total = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        if need is None:
            return "缓存未生效（该模型的最小可缓存长度未知）"
        if total < need:
            return (
                f"缓存未生效：前缀约 {total} tokens，{self.model} 要求至少 {need}。"
                f"\n     短于下限时 cache_control 被静默忽略 —— 不报错，也不建缓存。"
                f"\n     想看到缓存生效：把前缀加长，或换最小值更低的模型"
                f"（sonnet-5 要 1024，opus-5 要 512）"
            )
        return "缓存未生效：长度够了，八成是前缀不稳定（前面某个字节变了）"

    @property
    def cost_usd(self) -> float:
        cin, cout = PRICING.get(self.model, (0.0, 0.0))
        # 缓存命中按输入价 10% 计，缓存写入按 125% 计（5 分钟 TTL）
        billed_in = (
            self.input_tokens
            + self.cache_read_tokens * 0.10
            + self.cache_write_tokens * 1.25
        )
        return round(billed_in / 1e6 * cin + self.output_tokens / 1e6 * cout, 6)


@dataclass
class LlmResult:
    text: str = ""
    parsed: Any = None
    usage: Usage = field(default_factory=Usage)


# 最小可缓存长度 —— 按模型不同，差 8 倍。
# 前缀短于这个数，cache_control 会被静默忽略：不报错、不建缓存、cache_read 永远 0。
# 来源：platform.claude.com/docs/en/build-with-claude/prompt-caching
MIN_CACHE_TOKENS: dict[str, int] = {
    "claude-opus-5": 512,
    "claude-fable-5-1": 512,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-haiku-4-5": 4096,
}


def min_cache_tokens(model: str) -> int | None:
    """这个模型的前缀至少要多长才可能被缓存。未知模型返回 None。"""
    for prefix, n in MIN_CACHE_TOKENS.items():
        if model.startswith(prefix):
            return n
    return None


OFFICIAL_BASE_URL = "https://api.anthropic.com"
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


class Llm:
    def __init__(self) -> None:
        s = settings()
        if not s.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "没读到 API key。\n"
                f"  期望从这个文件读：{s.env_file_path}\n"
                f"  它存在吗：{'是' if s.env_file_path.exists() else '否 —— cp .env.example .env'}\n"
                "  文件里需要有这一行（前缀 CHEMQ_ 不能少）：\n"
                "      CHEMQ_ANTHROPIC_API_KEY=sk-ant-api03-...\n"
                "  验证：bash scripts/doctor.sh"
            )
        kwargs: dict[str, Any] = {"api_key": s.anthropic_api_key or None}
        if s.anthropic_base_url:
            kwargs["base_url"] = s.anthropic_base_url
        self._client = Anthropic(**kwargs)
        self._s = s
        # 记下实际生效的地址，报错时要用。注意优先级：
        # 显式配置 > 裸的 ANTHROPIC_BASE_URL 环境变量 > 官方地址
        self._base_url = (
            s.anthropic_base_url or os.environ.get("ANTHROPIC_BASE_URL") or OFFICIAL_BASE_URL
        )

    def _connection_error(self, err: Exception) -> RuntimeError:
        """把 APIConnectionError 翻译成能直接动手的诊断。

        这类错误 99% 不是代码问题，是环境变量污染：某个 AI 工具往 shell 里塞了
        ANTHROPIC_BASE_URL 或代理变量，指向一个现在解析不了的主机。
        """
        lines = ["连不上 Claude API。这几乎都是环境问题，不是代码问题。", ""]
        lines.append(f"  实际请求的地址：{self._base_url}")

        if self._base_url != OFFICIAL_BASE_URL:
            src = "CHEMQ_ANTHROPIC_BASE_URL（.env 里）" if self._s.anthropic_base_url \
                  else "ANTHROPIC_BASE_URL（shell 环境变量）"
            lines += [
                f"  ← 不是官方地址，来自 {src}",
                "",
                "  如果你没打算走第三方网关，清掉它：",
                "      unset ANTHROPIC_BASE_URL          # 当前终端立刻生效",
                "      grep -rn ANTHROPIC_BASE_URL ~/.zshrc ~/.zprofile ~/.bash_profile",
                "      # 找到之后从配置文件里删掉，否则每开一个终端又回来",
            ]

        proxies = {k: os.environ[k] for k in PROXY_VARS if os.environ.get(k)}
        if proxies:
            lines += ["", "  生效中的代理变量："]
            lines += [f"      {k}={v}" for k, v in proxies.items()]
            lines += [
                "  代理主机连不上时报的就是这个错。临时排除：",
                "      unset " + " ".join(proxies),
            ]
        else:
            lines += ["", "  没有代理变量。那就查 DNS 和网络："]
            lines += [
                "      nslookup api.anthropic.com",
                "      curl -sS -o /dev/null -w '%{http_code}' https://api.anthropic.com/v1/models \\",
                "        -H \"x-api-key: $CHEMQ_ANTHROPIC_API_KEY\" -H 'anthropic-version: 2023-06-01'",
            ]

        lines += ["", f"  原始错误：{type(err).__name__}: {err}"]
        return RuntimeError("\n".join(lines))

    # ---------- 带缓存的 system 块 ----------
    @staticmethod
    def cached_system(*blocks: str) -> list[dict]:
        """最后一个块打 cache_control，前缀（含它）整体进缓存。

        顺序很重要：把"稳定且大"的内容（指标字典、schema DDL、few-shot）放前面，
        把"每次都变"的内容（用户问题）放 messages 里，缓存命中率才高。
        """
        out: list[dict] = [{"type": "text", "text": b} for b in blocks if b]
        if out:
            out[-1]["cache_control"] = {"type": "ephemeral"}
        return out

    def count_tokens(self, *, model: str, system: list[Any] | str, user: str = "x") -> int:
        """只数 token，不生成。这个接口不计费 —— 调 prompt 时先用它量，别靠猜。"""
        resp = self._client.messages.count_tokens(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.input_tokens

    # ---------- 自由文本 ----------
    def complete(
        self,
        *,
        model: str,
        system: list[dict] | str,
        user: str,
        max_tokens: int | None = None,
        thinking_budget: int | None = None,
    ) -> LlmResult:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self._s.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if thinking_budget:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}

        t0 = time.perf_counter()
        try:
            resp = self._client.messages.create(**kwargs)
        except APIConnectionError as e:
            raise self._connection_error(e) from e
        return LlmResult(
            text="".join(b.text for b in resp.content if b.type == "text"),
            usage=self._usage(model, resp, t0),
        )

    # ---------- 结构化输出 ----------
    def parse(
        self,
        *,
        model: str,
        system: list[dict] | str,
        user: str,
        schema: type[T],
        max_tokens: int | None = None,
        effort: str | None = None,
    ) -> LlmResult:
        """用 structured outputs 拿到强类型对象，schema 由 pydantic 模型生成。

        effort 走 output_config.effort。⚠ 改动顶层 effort 会让 prompt cache 失效 ——
        对比实验时这一点会体现在成本上，别误以为是别的原因。
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self._s.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
        }
        eff = effort if effort is not None else (self._s.effort or None)
        if eff:
            kwargs["output_config"] = {"effort": eff}

        t0 = time.perf_counter()
        try:
            resp = self._client.messages.parse(**kwargs)
        except TypeError as e:
            if "output_config" in str(e):
                raise RuntimeError(
                    f"当前 anthropic SDK 的 messages.parse() 不接受 output_config"
                    f"（effort={eff}）。两条路：升级 SDK（uv add 'anthropic@latest'），"
                    f"或改用 messages.create() 自己传 output_config 并解析 JSON。\n"
                    f"  原始错误：{e}"
                ) from e
            raise
        except APIConnectionError as e:
            raise self._connection_error(e) from e
        return LlmResult(parsed=resp.parsed_output, usage=self._usage(model, resp, t0))

    @staticmethod
    def _usage(model: str, resp: Any, t0: float) -> Usage:
        u = getattr(resp, "usage", None)
        details = getattr(u, "output_tokens_details", None)
        return Usage(
            thinking_tokens=getattr(details, "thinking_tokens", 0) or 0,
            model=model,
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )
