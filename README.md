# GenericAgent-v1-web

多模型群聊 AI Agent 协作平台，带 Web UI。

## 功能

- **多模型群聊**: 多个 AI 同时对话，互相监工
- **自我进化**: 完成任务后自动沉淀为 Skill，越用越强
- **Web UI**: 现代化网页界面，多会话管理
- **打断回复**: 随时中断 AI 输出
- **多平台接入**: 支持微信 / QQ / 飞书 / 钉钉 / Telegram

## 快速开始

```bash
# 1. 安装依赖
pip install requests streamlit pywebview

# 2. 配置 API Key
cp mykey_template.py mykey.py
# 编辑 mykey.py，填入你的 LLM API Key

# 3. 启动
python launch.pyw
```

## 项目结构

```
GenericAgent-v1-web/
├── frontends/         # Web UI 前端
├── plugins/           # 插件
├── memory/            # 记忆系统
├── agent_loop.py      # Agent 核心循环
├── llmcore.py         # LLM 调用核心
├── launch.pyw         # 启动入口
└── mykey_template.py  # API Key 配置模板
```

## 技术栈

- Python 3.10+
- Streamlit / pywebview
- 支持 Claude、GPT、Kimi、MiniMax 等主流模型

## 许可

MIT License
