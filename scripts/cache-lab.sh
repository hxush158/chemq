#!/usr/bin/env bash
# 破坏实验 A · 缓存在什么条件下才存在
#
#   bash scripts/cache-lab.sh
#   bash scripts/cache-lab.sh "8月哪个大区回款率最低"     # 换个问题也行
#
# 一共 6 次 API 调用，总成本不到 $0.03。
# ⚠ 全程别中断超过 5 分钟 —— 默认缓存 TTL 就是 5 分钟，过期了你会以为是别的原因。
set -uo pipefail
cd "$(dirname "$0")/../ai"

Q="${1:-8月产量多少}"

echo "════════ 第一步：量一下前缀有多长 ════════"
echo "（count_tokens 接口不计费）"
echo
uv run chemq tokens "$Q"

run3() {
  local model="$1" label="$2"
  printf "\n════════ %s ════════\n" "$label"
  for i in 1 2 3; do
    local line
    line=$(CHEMQ_MODEL_ROUTER="$model" uv run chemq plan "$Q" 2>/dev/null \
           | grep -m1 'cache_read=')
    if [ -z "$line" ]; then
      printf "  第%d次  (没拿到 usage —— 调用可能失败了，单独跑一次看报错)\n" "$i"
    else
      printf "  第%d次 %s\n" "$i" "$line"
    fi
  done
}

run3 "claude-haiku-4-5-20251001" "第二步：Haiku 4.5（下限 4096，前缀不够）"
echo "  ↑ cache_write 和 cache_read 应该全是 0 —— 缓存压根没建，API 不报错"

run3 "claude-sonnet-5" "第三步：Sonnet 5（下限 1024，前缀够了）"
echo "  ↑ 第1次 cache_write>0（写缓存，付 125%）"
echo "    第2、3次 cache_read>0（命中，输入按 10% 计费）← 这就是你要看的那一刻"

cat <<'TXT'

════════ 该记进笔记的 ════════

  同一份 prompt、同一个问题，只换了模型，缓存的开关就变了。

  「用便宜模型省钱」不是无条件成立的：
    Haiku 单价是 Sonnet 的一半，但这个前缀长度下它拿不到缓存折扣；
    Sonnet 单价贵一倍，命中缓存后重复输入只付 10%。
  算总账要把缓存算进去，不能只看每百万 token 的标价。

  顺手算一下：假设一天问 200 次，前缀 1200 tokens。
    Haiku  无缓存：200 × 1200 × $1/1M       = ?
    Sonnet 有缓存：200 × 1200 × $2/1M × 0.1 = ?
  哪个便宜？（答案可能和你的直觉相反）
TXT
