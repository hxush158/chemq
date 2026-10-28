#!/usr/bin/env bash
# 新 Mac 一键装开发环境。只装，不改你已有的东西；每步都先检查装没装过。
#
#   bash scripts/bootstrap-macos.sh
#
# 装完需要你手动做两件事，脚本最后会提示：
#   1. 打开 Docker Desktop（第一次要点同意）
#   2. 填 .env 里的 API key
set -uo pipefail

ARCH=$(uname -m)
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
step() { printf "\n\033[1m── %s\033[0m\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }

printf "\033[1mChemQ 开发环境安装\033[0m  (macOS %s / %s)\n" "$(sw_vers -productVersion)" "$ARCH"

step "1/6  Xcode 命令行工具（提供 git 和 make）"
if xcode-select -p >/dev/null 2>&1; then
  ok "已安装：$(xcode-select -p)"
else
  warn "弹窗会出现，点「安装」，装完再回来重跑这个脚本"
  xcode-select --install
  exit 0
fi

step "2/6  Homebrew"
if command -v brew >/dev/null 2>&1; then
  ok "已安装：$(brew --version | head -1)"
else
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || exit 1
  # Apple Silicon 装在 /opt/homebrew，Intel 在 /usr/local。写进 .zprofile 让新终端也能用
  BREW_PREFIX=$([ "$ARCH" = "arm64" ] && echo /opt/homebrew || echo /usr/local)
  grep -q "$BREW_PREFIX/bin/brew shellenv" ~/.zprofile 2>/dev/null \
    || echo "eval \"\$($BREW_PREFIX/bin/brew shellenv)\"" >> ~/.zprofile
  eval "$($BREW_PREFIX/bin/brew shellenv)"
  ok "已安装"
fi

step "3/6  uv（Python 包管理器，自带 Python）"
if command -v uv >/dev/null 2>&1; then
  ok "已安装：$(uv --version)"
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  ok "已安装（新终端才会自动进 PATH）"
fi

step "4/6  Docker Desktop"
if [ -d "/Applications/Docker.app" ]; then
  ok "已安装"
else
  brew install --cask docker && ok "已安装"
fi

step "5/6  VS Code"
if [ -d "/Applications/Visual Studio Code.app" ]; then
  ok "已安装"
else
  brew install --cask visual-studio-code && ok "已安装"
fi

step "6/6  环境变量体检"
# 上一台机器栽在这里：某个 AI 工具往 shell 里塞了 ANTHROPIC_BASE_URL，
# SDK 就不去官方地址了，报的错和「没配 key」毫无关系。新机器上先确认干净。
DIRTY=0
for v in ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN HTTP_PROXY HTTPS_PROXY ALL_PROXY; do
  if [ -n "${!v:-}" ]; then warn "$v=${!v}  ← 会劫持 SDK，确认是你有意配的"; DIRTY=1; fi
done
[ "$DIRTY" -eq 0 ] && ok "干净，没有会干扰的变量"

cat <<'TXT'

────────────────────────────────────────────────
接下来手动做三件事：

  1. 打开 Docker Desktop（首次要点同意条款），
     等菜单栏鲸鱼图标不再转

  2. cp .env.example .env
     然后编辑 .env，填 CHEMQ_ANTHROPIC_API_KEY
     key 在 https://platform.claude.com → API Keys 建
     ⚠ 只贴一次，别贴重复（上次栽过：216 字符 = 贴了两遍）

  3. 关掉终端重开（让 brew 和 uv 进 PATH），然后：

       bash scripts/doctor.sh      # 全绿才往下走
       make up                     # 起数据库，首次约 2-3 分钟
       make doctor                 # 完整体检（含数据库）
       make install-db             # W2 的依赖
       make test && make eval-dry
────────────────────────────────────────────────
TXT
