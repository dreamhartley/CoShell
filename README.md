<p align="center">
  <img src="assets/app-icon.png" width="180" alt="CoShell 项目图标">
</p>

<h1 align="center">CoShell</h1>

<p align="center">
  <strong>安全、现代且可扩展的桌面 SSH / SFTP 工作台</strong>
</p>

<p align="center">
  将远程终端、文件管理、文本编辑与 AI Agent 汇集在一个本地优先的桌面应用中。
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FastAPI-0.116-009688?logo=fastapi&logoColor=white" alt="FastAPI 0.116">
  <img src="https://img.shields.io/badge/Paramiko-3.5-2C2D72" alt="Paramiko 3.5">
  <img src="https://img.shields.io/badge/pywebview-6.1-4B8BBE" alt="pywebview 6.1">
  <img src="https://img.shields.io/badge/xterm.js-5.3-000000?logo=gnometerminal&logoColor=white" alt="xterm.js 5.3">
  <img src="https://img.shields.io/badge/CodeMirror-5.65-D30707" alt="CodeMirror 5.65">
  <img src="https://img.shields.io/badge/SearXNG-bundled-3050FF?logo=searxng&logoColor=white" alt="Bundled SearXNG">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License">
</p>

<p align="center">
  <a href="#功能亮点">功能亮点</a> ·
  <a href="#技术栈">技术栈</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#数据与安全">数据与安全</a> ·
  <a href="#agent">Agent</a> ·
  <a href="#打包发布">打包发布</a>
</p>

---

CoShell 面向个人电脑与可信内网环境，提供多标签 SSH 会话、完整 SFTP 文件操作、本地凭据保险库和具备远程执行能力的 AI Agent。应用默认仅监听本机回环地址，核心前端依赖随程序分发，日常使用无需浏览器、Docker 或外部 CDN。

## 功能亮点

| 模块 | 能力 |
| --- | --- |
| **远程终端** | 多标签 SSH 会话、标签排序与重命名、断开恢复、自适应终端尺寸 |
| **文件工作区** | SFTP 浏览、分块与拖拽上传、下载、新建、移动、复制、重命名及递归删除 |
| **在线编辑** | 内置 CodeMirror，支持语法高亮、自动缩进、括号匹配与 `Ctrl+S` 原子保存 |
| **连接管理** | 保存服务器、复用 SSH 密钥、快捷命令与脚本、首次连接主机指纹确认 |
| **安全存储** | Argon2id 密钥派生、AES-GCM 凭据加密、Windows DPAPI 可选自动解锁 |
| **AI Agent** | OpenAI 兼容 API、多轮远程任务、命令风险审批、workspace / SFTP / MCP 工具 |
| **本地搜索** | 随附 SearXNG sidecar，聚合多搜索引擎并支持网页内容读取 |
| **桌面体验** | 原生桌面窗口、明暗主题、终端配色同步、配置备份与还原 |

## 技术栈

| 层级 | 主要技术 | 用途 |
| --- | --- | --- |
| **桌面容器** | pywebview | 提供轻量原生窗口与桌面生命周期管理 |
| **应用后端** | Python · FastAPI · Uvicorn | 本地 API、WebSocket 会话和业务编排 |
| **远程连接** | Paramiko | SSH 终端、主机密钥校验与 SFTP 操作 |
| **前端界面** | HTML · CSS · Vanilla JavaScript | 无构建步骤的本地优先界面 |
| **终端与编辑器** | xterm.js · CodeMirror | 终端渲染、尺寸适配与代码编辑 |
| **安全组件** | cryptography · Argon2id · AES-GCM · DPAPI | 凭据加密、密钥派生与设备绑定 |
| **Agent 与搜索** | OpenAI-compatible API · MCP · SearXNG | AI 任务执行、工具扩展与本地聚合搜索 |

```text
pywebview Desktop
       │
       ├── FastAPI / WebSocket ── Paramiko ── SSH & SFTP servers
       │
       ├── Local encrypted vault ── Argon2id + AES-GCM
       │
       └── AI Agent ── OpenAI-compatible API / MCP / SearXNG
```

## 快速开始

需要 Python 3.11 或更高版本。双击 `start-gui.bat`，或在 PowerShell 中运行：

```powershell
.\start.ps1
```

依赖安装完成后会直接弹出桌面窗口，无需打开浏览器。首次使用需创建至少 8 位的保险库主密码，该密码不写入磁盘且无法找回。

也可手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Linux/macOS 使用对应的 `.venv/bin/python` 命令即可。如需纯 Web 调试模式，运行 `python run.py --web` 后访问 <http://127.0.0.1:8765>。

## 打包发布

```powershell
.\build.ps1
```

完成后运行 `dist\CoShell\CoShell.exe`，目标电脑无需单独安装 Python。打包版数据保存在 EXE 同目录的 `data` 文件夹中；移动程序时请将该文件夹一并移动，升级或替换程序时请保留它。

## 数据与安全

- 桌面应用仅在 `127.0.0.1` 随机端口监听；源码运行时数据保存在 `data/webssh.db`。
- Agent 的本机工作目录位于应用数据目录中的 `workspace`：源码运行时为 `data/workspace`，打包版为 EXE 同目录下的 `data/workspace`。每台已保存服务器使用稳定的 `server-<ID>` 独立子目录，临时连接按连接身份隔离；Agent 无法通过本地工具越过当前服务器的目录。删除服务器时可选择一并删除对应 workspace 文件。
- 密码、私钥与私钥口令仅以 AES-GCM 密文保存，密钥由主密码经 Argon2id 派生。启用"安全记住"后，主密码由 Windows DPAPI 绑定当前设备与用户加密保存，重启时自动解锁。
- 首次连接必须确认服务器 SHA-256 指纹；保存后的指纹发生变化时拒绝连接。
- 锁定保险库不会中断已建立的会话，但无法使用保存的凭据发起新连接。
- JSON 备份中的敏感值保持主密码加密，并排除 Windows 设备绑定的自动解锁信息；还原后需使用创建备份时的主密码解锁。
- 本项目面向单用户本地使用。若通过反向代理开放访问，必须另行部署 HTTPS、用户认证、访问控制、限流与审计。

## SSH 密钥库

在设置 → 密码库中可从本地文件导入 OpenSSH、RSA、ECDSA 等私钥。应用仅保存加密后的私钥与口令；新建连接时可从下拉框选择已导入的密钥，多个连接可复用同一密钥。

## Agent

在设置 → Agent 中填写 OpenAI 兼容 API 地址（例如 `https://api.openai.com/v1`）、API 密钥并获取/选择模型。API 密钥与 SSH 凭据一样加密保存。

连接 SSH 后，左侧边栏的 Agent 聊天是完整任务的主要入口，适合部署、配置、联网查询、workspace/SFTP 文件处理和持续多轮沟通。聊天过程与终端输入相互独立；Agent 的命令及输出显示在聊天过程区域，不会写入交互终端。任务处理中发送按钮会切换为停止按钮；标题栏可新建对话，或将终端选中内容/上一条命令输出附加到上下文。

输入框左下角可在“请求批准”与“完全访问”之间切换。默认的请求批准模式会在 Agent 执行删除、格式化、重启、卸载、清理数据等高风险命令前暂停任务，并在输入框上方显示完整命令与“是/否”按钮；完全访问模式不再请求授权。模式仅在当前应用会话中保留，重新打开应用后恢复为请求批准。

终端中的 `/agent` 专门用于处理刚刚出现的报错。它会优先使用终端选中内容，否则附带上一条命令及其输出，并自动隐藏常见密钥、Token 和密码：

```text
/agent 帮我解决这个报错
/agent --explain 这个错误是什么原因
/agent --no-context 检查 nginx 为什么无法启动
/agent 继续，修改后还是启动失败
/agent mode
/agent mode approval
/agent mode full
/agent clear
```

`/agent` 与左侧聊天的会话、执行状态、授权请求和权限模式完全隔离。普通 `/agent` 请求会创建新的临时故障事件；`/agent 继续` 只延续最近一次终端故障；`/agent clear` 清除该临时上下文。`--explain` 为只读解释模式，`--no-context` 不发送终端输出。处理过程中按 `Ctrl+C` 可停止任务。

终端 `/agent` 遇到高风险命令时，会直接在当前终端显示风险原因和完整命令，输入“是”或“否”决定是否执行。`/agent mode` 查看当前终端的模式，`/agent mode approval` 切换到请求批准，`/agent mode full` 切换到完全访问。模式按终端独立保存，不会改变左侧 Agent 的模式。

输入 `/agent `（末尾包含空格）时，终端会在当前输入行附近显示所有子命令。可用鼠标选择，或使用上下方向键并按 `Enter`/`Tab` 填充；继续输入其他内容、按 `Esc` 或退格时会自动隐藏。

终端快速处置最多 12 轮、单条命令最长 5 分钟，重点是检查、最小化修复和验证；超出上限时会在当前终端总结，可用 `/agent 继续` 接着处理。左侧聊天仍使用最多 30 轮、单条命令最长 15 分钟的完整 Agent，并保留独立的多轮上下文。

每次请求的系统提示会包含当前本地日期和时区。左侧 Agent 可通过 `workspace_list`、`workspace_read`、`workspace_write` 管理 `workspace` 中的 UTF-8 文本文件，并通过 `sftp_transfer` 在 `workspace` 与当前 SSH 主机之间上传或下载单个文件。本地路径必须相对于 `workspace`；文本工具限制为 256 KiB，SFTP 单文件限制为 512 MiB，覆盖已有文件必须显式确认。终端 `/agent` 不开放本地 workspace、SFTP 和 MCP 工具。

内置联网工具 `web_search` 与 `web_fetch` 可在设置中单独关闭，亦可安装提供搜索工具的 HTTP MCP 服务。安装 MCP 时会读取 `tools/list`，仅将名称或描述包含 `search` 的工具提供给 Agent。

应用启动时会在 `127.0.0.1` 随机端口自动启动随附的 SearXNG sidecar，Agent 默认通过其 JSON API 聚合 Brave、Startpage 与 DuckDuckGo，无需安装 Docker 或填写搜索服务地址；应用退出时 sidecar 会一并关闭。`web_fetch` 仅支持公开 HTTP/HTTPS 内容，拒绝字面形式的本机、内网和保留地址；检测到 Clash/Mihomo 等 TUN 的标准 Fake-IP 环境时，会兼容由代理映射的公网域名，但不会放行直接输入的 Fake-IP 地址。

每条工具命令均在独立 shell 中执行，工作目录与环境变量不会跨命令保留，需在同一条命令中设置。Agent 具备直接操作服务器的能力，使用高权限账号时请仔细描述目标。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

## 第三方组件

`static/vendor` 包含 xterm.js 5.3.0、FitAddon 0.8.0、CodeMirror 5.65.16 及其 MIT 许可证，运行时不访问 CDN。

`third_party/searxng` 包含固定版本的 SearXNG 最小运行时快照及完整对应源码，作为独立的本机 sidecar 运行。其上游版本、修改说明和许可证见该目录中的 `BUNDLED_VERSION.txt` 与 `LICENSE`。

## 许可证

主程序采用 MIT 许可证，详见 [LICENSE](LICENSE)。随附的 SearXNG sidecar 采用 AGPL-3.0-or-later，详见 [third_party/searxng/LICENSE](third_party/searxng/LICENSE)。
