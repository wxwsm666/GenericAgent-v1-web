#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  GenericAgent — Chrome 扩展安装脚本 (macOS)
#  双击运行即可自动加载浏览器控制扩展
# ══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$SCRIPT_DIR/assets/tmwd_cdp_bridge"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   GenericAgent - Chrome 扩展安装工具                     ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check Chrome exists
CHROME=""
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
  if [ -f "$c" ]; then CHROME="$c"; break; fi
done

if [ -z "$CHROME" ]; then
  echo -e "${RED}❌ 未找到 Chrome 浏览器${NC}"
  echo "   请先安装 Google Chrome: https://www.google.com/chrome/"
  read -p "按 Enter 退出..."
  exit 1
fi

echo -e "${GREEN}✅ Chrome: $CHROME${NC}"
echo -e "${GREEN}✅ 扩展目录: $EXT_DIR${NC}"
echo ""

# Check manifest exists
if [ ! -f "$EXT_DIR/manifest.json" ]; then
  echo -e "${RED}❌ 扩展文件缺失: $EXT_DIR/manifest.json${NC}"
  read -p "按 Enter 退出..."
  exit 1
fi

echo -e "${YELLOW}📌 正在启动 Chrome 并加载扩展...${NC}"
echo ""
echo "  首次使用需要在 Chrome 中启用开发者模式："
echo "  1. 打开 chrome://extensions/"
echo "  2. 打开右上角「开发者模式」开关"
echo "  3. 点击「加载已解压的扩展程序」"
echo "  4. 选择目录: $EXT_DIR"
echo ""

# Kill existing Chrome if running (to load extension properly)
# pkill -f "Google Chrome" 2>/dev/null && sleep 1

# Launch Chrome with extension pre-loaded
nohup "$CHROME" \
  --load-extension="$EXT_DIR" \
  --disable-background-mode \
  "chrome://extensions/" \
  > /dev/null 2>&1 &

sleep 2

echo -e "${GREEN}✅ Chrome 已启动，扩展已自动加载${NC}"
echo ""
echo "  验证方法："
echo "  1. 打开任意网页"
echo "  2. 回到 GenericAgent Web UI (http://localhost:18600)"
echo "  3. 在设置中查看浏览器连接状态应为 ✅ 已连接"
echo ""
read -p "按 Enter 关闭..."
