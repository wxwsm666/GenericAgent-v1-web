#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  GenericAgent — 一键启动脚本 (macOS)
#  双击本文件即可启动 Web UI，首次运行会自动安装依赖。
# ══════════════════════════════════════════════════════════════════════════════

set -e

# ── 获取脚本所在目录（解决双击时 CWD 不是项目目录的问题）──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   GenericAgent Web UI v0.8.1                             ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── 检查 Python ──
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ 未找到 python3，请先安装 Python 3.10+${NC}"
    echo "   https://www.python.org/downloads/"
    read -p "按 Enter 退出..."
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}✅ Python ${PY_VER}${NC}"

# ── 创建/激活虚拟环境 ──
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    echo -e "${YELLOW}📦 首次运行，创建虚拟环境...${NC}"
    python3 -m venv "$VENV_DIR"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# ── 安装依赖 ──
DEPS_FLAG="$VENV_DIR/.deps_installed"
if [ ! -f "$DEPS_FLAG" ]; then
    echo -e "${YELLOW}📦 安装依赖包...${NC}"
    pip install --upgrade pip -q
    pip install flask requests 'beautifulsoup4>=4.12' bottle simple-websocket-server rumps Pillow lark-oapi dingtalk-stream -q
    touch "$DEPS_FLAG"
    echo -e "${GREEN}✅ 依赖安装完成${NC}"
fi

# ── 检查 mykey.py ──
if [ ! -f "$SCRIPT_DIR/mykey.py" ]; then
    echo -e "${YELLOW}⚠️  未找到 mykey.py，正在从模板创建...${NC}"
    if [ -f "$SCRIPT_DIR/mykey_template.py" ]; then
        cp "$SCRIPT_DIR/mykey_template.py" "$SCRIPT_DIR/mykey.py"
        echo -e "${YELLOW}📝 请编辑 mykey.py 填入你的 API Key，然后重新运行本脚本${NC}"
        open "$SCRIPT_DIR/mykey.py" 2>/dev/null || true
        read -p "按 Enter 退出..."
        exit 0
    else
        echo -e "${RED}❌ 模板文件也丢失，请手动创建 mykey.py${NC}"
        read -p "按 Enter 退出..."
        exit 1
    fi
fi

# ── 启动 ──
echo -e "${GREEN}🚀 启动 Web UI...${NC}"
echo ""
echo "  打开浏览器访问:  http://localhost:18600"
echo "  按 Ctrl+C 停止服务"
echo ""

cd "$SCRIPT_DIR/frontends"
python3 web_server.py --port 18600

# 保持终端打开
echo ""
read -p "服务已停止，按 Enter 关闭窗口..."
