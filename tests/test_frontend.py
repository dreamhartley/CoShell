from pathlib import Path
import re

import quickjs


def test_app_javascript_parses():
    source = Path("static/app.js").read_text(encoding="utf-8")
    # Compiles the complete browser script without executing DOM-dependent code.
    quickjs.Context().eval(f"new Function({source!r})")


def test_theme_picker_exposes_all_themes_and_removes_quick_toggle():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")

    themes = ("dark", "light", "fresh", "ocean", "midnight")
    assert 'id="theme-btn"' not in html
    assert 'data-settings-panel="theme"' in html
    for theme in themes:
        assert f'data-theme-choice="{theme}"' in html
        assert f"{theme}:{{background:" in javascript
    for theme in themes[1:]:
        assert f':root[data-theme="{theme}"]' in css


def test_settings_exposes_backup_and_restore_controls():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert 'data-settings-panel="backup"' in html
    assert 'id="backup-download"' in html
    assert 'id="backup-restore"' in html
    assert "'/api/backup'" in javascript
    assert "'/api/restore'" in javascript
    assert "themedConfirm('还原会整体替换" in javascript


def test_frontend_uses_themed_prompts_instead_of_browser_dialogs():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert 'id="app-prompt-dialog"' in html
    assert "themedConfirm" in javascript
    assert "themedInput" in javascript
    assert not re.search(r"\b(?:alert|confirm|prompt)\s*\(", javascript)
    assert "beforeunload" not in javascript


def test_server_deletion_offers_workspace_cleanup():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "对应 workspace 中的所有文件" in javascript
    assert "delete_workspace=${deleteWorkspace}" in javascript


def test_terminal_has_custom_context_menu_actions():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "showTerminalMenu(event,tab)" in javascript
    for label in ("复制", "粘贴", "Agent 命令", "全选终端内容", "清空终端显示", "断开连接", "重新连接"):
        assert f"label:'{label}'" in javascript
    assert "sendTerminalInput(tab,'/agent ')" in javascript


def test_local_terminal_prompt_is_single_and_pinned_to_bottom():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "if(tab.localPromptShown)return" in javascript
    assert "writeAtTerminalBottom" in javascript
    assert "tab.term.scrollToBottom()" in javascript
    assert "\\r\\x1b[2K" in javascript
    assert "m.type==='output'&&!tab.localPromptShown" in javascript


def test_agent_sidebar_has_streaming_chat_and_keeps_answers_out_of_terminal():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")

    assert 'data-panel="agent"' in html
    assert 'id="agent-chat-form"' in html
    assert "'/api/agent/chat/stream'" in javascript
    assert "event.type==='command_output'" in javascript
    assert "event.type==='answer'" in javascript
    assert "event.type==='answer')tab.term" not in javascript
    assert "event.type==='answer_delta'" in javascript
    assert "process.currentText.text+=event.delta" in javascript
    assert "renderAgentChatMarkdown(entry.text)" in javascript


def test_terminal_agent_command_sends_recent_terminal_context_only_on_terminal_route():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "function recentTerminalContext(tab,command='')" in javascript
    assert "terminal_context:terminalContext||null" in javascript
    assert "runAgent(tab,captured.slice(6).trim(),context)" in javascript
    assert "body:JSON.stringify({session_id:tab.sessionId,message:message.trim()})" in javascript
    assert "agent-message-role" not in javascript
    assert "正在执行命令：" in javascript
    assert "agent-command" in css
    assert "text-overflow:ellipsis" in css
    assert ".agent-message,.agent-activity{flex-shrink:0}" in css
    assert "-webkit-user-select:text;user-select:text" in css
    assert "复制选中内容" in javascript
    assert "复制整条消息" in javascript
    assert "function renderAgentProcess" in javascript
    assert "agent-process-toggle" in css
    assert "process?.items.push(tab.agentActivity)" in javascript
    assert "process.currentText=null" in javascript
    assert "process.completedAt=Date.now();process.open=false" in javascript
    assert "chevron.setAttribute('aria-hidden','true')" in javascript
    assert '.agent-process-toggle[aria-expanded="true"] .agent-process-chevron{transform:rotate(90deg)}' in css
    assert "transform-origin:9px 9px" in css
    assert "event.type==='thinking_start'" in javascript
    assert "text:'正在思考'" in javascript
    assert "event.type==='local_tool_start'" in javascript
    assert "event.type==='local_tool_end'" in javascript
    assert "正在${action}" in javascript
    assert "item.status=event.success?'done':'failed'" in javascript
    assert "reasoning_content" not in javascript


def test_streaming_markdown_parser_advances_on_incomplete_prefixes():
    source = Path("static/app.js").read_text(encoding="utf-8")
    markdown_functions = source[source.index("function appendAgentMarkdownInline"):source.index("function renderAgentChat(){")]
    dom_stub = """
    const document={
      createElement(tag){return {tag,childNodes:[],dataset:{},append(...items){this.childNodes.push(...items)}}},
      createTextNode(text){return {text}}
    };
    """
    context = quickjs.Context()
    context.set_time_limit(1)
    result = context.eval(dom_stub + markdown_functions + "JSON.stringify(['- ','1. ','# ','* '].map(value=>renderAgentChatMarkdown(value).childNodes.length))")
    assert result == "[1,1,1,1]"
