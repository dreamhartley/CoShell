# CoShell

面向个人本机或可信内网的多标签桌面 SSH/SFTP 客户端。后端基于 FastAPI 与 Paramiko，桌面窗口使用 pywebview，前端使用原生 JavaScript 与本地 xterm.js。

## 功能

- 多标签 SSH 会话，支持排序、重命名、断开后恢复
- 密码与 OpenSSH 私钥认证，首次连接主机指纹确认
- SFTP 浏览、分块上传、拖拽上传、下载、新建、移动、复制、重命名和递归删除
- 内置 CodeMirror 文本编辑器，支持语法高亮、自动缩进、括号匹配与 `Ctrl+S` 原子保存
- 保存的服务器与可分组的快捷命令/脚本
- Argon2id + AES-GCM 本地凭据保险库
- OpenAI 兼容的终端 Agent，可自动获取模型并通过 SSH 多步执行命令
- 零配置内置 SearXNG 搜索后端，支持多搜索引擎聚合与网页读取
- 明亮/黑暗主题，终端配色即时同步
- 设置内一键备份/还原连接、快捷命令、主题、加密凭据及 Agent/MCP 配置

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
