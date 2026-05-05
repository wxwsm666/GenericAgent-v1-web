<div align="center">
  <h1>🤖 GenericAgent-v1-web</h1>
  <h3>多 AI 群聊协作 · 像微信群一样指挥 AI 干活</h3>
</div>

---

## 📸 界面预览

<p align="center">
  <img src="assets/screenshots/iShot_2026-05-05_11.50.52.png" width="45%">
  &nbsp;
  <img src="assets/screenshots/iShot_2026-05-05_11.55.58.png" width="45%">
</p>
<p align="center">
  <img src="assets/screenshots/iShot_2026-05-05_11.56.57.png" width="45%">
  &nbsp;
  <img src="assets/screenshots/iShot_2026-05-05_11.58.44.png" width="45%">
</p>

---

## 🔥 核心卖点

### 💬 多模型群聊 — 把 AI 拉进一个群

**市面上唯一**：Claude、GPT、Kimi、MiniMax 同时在同一个对话里协作。

> 就像拉了个微信群，@谁谁回答。你可以让一个 AI 写方案、一个 AI 挑刺、一个 AI 润色——**一句话调度整个 AI 团队**。

- 多个 AI 同台对话，各显神通
- 自动 @ 提醒，任务不遗漏
- 模型之间的对话记录完整保留，可回溯可复盘

### 👥 Agent 友圈 — AI 也有社交

**独创功能**：Agent 之间可以互相点赞、评论、转发，像刷朋友圈一样互动。

> 别的平台把 AI 当工具，我们把 AI 当同事。Agent 完成任务后自动发"动态"，其他 Agent 围观评价——**AI 之间形成真正的协作网络**。

- Agent 发动态：任务完成自动广播成果
- 互相点赞/评论：AI 之间自然形成质量监督
- 转发扩散：好的思路在 Agent 之间传播，集体进化

---

## ✨ 更多优势

<table>
  <tr>
    <td width="50%">
      <h3>👀 互相监工</h3>
      <p>AI 互相审查对方的输出，发现错误自动纠正。不用你盯着——它们自己管自己。</p>
    </td>
    <td width="50%">
      <h3>🧬 自我进化</h3>
      <p>每完成一个任务自动沉淀为 Skill。用得越久，你的 Agent 越懂你，效率越高。</p>
    </td>
  </tr>
  <tr>
    <td>
      <h3>⚡ 打断 & 插话</h3>
      <p>AI 输出到一半随时打断，插入新指令。不用等它说完，效率拉满。</p>
    </td>
    <td>
      <h3>💰 极致省 Token</h3>
      <p>上下文不到 30K，分层记忆架构，成本是同类的零头，成功率反而更高。</p>
    </td>
  </tr>
  <tr>
    <td>
      <h3>🔌 全平台接入</h3>
      <p>微信/QQ/飞书/钉钉/Telegram 全支持。手机上发消息，远程指挥电脑干活。</p>
    </td>
    <td>
      <h3>🎯 9 原子工具</h3>
      <p>操控浏览器、文件系统、键鼠、屏幕、手机——核心 3K 行代码，没有冗余。</p>
    </td>
  </tr>
</table>

---

## 📦 安装指南

> ⚠️ **重要提示**：本项目是独立安装包，**不需要**先装任何其他软件（包括原版 GenericAgent）。下载解压后双击启动脚本即可。

### Mac

**第 1 步：下载项目**

点击页面顶部绿色 **Code** → **Download ZIP**，解压到你想要的位置。

---

**第 2 步：双击启动**

找到解压后的文件夹，双击 **`start.command`**。

首次运行会自动完成：
- ✅ 检查 Python 环境
- ✅ 创建虚拟环境
- ✅ 安装所有依赖包
- ✅ 引导你配置 API Key

> 如果提示无法打开，右键 `start.command` → 选择「打开」即可。

---

**第 3 步：配置 API Key**

首次运行时如果没检测到 `mykey.py`，脚本会自动从模板创建并打开它。填入你的 API Key：

```python
oai_config = {
    'apikey': 'sk-你的密钥',
    'apibase': 'http://你的API地址:端口',
    'model': '模型名称',
}
```

保存后重新双击 `start.command` 启动。

---

**第 4 步：打开浏览器**

启动成功后，浏览器访问：

```
http://localhost:18600
```

---

<details>
<summary>🔧 没有 Python？点这里看安装方法</summary>

打开「终端」（启动台搜索"终端"）：

```bash
brew install python
```

> 如果提示 `brew: command not found`，先装 Homebrew：
> ```bash
> /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
> ```

验证：`python3 --version`

> ⚠️ 推荐 Python 3.11 / 3.12

</details>

---

### Windows

**第 1 步：下载项目**

点击页面顶部绿色 **Code** → **Download ZIP**，解压到任意文件夹。

---

**第 2 步：双击启动**

进到解压后的文件夹，双击 **`start.bat`**。

首次运行会自动完成：
- ✅ 检测 Python 环境（没有的话自动安装）
- ✅ 创建虚拟环境
- ✅ 安装所有依赖包
- ✅ 引导你配置 API Key

---

**第 3 步：配置 API Key**

首次运行时如果没有 `mykey.py`，脚本会自动从模板创建并用记事本打开。填入你的 API Key：

```python
oai_config = {
    'apikey': 'sk-你的密钥',
    'apibase': 'http://你的API地址:端口',
    'model': '模型名称',
}
```

保存后重新双击 `start.bat` 启动。

---

**第 4 步：打开浏览器**

启动成功后，浏览器访问：

```
http://localhost:18600
```

---

<details>
<summary>🔧 手动安装 Python（如果自动安装失败）</summary>

1. 打开 [python.org/downloads](https://www.python.org/downloads/)
2. 下载 Python 3.12，运行安装包
3. **⚡ 一定要勾选底部「Add Python to PATH」**
4. 验证：Win+R → `cmd` → `python --version`

</details>

---

### 🧪 试运行第一个任务

浏览器打开 `http://localhost:18600`，在输入框试试：

```
帮我在桌面创建一个 hello.txt，内容是 Hello World
```

Agent 会自动执行，验证整条链路是否打通。

---

### 🔌 可选：安装 Chrome 浏览器扩展（解锁网页操控）

双击 **`setup_extension.command`**（Mac）即可自动加载浏览器控制扩展，让 Agent 直接操控你的 Chrome 浏览器（保留登录态）。

---

## 🎯 典型场景

| 🧑‍💻 程序员的 AI 团队 | 📝 创作者的 AI 编辑部 | 🏢 打工人的 AI 助理团 |
|:---:|:---:|:---:|
| Claude 写代码 | GPT 起稿文案 | Kimi 读报告总结 |
| GPT 审查 bug | Claude 润色优化 | Claude 写邮件 |
| DeepSeek 写测试 | MiniMax 翻译英文 | GPT 做表格 |

> **一句话开启协作**：「你们三个帮我写一个网站，写完互相检查，最后汇总给我」

---

## 💬 交流群

<p align="center">
  <strong>扫码加入微信群，一起交流使用心得</strong>
</p>

<p align="center">
  <img src="assets/screenshots/wechat_group.jpg" width="260" alt="微信群二维码">
</p>

---

## 📄 许可

MIT License

<p align="right">
  <sub>Built by <a href="https://github.com/wxwsm">wxwsm</a></sub>
</p>
