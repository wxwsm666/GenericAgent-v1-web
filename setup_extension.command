#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  GenericAgent — Chrome 扩展一键安装 (macOS)
#  双击运行，自动检测 Chrome 状态并引导安装
# ══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="$SCRIPT_DIR/assets/tmwd_cdp_bridge"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

clear
echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   GenericAgent — Chrome 扩展安装                         ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Check Extension ──
if [ ! -f "$EXT_DIR/manifest.json" ]; then
  echo -e "${RED}❌ 扩展文件缺失: $EXT_DIR${NC}"
  read -p "按 Enter 退出..."; exit 1
fi
echo -e "${GREEN}✅ 扩展文件就绪: ${EXT_DIR}${NC}"

# ── Find Chrome ──
CHROME=""
for c in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
         "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
  [ -f "$c" ] && { CHROME="$c"; break; }
done
if [ -z "$CHROME" ]; then
  echo -e "${RED}❌ 未找到 Chrome，请先安装 Google Chrome${NC}"
  read -p "按 Enter 退出..."; exit 1
fi
echo -e "${GREEN}✅ Chrome: ${CHROME}${NC}"

# ── Check if Chrome is running ──
CHROME_RUNNING=$(pgrep -f "Google Chrome" | head -1)
if [ -n "$CHROME_RUNNING" ]; then
  echo -e "${YELLOW}⚠️  Chrome 正在运行，无法使用自动加载模式${NC}"
  echo ""
  echo "  请按以下步骤手动安装："
  echo ""
  echo -e "  ${CYAN}1.${NC} Chrome 地址栏输入 ${YELLOW}chrome://extensions${NC} 并回车"
  echo -e "  ${CYAN}2.${NC} 打开右上角 ${YELLOW}「开发者模式」${NC} 开关"
  echo -e "  ${CYAN}3.${NC} 点击左上角 ${YELLOW}「加载已解压的扩展程序」${NC}"
  echo -e "  ${CYAN}4.${NC} 选择目录: ${YELLOW}${EXT_DIR}${NC}"
  echo ""

  # Auto-open extensions page and Finder
  open "chrome://extensions" 2>/dev/null &
  open -R "$EXT_DIR" 2>/dev/null &

  echo -e "${GREEN}✅ 已自动打开 extensions 页面和扩展文件夹${NC}"
  echo -e "   将文件夹中的内容拖入扩展页面即可（或按上方步骤操作）"
  echo ""
else
  echo -e "${YELLOW}📌 Chrome 未运行，使用自动加载模式...${NC}"
  echo ""

  # Launch Chrome with extension pre-loaded + open extensions page
  nohup "$CHROME" \
    --load-extension="$EXT_DIR" \
    "chrome://extensions/" \
    > /dev/null 2>&1 &

  sleep 2

  echo -e "${GREEN}✅ Chrome 已启动，扩展已临时加载${NC}"
  echo ""
  echo -e "  ${YELLOW}⚠️  临时加载仅在本次 Chrome 会话有效${NC}"
  echo "  如需永久安装，请在 chrome://extensions 页面："
  echo -e "  ${CYAN}1.${NC} 打开右上角 ${YELLOW}「开发者模式」${NC}"
  echo -e "  ${CYAN}2.${NC} 确认 ${YELLOW}TMWD CDP Bridge${NC} 扩展已启用"
  echo -e "  ${CYAN}3.${NC} 若未出现，点「加载已解压的扩展程序」选: ${YELLOW}${EXT_DIR}${NC}"
  echo ""
fi

echo "  验证方法："
echo "  1. 回到 GenericAgent Web UI (http://localhost:18600)"
echo "  2. 查看顶部工具栏浏览器图标应为 🌐（绿色已连接）"
echo "  3. 或在设置页面查看浏览器连接状态"
echo ""

read -p "按 Enter 关闭..."
