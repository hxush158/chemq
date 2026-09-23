#!/usr/bin/env bash
# 把 .githooks/ 设为这个仓库的 hooks 目录。
# 用 core.hooksPath 而不是往 .git/hooks 里拷 —— 这样 hook 本身能进版本库，
# 换台机器 clone 下来跑一次这个脚本就有，不会丢。
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
echo "✓ hooks 已启用（core.hooksPath=.githooks）"
echo "  现在每次 git commit 都会先扫一遍暂存区有没有密钥"
