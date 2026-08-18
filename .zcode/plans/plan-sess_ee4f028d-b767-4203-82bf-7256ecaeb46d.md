# Agent 聊天历史记录功能实现方案

已确认的设计决策：LLM 自动生成会话标题、历史按当前服务器隔离、恢复会话时还原完整上下文可继续对话。

## 数据流总览

- 前端每个终端 tab 新增 `agentChatId`；首轮消息发送时生成 uuid
- 每轮对话结束（含出错/停止，挂在 `runAgentChat` 的 finally 中）自动调 `PUT /api/agent/chats` 保存：前端上传界面消息（`tab.agentChat`，含过程块），后端同时从内存 `AgentRegistry` 快照 LLM 上下文消息（OpenAI 格式，不含 system，每轮重建所以可整体替换）
- 点"+"新建对话：现有逻辑不变（清空+reset），仅追加 `tab.agentChatId=null`；旧会话因每轮自动保存已进入历史，天然满足"新建聊天保存会话"，且崩溃/重启也不丢已完成的轮次
- 恢复：调 restore 端点，后端把存的上下文写回 `AgentRegistry`（当前 session_id），前端用存的 display 条目整体替换 `tab.agentChat` 后重绘（`renderAgentChat` 已支持渲染 `process`/`message` 两种条目，直接复用）
- 标题：首次保存后由后端守护线程调当前模型生成（≤16 字中文标题），失败则列表展示首条用户消息前 20 字作为回退

## 文件改动

### 1. `static/icons/history.svg`（新增）
复制用户上传的 SVG，保留 viewBox 24（mask 用 alpha 通道，原 `#1C274C` 填充可直接用）。

### 2. `app/database.py`
SCHEMA 追加（`CREATE TABLE IF NOT EXISTS`，启动自动建表，无需迁移）：
```sql
CREATE TABLE IF NOT EXISTS agent_chats (
    id TEXT PRIMARY KEY,               -- 前端生成的 uuid
    server_id INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL DEFAULT '',    -- LLM 生成，空=未生成（列表用消息预览回退）
    model TEXT NOT NULL DEFAULT '',    -- 保存时的当前模型
    display TEXT NOT NULL DEFAULT '[]',  -- 前端 tab.agentChat 序列化（消息+过程块）
    context TEXT NOT NULL DEFAULT '[]',  -- 后端 LLM 消息上下文
    created_at TEXT NOT NULL,          -- Python 本地时间字符串（避免 SQLite CURRENT_TIMESTAMP 的 UTC 偏移）
    updated_at TEXT NOT NULL
);
```

### 3. `app/agent.py`
`AgentRegistry` 新增两个方法：
- `messages(session_id)` → 返回该会话消息的深拷贝（无则 None）
- `restore(session_id, messages)` → 整体替换会话消息（`conversation.messages` 不含 system，每轮由 `chat()` 重建，见 agent.py:728-731，直接替换安全；DeepSeek 的 `reasoning_content` 字段随消息一起存取，保持兼容）

### 4. `app/schemas.py`
- `AgentChatSaveBody {chat_id: str, session_id: str, server_id: int = 0, display: list = []}`
- `AgentChatRestoreBody {session_id: str}`

### 5. `app/main.py` 新增 4 个端点（跟随现有单文件路由模式）
- `GET /api/agent/chats?server_id=N` → 按 updated_at 倒序返回 `{id, title, model, created_at, updated_at}`（不含 display/context 大字段；title 为空时解析 display JSON 取首条用户消息前 20 字作预览）
- `PUT /api/agent/chats` → upsert：存 display、从 `agents.messages(session_id)` 快照 context、记录当前模型；序列化超 4MB 返回 413；新行 title 为空则启动守护线程生成标题
- `POST /api/agent/chats/{chat_id}/restore` → 校验 session 存在（404），`agents.restore(...)`，返回 `{title, model, display}`
- `DELETE /api/agent/chats/{chat_id}`

标题生成守护线程（main.py 内辅助函数）：用 `openai_request()`（agent.py:50，非流式）+ 当前配置发一次请求，system 提示要求输出不超过 16 字的简短中文标题、不带引号；取首行、去引号、截 30 字后更新行；任何失败静默保留空 title（列表走预览回退）。

### 6. `app/backup.py`
TABLES 字典追加 `agent_chats` 表，纳入备份/恢复。

### 7. `static/index.html`
- `index.html:43-45` `.agent-panel-actions` 内、`#agent-new-chat` **左侧**插入：
  `<button id="agent-history" class="icon-btn" type="button" title="历史会话"><span class="agent-history-icon"></span></button>`
- 参照 `#app-prompt-dialog` 结构（dialog-title + 关闭按钮）新增 `<dialog id="agent-history-dialog">`：标题"历史会话"、滚动列表容器 `#agent-history-list`、空状态提示

### 8. `static/app.css`
- `.agent-history-icon`：仿 `.side-tab-agent-icon` 的 CSS mask 引用 `/static/icons/history.svg`
- 历史列表样式：行布局（标题+模型徽标+创建时间 `YYYY-MM-DD HH:mm`+悬停显示的删除按钮）、当前会话徽标、空状态、列表 max-height 滚动

### 9. `static/app.js`
- `newTerminal`（:566）tab 对象增加 `agentChatId:null`
- `runAgentChat`（:309-313）：开头 `tab.agentChatId||=crypto.randomUUID()`；finally 中调用 `saveAgentChat(tab)`（fire-and-forget，失败 toast）
- 新建对话（:330）：追加 `tab.agentChatId=null`
- 新增历史按钮处理：无 tab 或 `agentBusy` 时禁用（在 `renderAgentChat` 中统一维护 disabled 状态）；打开弹窗时 `GET /api/agent/chats?server_id=` 渲染列表
- 行点击恢复：未连接则 toast「请先连接终端」；调 restore 端点 → 替换 `tab.agentChat`/`agentChatId`、清 `agentPendingContext`/审批状态 → `renderAgentChat()` → 关闭弹窗；当前活动会话显示徽标，点击仅关闭弹窗
- 删除按钮：`themedConfirm` 确认（现有通用弹窗）→ DELETE → 移除行；若删除的是当前活动会话则置 `tab.agentChatId=null`，后续轮次另存新行

## 测试与验证
- `tests/test_core.py` 补充：agent_chats 表建表、save/list/delete/restore 端点、标题回退逻辑（mock `openai_request`）
- 手动验证流程：连接服务器 → 对话一轮 → sqlite 出现记录且标题自动生成 → 点"+"新建 → 点历史按钮弹出列表（含创建时间）→ 恢复后追问"我刚才说了什么"验证上下文 → 删除会话 → 重启应用历史仍在

## 边界情况处理
- vault 锁定/未配置模型：保存降级（model 存空串、跳过标题生成），不阻塞保存
- 同服务器多 tab：同一 chat 行最后写入者生效（可接受）
- 恢复的消息列表若以 tool 消息开头：`chat()` 已有防御（agent.py:729）自动剔除
- 删除当前活动会话后继续聊天：另起新 chat 行