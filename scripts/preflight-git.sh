#!/usr/bin/env bash
# 首次推 GitHub 之前跑一遍：扫全树，不只是暂存区。
#   bash scripts/preflight-git.sh
set -uo pipefail
cd "$(dirname "$0")/.."

FAIL=0
ok(){ printf "  \033[32m✓\033[0m %s\n" "$1"; }
bad(){ printf "  \033[31m✗\033[0m %s\n" "$1"; FAIL=1; }
warn(){ printf "  \033[33m!\033[0m %s\n" "$1"; }

echo "── 1. 密钥扫描（全部会被提交的文件）──"
FILES=$(git ls-files --cached --others --exclude-standard 2>/dev/null || find . -type f -not -path './.git/*')
HITS=$(echo "$FILES" | xargs grep -lE 'sk-ant-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|BEGIN [A-Z ]*PRIVATE KEY' 2>/dev/null)
if [ -n "$HITS" ]; then
  bad "这些文件里有疑似密钥："
  echo "$HITS" | sed 's/^/      /'
else
  ok "没有发现密钥"
fi

echo "── 2. .env 状态 ──"
if [ -f .env ]; then
  if git check-ignore -q .env 2>/dev/null; then ok ".env 存在，且已被 gitignore"
  else bad ".env 存在但没被忽略！检查 .gitignore"; fi
else
  warn ".env 不存在（还没配 key？）"
fi

echo "── 3. 会被提交的大文件（>500KB）──"
BIG=$(echo "$FILES" | while read -r f; do
  [ -f "$f" ] && s=$(wc -c <"$f") && [ "$s" -gt 512000 ] && printf "      %6s KB  %s\n" "$((s/1024))" "$f"
done)
if [ -n "$BIG" ]; then echo "$BIG"; warn "确认这些该进版本库（02_seed.sql 是有意提交的，见 README）"
else ok "没有大文件"; fi

echo "── 4. 业务信息检查 ──"
# 仓库要是公开的，别把客户真实信息推上去
# 只拦"曙光化工"会漏掉"接曙光""曙光那边"这类写法（W4 之后真实发生过）—— 拦单独的"曙光"两个字。
# docs/ 也是公开的，不再排除；只排除本脚本自己（它里面写着这些词）。
REAL=$(echo "$FILES" | xargs grep -lE '曙光|安庆|Shuguang|Anqing' 2>/dev/null | grep -v '^scripts/preflight-git.sh$' || true)
if [ -n "$REAL" ]; then
  bad "以下文件出现了真实客户名（仓库是公开的，必须处理）："
  echo "$REAL" | sed 's/^/      /'
else
  ok "代码和数据里没有真实客户名（naming.yaml 是占位符）"
fi

echo
[ "$FAIL" -eq 0 ] && printf "  \033[32m可以推。\033[0m\n" || printf "  \033[31m先解决 ✗ 项。\033[0m\n"
exit "$FAIL"
