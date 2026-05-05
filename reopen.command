#!/bin/bash
# GenericAgent — 重新打开 Web UI（双击此文件即可）
open "http://localhost:18600" 2>/dev/null || true
echo "✅ 已打开浏览器 http://localhost:18600"
echo "如果无法访问，请先运行 start.command 启动服务。"
