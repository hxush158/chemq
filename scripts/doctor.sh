#!/usr/bin/env bash
# W1 体检：不需要 Docker / 数据库。
# 最后一步会真打一次 API —— 只有这一步能同时验证「地址对不对」和「key 有没有效」。
# 直接跑： bash scripts/doctor.sh
set -uo pipefail
cd "$(dirname "$0")/.."

FAIL=0
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad()  { printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }

echo "--- 工具 ---"
if command -v uv >/dev/null 2>&1; then ok "uv $(uv --version 2>/dev/null)"
else bad "uv 未安装 -> curl -LsSf https://astral.sh/uv/install.sh | sh"; fi
if [ -d ai/.venv ]; then ok "ai/.venv 已创建"
else bad "ai/.venv 未创建 -> make install"; fi

echo "--- API key ---"
KEY=""
if [ ! -f .env ]; then
  bad ".env 不存在 -> cp .env.example .env"
else
  KEY=$(grep -E '^CHEMQ_ANTHROPIC_API_KEY=' .env | head -1 | cut -d= -f2- \
        | tr -d '\r' | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//' -e 's/[[:space:]]*$//')
  if   [ -z "$KEY" ];              then bad ".env 里 CHEMQ_ANTHROPIC_API_KEY 是空的"
  elif [[ "$KEY" == *"..."* ]];    then bad "还是占位符 —— 你 cp 了 .env.example 但没真填 key"
  elif [ "${#KEY}" -lt 40 ];       then bad "key 只有 ${#KEY} 个字符，真 key 通常 100+ —— 八成填错了"
  else ok "key 已填（${#KEY} 字符，以 ${KEY:0:10}… 开头）"; fi
fi

echo "--- 环境变量劫持检查 ---"
# 这一段是重点。SDK 会读裸的 ANTHROPIC_* 和代理变量，
# 你的配置可能被一个你没写、也看不见的东西接管。
HIJACK=0
if [ -n "${ANTHROPIC_BASE_URL:-}" ]; then
  warn "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
  warn "  SDK 会用它而不是官方地址。不是你有意配的就清掉："
  warn "  unset ANTHROPIC_BASE_URL && grep -rn ANTHROPIC_BASE_URL ~/.zshrc ~/.zprofile ~/.bash_profile"
  HIJACK=1
fi
if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  warn "ANTHROPIC_AUTH_TOKEN 已设置 —— 它会随请求发往上面那个地址"
  HIJACK=1
fi
for v in HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy; do
  [ -n "${!v:-}" ] && { warn "$v=${!v}"; HIJACK=1; }
done
[ "$HIJACK" -eq 0 ] && ok "没有会干扰的环境变量"

echo "--- 真实调用 ---"
BASE="${CHEMQ_ANTHROPIC_BASE_URL:-${ANTHROPIC_BASE_URL:-https://api.anthropic.com}}"
if [ -z "$KEY" ]; then
  bad "没有 key，跳过（先把上面的 key 问题解决）"
else
  CODE=$(curl -sS -m 20 -o /dev/null -w '%{http_code}' "$BASE/v1/models" \
         -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' 2>/dev/null) || true
  [ -z "$CODE" ] && CODE=000
  case "$CODE" in
    200) ok "API 可达且 key 有效  ($BASE)" ;;
    401) bad "key 无效，HTTP 401  ($BASE)"
         bad "  去 https://platform.claude.com -> API Keys 建一个新的，粘进 .env" ;;
    403) bad "HTTP 403 —— key 有效但没权限，或余额/额度问题  ($BASE)" ;;
    000) bad "连不上 $BASE"
         bad "  地址不对（看上面的劫持检查）、代理不通、或网络问题" ;;
    *)   warn "HTTP $CODE  ($BASE) —— 非预期，贴出来看看" ;;
  esac
fi

echo "--- 依赖 ---"
if (cd ai && uv run --quiet python -c "import chemq, yaml, anthropic, sqlglot" 2>/dev/null); then
  ok "chemq 包可导入"
else bad "依赖装得不全 -> make install"; fi

echo
if [ "$FAIL" -eq 0 ]; then
  printf "  \033[32m全绿。\033[0m 开始 W1：make test && make eval-dry && make eval WEEK=1\n"
else
  printf "  \033[31m有 ✗ 项。\033[0m 按上面的提示逐条解决，再跑一次 bash scripts/doctor.sh\n"
fi
exit "$FAIL"
