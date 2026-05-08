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
VERSION=$(python3 -c "import json; print(json.load(open('$SCRIPT_DIR/version.json')).get('version','unknown'))" 2>/dev/null || echo "unknown")
echo "║   GenericAgent Web UI v${VERSION}                             ║"
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
    pip install flask requests 'beautifulsoup4>=4.12' bottle simple-websocket-server rumps Pillow lark-oapi dingtalk-stream rapidocr-onnxruntime -q
    touch "$DEPS_FLAG"
    echo -e "${GREEN}✅ 依赖安装完成${NC}"
fi

# ── 检查 mykey.py ──
if [ ! -f "$SCRIPT_DIR/mykey.py" ]; then
    echo -e "${YELLOW}⚠️  未找到 mykey.py，正在从模板创建...${NC}"
    if [ -f "$SCRIPT_DIR/mykey_template.py" ]; then
        cp "$SCRIPT_DIR/mykey_template.py" "$SCRIPT_DIR/mykey.py"
        echo -e "${YELLOW}📝 已创建 mykey.py，启动后在网页中配置 API Key 即可${NC}"
    fi
fi

# ── 确保 update_source 存在（老用户自动修复）──
if [ -f "$SCRIPT_DIR/mykey.py" ]; then
    if ! grep -q '^update_source\s*=' "$SCRIPT_DIR/mykey.py"; then
        echo -e "${YELLOW}🔧 检测到旧版 mykey.py，正在添加更新源配置...${NC}"
        echo "" >> "$SCRIPT_DIR/mykey.py"
        echo "# ── 在线更新配置 ──" >> "$SCRIPT_DIR/mykey.py"
        echo "update_source = 'https://raw.githubusercontent.com/wxwsm666/GenericAgent-v1-web/main/version.json'" >> "$SCRIPT_DIR/mykey.py"
        echo "update_channel = 'stable'" >> "$SCRIPT_DIR/mykey.py"
        echo -e "${GREEN}✅ 更新源已自动添加${NC}"
    fi
fi

# ── 端口冲突检测 ──
PORT=18600
# Kill any existing process on our port
if lsof -ti :$PORT &>/dev/null; then
    echo -e "${YELLOW}⚠️  端口 $PORT 被占用，正在释放...${NC}"
    kill -9 $(lsof -ti :$PORT) 2>/dev/null || true
    sleep 0.5
fi
# Also kill legacy port 18581 if occupied
if lsof -ti :18581 &>/dev/null; then
    echo -e "${YELLOW}⚠️  检测到旧版服务在端口 18581 运行中，正在释放...${NC}"
    kill -9 $(lsof -ti :18581) 2>/dev/null || true
    sleep 0.5
fi

# ── 启动服务（后台运行）──
echo -e "${GREEN}🚀 启动 Web UI...${NC}"
cd "$SCRIPT_DIR/frontends"

# Start server in background
python3 web_server.py --port $PORT --no-browser &
SERVER_PID=$!
sleep 2

# Check server is running
if ! curl -s http://localhost:$PORT/api/status > /dev/null 2>&1; then
  echo -e "${RED}❌ 服务启动失败，请检查日志${NC}"
  read -p "按 Enter 退出..."
  exit 1
fi

echo ""
echo "  浏览器已打开:  http://localhost:$PORT"
echo "  💡 关闭浏览器后重新打开此页面即可继续使用"
echo "  按 Ctrl+C 停止服务"
echo ""

# ── 打开浏览器 ──
open "http://localhost:$PORT"

# ── 检测浏览器扩展 ──
echo -e "${CYAN}🔍 检测浏览器扩展...${NC}"
EXT_STATUS=$(curl -s http://localhost:$PORT/api/browser/status 2>/dev/null || echo '{"connected":false}')
EXT_CONNECTED=$(python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print('yes' if d.get('connected') else 'no')" <<< "$EXT_STATUS" 2>/dev/null || echo "no")

if [ "$EXT_CONNECTED" = "yes" ]; then
  TAB_COUNT=$(python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('tab_count',0))" <<< "$EXT_STATUS" 2>/dev/null || echo "0")
  echo -e "${GREEN}✅ 浏览器扩展已连接 (${TAB_COUNT}个标签页)${NC}"
else
  echo -e "${YELLOW}⚠️  浏览器扩展未连接 — web_scan/web_execute_js 将不可用${NC}"
  echo -e "  一键安装: 双击 ${CYAN}setup_extension.command${NC}"
  echo ""
fi

echo ""

# 保持终端打开，等待服务进程
wait $SERVER_PID 2>/dev/null
echo ""
echo "服务已停止，按 Enter 关闭窗口..."
read
