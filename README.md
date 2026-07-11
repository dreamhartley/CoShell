# 轻量 SSH Terminal

一个面向个人本机或可信内网的多标签桌面 SSH/SFTP 客户端。后端使用 FastAPI 与 Paramiko，桌面窗口使用 pywebview，前端使用原生 JavaScript 和本地 xterm.js。

## 功能

- 多 SSH 标签独立连接、排序、重命名、恢复和关闭
- 密码与 OpenSSH 私钥认证，首次连接主机指纹确认
- SFTP 浏览、分块上传、拖拽上传、下载、新建、移动、重命名、复制和递归删除
- 内置文本编辑器，支持语法高亮、自动缩进、括号匹配和 Ctrl+S 原子保存
- 保存的服务器和可分组快捷命令/脚本
- Argon2id + AES-GCM 本地凭据保险库
- OpenAI API 兼容的终端 Agent，可自动获取模型并通过 SSH 多步执行命令
- 明亮/黑暗主题，以及终端配色即时同步

## 快速开始（Windows）

需要 Python 3.11 或更高版本。双击 `start-gui.bat`，或在 PowerShell 中运行：

```powershell
.\start.ps1
```

依赖安装完成后会直接弹出桌面窗口，不需要打开浏览器。首次使用请创建至少 8 位的保险库主密码；该密码不会写入磁盘，也无法找回。

也可以手动启动：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Linux/macOS 使用对应的 `.venv/bin/python` 命令即可。

如需以前的纯 Web 调试方式，可运行 `python run.py --web`，再访问 <http://127.0.0.1:8765>。

## 打包 Windows 程序

在 PowerShell 中运行：

```powershell
.\build.ps1
```

完成后可直接运行 `dist\LightSSHTerminal\LightSSHTerminal.exe`，目标电脑无需单独安装 Python。打包版的数据保存在 `%LOCALAPPDATA%\LightSSHTerminal`，升级或替换程序不会丢失配置。

## 数据与安全

- 桌面应用只在 `127.0.0.1` 的随机空闲端口监听；源码运行时数据保存在 `data/webssh.db`。
- 密码、私钥和私钥口令仅以 AES-GCM 密文保存；密钥由主密码经 Argon2id 派生。启用“安全记住”后，主密码由 Windows DPAPI 绑定当前设备和 Windows 用户加密保存，应用重启时自动解锁。
- 首次连接必须确认服务器 SHA-256 指纹。保存后的指纹发生变化时应用会拒绝连接。
- 锁定保险库不会中断已建立的 SSH 会话，但无法使用保存的凭据发起新连接。
- 默认在当前 Windows 用户和设备上安全记住保险库主密码，关闭并重新打开应用、页面刷新或 SSH 重连时都会自动解锁；手动点击“锁定”会清除自动解锁凭据。将程序或数据库复制到另一台电脑、或更换 Windows 用户后，设备绑定凭据无法解密，保险库会保持锁定。
- 本项目不是公网多用户服务。若通过反向代理开放访问，必须另外部署 HTTPS、用户认证、访问控制、限流和审计。

## SFTP 操作

双击目录进入，双击文件在线编辑；需要下载时可使用文件右键菜单。文件和目录的网页右键菜单支持编辑、下载、复制、粘贴、重命名、递归删除及复制远程路径；列表空白处右键可以新建文件、上传、新建目录、粘贴或刷新。

双击文本文件会在内置 CodeMirror 编辑器中打开。编辑器支持常见代码格式的语法高亮、行号、智能缩进、括号补全与匹配，并可使用 `Ctrl+S`/`Cmd+S` 保存。保存时先写入远端同目录临时文件，再替换原文件并保留权限；如果远端文件在编辑期间发生变化，会在覆盖前提示。在线编辑限制为 UTF-8 文本且不超过 5 MiB。

SSH 断开后，终端会显示本地 `local>` 提示符。输入 `/reconnect` 并回车即可在原标签中重连；保存的服务器会直接复用配置，临时连接则在原标签内重新填写连接信息。

## Agent

点击右上角“设置”，先在“密码库”标签初始化或解锁保险库，再到“Agent”标签填写 OpenAI 兼容 API 的基础地址（例如 `https://api.openai.com/v1`）、API 密钥并获取/选择模型。API 密钥与 SSH 凭据一样加密保存在本地数据库中。

“密码库”标签也可以从本地文件导入 OpenSSH、RSA、ECDSA 等私钥。应用只保存加密后的私钥和口令；新建 SSH 连接时可直接从“密码库密钥”下拉框选择，多个连接可以复用同一密钥。

Agent 设置可单独关闭内置联网工具，也可安装提供搜索工具的 HTTP MCP 服务。内置联网工具包含 `web_search` 和 `web_fetch`：前者查找公开网页，后者读取网页标题、正文和链接，并可沿返回链接继续访问。网页读取仅支持公开的 HTTP/HTTPS 文本、HTML 和 JSON 内容，限制单页 2 MiB，并拒绝本机、内网及保留地址。安装 MCP 时会连接 MCP 端点并读取 `tools/list`，仅将名称或描述包含 `search` 的工具提供给 Agent；每个 MCP 服务均可独立启停、刷新工具列表或卸载。鉴权令牌会通过密码库加密保存。

连接 SSH 后，在当前终端输入自然语言任务：

```text
/agent 帮我在 VPS 上安装 ufw 并放行 22、443 端口
/agent 把 https://github.com/example/project 部署到这台服务器，并按项目文档完成配置和启动验证
```

Agent 会先读取你提供的 GitHub 项目页、README 或安装文档，并可继续打开页面中的文档链接；随后在当前 SSH 连接上通过独立的非交互命令通道检查环境、执行部署并验证结果。单次任务最多执行 30 轮，单条安装命令最长可运行 15 分钟。达到上限时会返回已完成步骤与当前状态，可输入 `/agent 继续刚才的任务` 沿用上下文继续处理，不会直接丢失执行结果。SSH 断开后上下文自动清除。每条工具命令都是独立 shell，涉及工作目录或环境变量时应在同一条命令中设置。Agent 具备直接操作服务器的能力，使用高权限账号时请仔细描述目标。

`/agent` 也可以用于普通问答。Agent 会直接以 `Agent:` 前缀在终端输出答案，不会为普通知识问题执行 SSH 命令；遇到最新信息、外部文档或需要事实核查的问题时，可以调用在线搜索并在回答中附上来源链接。Agent 回答中的 Markdown 会转换成终端样式显示（标题、粗斜体、代码、列表、引用和链接），普通 SSH 输出不会经过 Markdown 渲染。

选择或拖入文件后，文件名会立即显示在列表中。右侧环状进度按 1 MiB 分块实际写入 SFTP 的进度更新；完成远端写入队列校验后才会刷新为正式文件。上传失败或 SSH 断开时会清理未完成的远端文件。

远端目录递归复制通过 SFTP 完成，大目录可能耗时较长。移动使用服务器的 SFTP rename，服务器不支持跨文件系统移动时会显示错误。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

## 第三方前端组件

`static/vendor` 中包含 xterm.js 5.3.0、FitAddon 0.8.0、CodeMirror 5.65.16 及其 MIT 许可证。运行时不访问 CDN。
