#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
#  GenericAgent — 导出脚本
#  运行: bash export.sh
#  同时生成 macOS 和 Windows 两个版本的 zip 包
# ══════════════════════════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

VERSION="v0.6.0"
BASE_EXPORT_DIR="$SCRIPT_DIR/../GenericAgent_${VERSION}_export"
OUTPUT_DIR="$(dirname "$SCRIPT_DIR")"

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║   GenericAgent — Export ${VERSION}                       ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── 清理 ──
rm -rf "$BASE_EXPORT_DIR"

# ── 复制项目（共用）──
echo -e "${YELLOW}📦 复制项目文件...${NC}"
mkdir -p "$BASE_EXPORT_DIR"

rsync -a \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='.git' \
    --exclude='.vscode' \
    --exclude='.idea' \
    --exclude='.DS_Store' \
    --exclude='temp/' \
    --exclude='tmp/' \
    --exclude='backups/' \
    --exclude='*.zip' \
    --exclude='*.tar.gz' \
    --exclude='Thumbs.db' \
    "$SCRIPT_DIR/" "$BASE_EXPORT_DIR/"

# ── 确保必要的空目录 ──
mkdir -p "$BASE_EXPORT_DIR/temp"
mkdir -p "$BASE_EXPORT_DIR/temp/model_responses"
mkdir -p "$BASE_EXPORT_DIR/temp/uploads"

# ── 移除 mykey.py ──
rm -f "$BASE_EXPORT_DIR/mykey.py"

# ═══════════════ Windows 版本 ═══════════════
echo -e "${YELLOW}📦 生成 Windows 版本...${NC}"
WIN_DIR="$SCRIPT_DIR/../GenericAgent_${VERSION}_win"
rm -rf "$WIN_DIR"
cp -R "$BASE_EXPORT_DIR" "$WIN_DIR"

# Windows 不需要 .command 文件
rm -f "$WIN_DIR/start.command"

# 创建 README.txt（Windows 用）
cat > "$WIN_DIR/README.txt" <<'WINTXT'
╔═══════════════════════════════════════════════════════════════╗
║   GenericAgent v0.6.0 — Windows 使用说明                     ║
╚═══════════════════════════════════════════════════════════════╝

【一键启动】
  双击  start.bat  即可启动。

【首次运行】
  脚本会自动:
    1. 检测 Python 环境，如未安装则自动下载安装
    2. 创建虚拟环境并安装依赖包
    3. 生成 mykey.py 配置模板

  启动后，用记事本打开 mykey.py，填入你的 API Key，
  然后再次双击 start.bat 即可正常使用。

【获取 API Key】
  推荐平台 (任选其一):
    • DeepSeek: https://platform.deepseek.com
    • MiniMax:  https://platform.minimaxi.com

  注册后在平台后台找到「API Key」，复制粘贴到 mykey.py 中
  对应配置的 'apikey' 字段。

【浏览器访问】
  启动后浏览器会自动打开 http://localhost:18600

【注意事项】
  • Windows 10/11 系统
  • 需要联网（安装依赖和调用 API）
  • 杀毒软件可能误报，请允许运行
WINTXT

WIN_ZIP="$OUTPUT_DIR/GenericAgent_${VERSION}_win.zip"
rm -f "$WIN_ZIP"
cd "$WIN_DIR/.."; zip -rq "$WIN_ZIP" "$(basename "$WIN_DIR")"; cd "$SCRIPT_DIR"
rm -rf "$WIN_DIR"

WIN_SIZE=$(du -h "$WIN_ZIP" | cut -f1)

# ═══════════════ macOS 版本 ═══════════════
echo -e "${YELLOW}📦 生成 macOS 版本...${NC}"
MAC_DIR="$SCRIPT_DIR/../GenericAgent_${VERSION}_mac"
rm -rf "$MAC_DIR"
cp -R "$BASE_EXPORT_DIR" "$MAC_DIR"

# macOS 不需要 .bat 文件；确保 .command 可执行
chmod +x "$MAC_DIR/start.command"

MAC_ZIP="$OUTPUT_DIR/GenericAgent_${VERSION}_mac.zip"
rm -f "$MAC_ZIP"
cd "$MAC_DIR/.."; zip -rq "$MAC_ZIP" "$(basename "$MAC_DIR")"; cd "$SCRIPT_DIR"
rm -rf "$MAC_DIR"

MAC_SIZE=$(du -h "$MAC_ZIP" | cut -f1)

# ── 清理 ──
rm -rf "$BASE_EXPORT_DIR"

# ═══════════════ 完成 ═══════════════
echo ""
echo -e "${GREEN}✅ 导出完成！${NC}"
echo ""
echo -e "  🪟 Windows: ${CYAN}${WIN_ZIP}${NC}  (${WIN_SIZE})"
echo -e "  🍎 macOS:   ${CYAN}${MAC_ZIP}${NC}  (${MAC_SIZE})"
echo ""
echo -e "${YELLOW}使用方法：${NC}"
echo ""
echo -e "  Windows 用户："
echo "    1. 解压 GenericAgent_v0.6.0_win.zip"
echo "    2. 双击 start.bat"
echo "    3. 首次运行会自动安装 Python 并配置"
echo ""
echo -e "  macOS 用户："
echo "    1. 解压 GenericAgent_v0.6.0_mac.zip"
echo "    2. 双击 start.command"
echo "    3. 首次运行会自动安装依赖并配置"
echo ""
