from pathlib import Path
import re

import quickjs


def test_app_javascript_parses():
    source = Path("static/app.js").read_text(encoding="utf-8")
    # Compiles the complete browser script without executing DOM-dependent code.
    quickjs.Context().eval(f"new Function({source!r})")


def test_welcome_greeting_uses_local_time_periods_and_all_configured_copy():
    html = Path("static/index.html").read_text(encoding="utf-8")
    source = Path("static/app.js").read_text(encoding="utf-8")

    assert 'id="welcome-greeting"' in html
    assert "从主机库选择一台服务器，和 Agent 一起开始工作" not in html
    greetings_source = source[source.index("const WELCOME_GREETINGS="):source.index("function updateWelcomeGreeting")]
    expected = [
        "早呀~我已经在线，准备好陪你建立今天的第一条连接。",
        "早上好。咖啡可以慢慢来，终端这边我帮你守着。",
        "中午好。忙了一上午，记得给自己和服务器都喘口气。我在。",
        "中午好～远程的世界还在转，这里可以安静一点。想连哪边？",
        "嗨，下午好。还有主机要处理的话，我陪你；想歇一会儿也行。",
        "下午好。午后容易犯困，终端这边我帮你盯着。需要连机还是排查？",
        "嗨，傍晚好。想把今天的活儿收个尾，还是先歇会儿再说？",
        "傍晚了。忙了一天，终于能喘口气了吧？我在。",
        "嗨，晚上好。不着急下线的话，我陪你多待会儿。",
        "晚上好。夜色下来了，终端的光刚好。今天想要做什么？",
        "夜深了。终端可以继续连着，人不用，有情况我盯着。",
        "凌晨了。如果你还醒着，那我就陪你一起加班一会儿。",
    ]
    for greeting in expected:
        assert greeting in source

    context = quickjs.Context()
    values = context.eval(
        greetings_source
        + "JSON.stringify([4,5,10,11,13,14,17,18,19,20,23,0].map(hour=>"
        + "WELCOME_GREETINGS.filter(item=>hour>=item.start).slice(-1)[0]?.start??0))"
    )
    assert values == "[0,5,5,11,11,14,14,18,18,20,20,0]"


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


def test_settings_can_generate_and_optionally_import_ssh_key_pair():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert 'id="ssh-key-generate-toggle"' in html
    assert 'id="ssh-key-generate-form"' in html
    assert 'name="name" maxlength="100" placeholder="留空自动生成"' in html
    assert 'name="file_name" maxlength="100"' in html
    assert 'name="auto_import" type="checkbox" checked' in html
    assert 'value="ed25519"' in html and 'value="rsa"' in html
    assert "'/api/ssh-keys/generate'" in javascript


def test_frontend_uses_themed_prompts_instead_of_browser_dialogs():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert 'id="app-prompt-dialog"' in html
    assert "themedConfirm" in javascript
    assert "themedInput" in javascript
    assert not re.search(r"\b(?:alert|confirm|prompt)\s*\(", javascript)
    assert "beforeunload" not in javascript


def test_browser_autofill_is_disabled_for_forms_and_credentials():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    forms = re.findall(r"<form\b[^>]*>", html)
    assert forms
    assert all('autocomplete="off"' in form for form in forms)
    assert 'name="username" required autocomplete="off"' in html
    assert 'name="password" type="password" minlength="8" required autocomplete="new-password"' in html
    assert html.count('name="passphrase" type="password" autocomplete="new-password"') == 3
    assert "input.autocomplete=type==='password'?'new-password':'off'" in javascript


def test_server_deletion_offers_workspace_cleanup():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "对应 workspace 中的所有文件" in javascript
    assert "delete_workspace=${deleteWorkspace}" in javascript


def test_server_editor_switches_authentication_fields():
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    source = javascript[
        javascript.index("function updateServerEditorAuthFields"):
        javascript.index("async function loadShortcuts")
    ]

    assert "form.elements.auth_type.addEventListener('change'" in source
    assert "form.elements.ssh_key_id.addEventListener('change'" in source
    assert "input.closest('label').classList.toggle('hidden',!visible)" in source
    assert "input.disabled=!visible" in source
    assert "ssh_key_id:usesKey&&d.ssh_key_id?Number(d.ssh_key_id):null" in source

    context = quickjs.Context()
    result = context.eval(
        """
        const fields={};
        for(const name of ['password','ssh_key_id','private_key','passphrase']){
          fields[name]={
            value:'',
            disabled:false,
            hidden:false,
            closest(){return {classList:{toggle(_name,value){fields[name].hidden=value}}}}
          };
        }
        fields.auth_type={value:'private_key'};
        const form={elements:fields};
        """
        + source.split("function editServer", 1)[0]
        + """
        updateServerEditorAuthFields(form);
        const keyState=Object.fromEntries(
          ['password','ssh_key_id','private_key','passphrase'].map(name=>[
            name,{hidden:fields[name].hidden,disabled:fields[name].disabled}
          ])
        );
        fields.auth_type.value='password';
        updateServerEditorAuthFields(form);
        const passwordState=Object.fromEntries(
          ['password','ssh_key_id','private_key','passphrase'].map(name=>[
            name,{hidden:fields[name].hidden,disabled:fields[name].disabled}
          ])
        );
        JSON.stringify({keyState,passwordState});
        """
    )
    assert result == (
        '{"keyState":{"password":{"hidden":true,"disabled":true},'
        '"ssh_key_id":{"hidden":false,"disabled":false},'
        '"private_key":{"hidden":false,"disabled":false},'
        '"passphrase":{"hidden":false,"disabled":false}},'
        '"passwordState":{"password":{"hidden":false,"disabled":false},'
        '"ssh_key_id":{"hidden":true,"disabled":true},'
        '"private_key":{"hidden":true,"disabled":true},'
        '"passphrase":{"hidden":true,"disabled":true}}}'
    )


def test_terminal_has_custom_context_menu_actions():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "showTerminalMenu(event,tab)" in javascript
    for label in ("复制", "粘贴", "Agent 命令", "全选终端内容", "清空终端显示", "断开连接", "重新连接"):
        assert f"label:'{label}'" in javascript
    assert "sendTerminalInput(tab,'/agent ')" in javascript


def test_sftp_is_the_default_sidebar_panel():
    html = Path("static/index.html").read_text(encoding="utf-8")

    assert 'class="side-tab active" data-panel="sftp"' in html
    assert 'id="panel-sftp" class="side-panel active"' in html
    assert 'class="side-tab side-tab-agent active"' not in html
    assert 'id="panel-agent" class="side-panel agent-panel active"' not in html


def test_terminal_clipboard_prefers_native_desktop_bridge():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    clipboard_source = javascript[
        javascript.index("function desktopClipboard"):
        javascript.index("async function disconnectTerminal")
    ]
    terminal_source = javascript[
        javascript.index("function newTerminal"):
        javascript.index("function activateTab")
    ]
    assert "window.pywebview?.api" in clipboard_source
    assert "desktop.read_clipboard()" in clipboard_source
    assert "desktop.write_clipboard(value)" in clipboard_source
    assert "navigator.clipboard.readText()" in clipboard_source
    assert "navigator.clipboard.writeText(value)" in clipboard_source
    assert "term.attachCustomKeyEventHandler" in terminal_source
    assert "key==='c'&&term.hasSelection()" in terminal_source
    assert "key==='v'" in terminal_source
    assert "term.paste(text)" in terminal_source
    assert javascript.count("navigator.clipboard") == 2


def test_connected_host_count_is_synced_to_desktop_close_guard():
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    update_count = javascript.split("function updateCount()", 1)[1].split("\n", 1)[0]

    assert "t.status==='connected'" in update_count
    assert "desktop.set_active_connections(n)" in update_count


def test_shortcuts_use_compact_rows_context_menu_and_code_editor():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")

    shortcut_source = javascript[javascript.index("async function loadShortcuts"):javascript.index("function openEditor")]
    assert "group_name" not in shortcut_source
    assert "shortcut-row" in shortcut_source
    assert "shortcut-menu-button" in shortcut_source
    assert "row.oncontextmenu" in shortcut_source
    assert "row.ondblclick" in shortcut_source
    for label in ("编辑", "删除", "填入", "执行"):
        assert f"label:'{label}'" in shortcut_source
    assert 'id="shortcut-add"' in html
    assert 'title="新建快捷指令"' in html
    assert 'aria-label="新建快捷指令"' in html
    assert "maxlength:30" in shortcut_source
    assert "menu.textContent='⋮'" in shortcut_source
    assert "'code'" in shortcut_source
    assert "lineNumbers:true" in javascript
    assert "Enter:shortcutEditorNewline" in javascript
    assert "function shortcutEditorNewline" in javascript
    assert "opensBlock" in javascript
    assert "keepShortcutBlankClickInView(cm)" in javascript
    assert ".shortcut-row" in css
    assert ".shortcut-code-field .CodeMirror" in css


def test_shortcut_editor_blank_click_uses_visible_column_instead_of_long_line_end():
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    handler_source = javascript[
        javascript.index("function keepShortcutBlankClickInView"):
        javascript.index("function openEditor")
    ]
    context = quickjs.Context()
    result = context.eval(
        """
        let handler;
        const calls=[];
        const editor={
          on(_name,value){handler=value},
          coordsChar(coords){
            calls.push(['coords',coords.left,coords.top]);
            return calls.length===1
              ? {line:0,ch:240,outside:1}
              : {line:0,ch:18};
          },
          lastLine(){return 0},
          charCoords(){return {top:10,bottom:30}},
          focus(){calls.push(['focus'])},
          setCursor(pos){calls.push(['cursor',pos.line,pos.ch])},
          extendSelection(pos){calls.push(['extend',pos.line,pos.ch])}
        };
        const event={
          button:0,clientX:150,clientY:200,
          preventDefault(){calls.push(['prevent'])}
        };
        """
        + handler_source
        + """
        keepShortcutBlankClickInView(editor);
        handler(editor,event);
        JSON.stringify(calls);
        """
    )
    assert result == (
        '[["coords",150,200],["prevent"],["coords",150,20],'
        '["focus"],["cursor",0,18]]'
    )


def test_host_library_replaces_sidebar_server_panel_and_supports_card_actions():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")

    assert 'data-panel="servers"' not in html
    assert 'id="panel-servers"' not in html
    assert 'id="host-manager"' in html
    assert 'id="host-manager-search"' in html
    assert 'id="host-library-button"' in html
    assert 'title="打开主机库"' in html
    assert html.index('id="host-library-button"') < html.index('id="terminal-tabs"')
    assert "$('#host-library-button').onclick=showHostManager" in javascript
    assert "card.ondblclick=()=>connectSavedServer(server)" in javascript
    assert "card.oncontextmenu=event=>showHostMenu(event,server)" in javascript
    assert "{label:'编辑',run:()=>editServer(server)}" in javascript
    assert "{label:'删除',run:()=>deleteServer(server)}" in javascript
    assert "saveOnly:true" in javascript
    empty_state = javascript.split("root.innerHTML=query?", 1)[1].split(";", 1)[0]
    assert '<strong>点击右上新建主机</strong></div>' in empty_state
    assert 'class="host-manager-empty-icon"' in empty_state
    assert '<strong>没有匹配的主机</strong></div>' in empty_state
    assert "换一个名称、地址或用户名试试" not in empty_state
    assert "新建一台主机" not in empty_state
    assert '<button class="primary" type="button">新建主机</button>' not in empty_state
    assert 'mask:url("/static/icons/empty-mailbox.svg")' in css
    assert 'mask:url("/static/icons/zoom-glass.svg")' in css
    assert Path("static/icons/empty-mailbox.svg").is_file()
    assert Path("static/icons/zoom-glass.svg").is_file()
    assert "server.os_type" in javascript
    assert '/static/icons/os/${type}.svg' in javascript
    for system in ("default", "ubuntu", "debian", "fedora", "centos", "rocky", "alpine", "arch"):
        assert f"{system}:{{label:" in javascript
        assert f".host-icon-{system}" in css
    icon_dir = Path("static/icons/os")
    for system in ("ubuntu", "debian", "fedora", "centos", "rocky", "alpine", "arch", "windows"):
        icon = icon_dir / f"{system}.svg"
        assert icon.is_file() and "<svg" in icon.read_text(encoding="utf-8")
    assert "www.debian.org/logos/openlogo-nd.svg" in (icon_dir / "SOURCES.md").read_text(encoding="utf-8")
    assert ".host-manager-list" in css
    assert ".host-icon" in css


def test_changed_host_key_dialog_allows_explicit_retrust():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "信任新指纹并连接" in javascript
    assert "请先通过可信渠道核对新指纹" in javascript
    assert "$('#host-key-accept').classList.remove('hidden')" in javascript
    assert "为保护连接，本次无法继续" not in javascript


def test_local_terminal_prompt_is_single_and_pinned_to_bottom():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "if(tab.localPromptShown)return" in javascript
    assert "writeAtTerminalBottom" in javascript
    assert "function terminalAppendRows(term)" in javascript
    assert "buffer.getLine(row)?.translateToString(true)" in javascript
    assert "contentRow-cursorRow+1" in javascript
    assert "'\\r\\n'.repeat(rows)" in javascript
    assert "tab.term.scrollToBottom()" in javascript
    assert "\\x1b[2K\\r${data}" in javascript
    assert "m.type==='output'&&!tab.localPromptShown" in javascript


def test_terminal_size_is_forced_to_remote_after_connection():
    javascript = Path("static/app.js").read_text(encoding="utf-8")

    assert "function syncTerminalSize(tab" in javascript
    assert "tab.fit.fit();syncTerminalSize(tab)" in javascript
    resize_source = javascript[javascript.index("function syncTerminalSize"):javascript.index("function scheduleTerminalFit")]
    context = quickjs.Context()
    synced = context.eval(
        "const WebSocket={OPEN:1};const state={activeId:'active'};const sent=[];"
        + resize_source
        + "const tab={id:'active',status:'connected',host:{classList:{contains(){return true}},getBoundingClientRect(){return {width:1200,height:800}}},fit:{fit(){}},term:{cols:132,rows:48,refresh(){}},ws:{readyState:1,send(message){sent.push(JSON.parse(message))}}};"
        + "fitTerminal(tab);JSON.stringify(sent)"
    )
    assert synced == '[{"type":"resize","cols":132,"rows":48}]'
    connected_handler = javascript.split("if(m.type==='connected')", 1)[1].split("else if", 1)[0]
    assert "tab.status='connected'" in connected_handler
    assert "fitTerminal(tab)" in connected_handler


def test_agent_sidebar_has_streaming_chat_and_keeps_answers_out_of_terminal():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")

    assert 'data-panel="agent"' in html
    assert 'class="side-tab side-tab-agent active"' not in html
    assert 'class="side-tab active" data-panel="sftp"' in html
    assert 'class="side-tab-agent-icon"' in html
    assert '<button class="side-tab" data-panel="agent">Agent</button>' not in html
    assert ".side-tab-agent{display:grid;width:36px" in css
    assert 'mask:url("/static/icons/ai-hub.svg")' in css
    assert ".side-tab-agent:hover{color:var(--accent)}" in css
    assert Path("static/icons/ai-hub.svg").is_file()
    assert 'id="agent-chat-form"' in html
    assert 'id="panel-agent" class="side-panel agent-panel active"' not in html
    assert 'id="panel-sftp" class="side-panel active"' in html
    assert 'id="agent-new-chat"' in html
    assert 'id="agent-chat-stop"' not in html
    assert 'id="agent-open-settings"' not in html
    assert 'id="agent-attach-terminal"' in html
    assert 'id="agent-terminal-context-tag" class="agent-terminal-context-tag hidden"' in html
    assert 'id="agent-terminal-context-remove"' in html
    assert 'id="agent-permission-mode"' in html
    assert 'id="agent-chat-hint"' not in html
    assert 'id="agent-approval-popover"' in html
    assert html.index('id="agent-approval-popover"') < html.index('id="agent-chat-form"')
    assert 'id="agent-approval-yes"' in html
    assert 'id="agent-approval-no"' in html
    assert 'id="agent-permission-confirm"' in html
    assert 'id="agent-permission-confirm-yes"' in html
    assert 'id="agent-permission-confirm-no"' in html
    assert "启用Agent完全访问模式" in html
    assert 'title="附加终端内容到上下文"' in html
    assert "已插入终端内容 ${pendingContext.lineCount}行" in javascript
    assert "tab.agentPendingContext=null;renderAgentChat();$('#agent-chat-input').focus()" in javascript
    assert ".agent-input-shell.has-terminal-context textarea{padding-top:13px;text-indent:" in css
    assert ".agent-input-shell{overflow:hidden;padding-bottom:48px}" in css
    assert ".agent-composer textarea{width:calc(100% - 8px);height:88px;min-height:88px;padding:13px 14px 10px;overflow-y:auto" in css
    assert ".agent-composer textarea::-webkit-scrollbar-track{margin:9px 0" in css
    assert ".agent-composer textarea::-webkit-scrollbar-thumb{min-height:28px;border:3px solid transparent" in css
    assert ".agent-composer textarea::-webkit-scrollbar-button{display:none" in css
    assert "tag.offsetWidth+7" in javascript
    assert ".agent-terminal-context-tag{" in css
    assert 'placeholder="Enter 发送，Shift+Enter 换行"' in html
    assert "e.key==='Enter'&&!e.shiftKey" in javascript
    assert "const AGENT_WELCOME_PROMPTS=[" in javascript
    assert "'查询 GitHub 仓库文档并在服务器上部署'" in javascript
    assert javascript.split("const AGENT_WELCOME_PROMPTS=[", 1)[1].split("];", 1)[0].count("',") == 10
    assert "startAgentWelcomeTypewriter()" in javascript
    assert ".agent-welcome-prompt::after" in css
    composer = html.split('id="agent-chat-form"', 1)[1].split("</form>", 1)[0]
    assert 'id="agent-attach-terminal"' in composer
    assert composer.index('id="agent-attach-terminal"') < composer.index('id="agent-chat-send"')
    assert "'/api/agent/chat/stream'" in javascript
    assert "event.type==='command_output'" in javascript
    assert "event.type==='answer'" in javascript
    assert "event.type==='answer')tab.term" not in javascript
    assert "event.type==='answer_delta'" in javascript
    assert "process.currentText.text+=event.delta" in javascript
    assert "renderAgentChatMarkdown(entry.text)" in javascript
    assert ".agent-message.user{width:auto;max-width:85%;align-self:flex-end;padding:9px 12px" in css
    assert ".agent-message.assistant{align-self:stretch;padding:4px 2px;background:transparent" in css
    assert ".agent-message.assistant{align-self:flex-start;background:var(--panel2)}" not in css
    assert "tab.agentActivity.output=" in javascript
    assert "tab.agentAbortController.signal" in javascript
    assert "send.textContent=busy?'■':'↑'" in javascript
    assert "if(tab.agentBusy){tab.agentAbortController?.abort();return}" in javascript
    assert "agent-open-settings" not in javascript
    assert "writeAgentOperation" not in javascript
    assert "'/api/agent/approval'" in javascript
    assert "permission_mode:state.sidebarAgentPermissionMode" in javascript
    assert "request_approval" in javascript
    assert "full_access" in javascript
    assert "event.type==='command_approval_required'" in javascript
    assert ".agent-approval-popover" in css
    assert 'class="agent-approval-popover agent-permission-confirm hidden"' in html
    assert "sidebarAgentPermissionPrompt" in javascript
    permission_handler = javascript.split("$('#agent-permission-mode').onclick=", 1)[1].split(";\n", 1)[0]
    assert "themedConfirm" not in permission_handler
    assert ".agent-permission-mode.full-access" in css
    assert 'mask:url("/static/icons/agent-request-approval.svg")' in css
    assert 'mask-image:url("/static/icons/agent-full-access.svg")' in css
    assert Path("static/icons/agent-request-approval.svg").is_file()
    assert Path("static/icons/agent-full-access.svg").is_file()


def test_terminal_agent_is_an_isolated_contextual_quick_fix():
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")

    assert "function recentTerminalContext(tab,command='')" in javascript
    assert "'/api/agent/quick-fix/stream'" in javascript
    assert "terminal_context:terminalContext.text||null" in javascript
    assert "runAgent(tab,captured.slice(6).trim(),context)" in javascript
    assert "continue_incident:request.continueIncident" in javascript
    assert "explain_only:request.explainOnly" in javascript
    assert "permission_mode:tab.quickAgentPermissionMode" in javascript
    assert "function handleTerminalAgentModeCommand" in javascript
    assert "const TERMINAL_AGENT_COMPLETIONS=" in javascript
    assert "function createTerminalAgentAutocomplete" in javascript
    assert "function showTerminalAgentAutocomplete" in javascript
    assert "function hideTerminalAgentAutocomplete" in javascript
    assert "function selectTerminalAgentAutocomplete" in javascript
    assert "function handleTerminalAgentAutocompleteKey" in javascript
    assert "tab.agentBuffer==='/agent '" in javascript
    assert "if(!showCompletions)hideTerminalAgentAutocomplete(tab)" in javascript
    assert ".terminal-agent-autocomplete-option.selected" in css
    assert "/agent mode approval" in javascript
    assert "/agent mode full" in javascript
    assert "function handleTerminalAgentApprovalInput" in javascript
    assert "是否允许执行？请输入 是/否" in javascript
    assert "tab.quickAgentApproval={...event" in javascript
    assert "source:'quick'" not in javascript
    assert ".side-tab[data-panel=\"agent\"]" not in javascript
    assert "左侧 Agent 正在操作当前主机" not in javascript
    assert "function redactTerminalContext" in javascript
    assert "tab.lastCommandStart" in javascript
    assert "tab.term.hasSelection" in javascript
    assert "tab.quickAgentAbort?.abort()" in javascript
    assert "tab.term.buffer.active.type!=='alternate'" in javascript
    assert "--no-context" in javascript
    assert "--explain" in javascript
    assert "agent-message-role" not in javascript
    assert "正在执行命令：" in javascript
    assert "agent-command" in css
    assert "text-overflow:ellipsis" in css
    assert ".agent-message,.agent-activity{flex-shrink:0}" in css
    assert ".agent-markdown,.agent-markdown *{-webkit-user-select:text;user-select:text;cursor:auto}" in css
    assert ".agent-chat,.agent-message{cursor:default}" in css
    assert "if(entry.role==='assistant')el.append(createAgentMessageCopyButton(entry.text))" in javascript
    assert "className='agent-message-copy'" in javascript
    assert ".agent-message-copy{display:grid;width:22px;height:22px;margin:3px auto 0 0" in css
    assert 'mask:url("/static/icons/copy.svg")' in css
    assert Path("static/icons/copy.svg").is_file()
    assert "button.setAttribute('aria-label','复制此消息')" in javascript
    assert "$('#agent-chat').addEventListener('contextmenu'" not in javascript
    assert "复制选中内容" not in javascript
    assert "复制整条消息" not in javascript
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
    assert "event.type==='local_tool_prepare'" in javascript
    assert "正在编辑文件：生成内容中…" in javascript
    assert "event.type==='local_tool_start'" in javascript
    assert "event.type==='local_tool_end'" in javascript
    assert "正在${action}" in javascript
    assert "item.status=event.success?'done':'failed'" in javascript
    assert "reasoning_content" not in javascript


def test_terminal_agent_redacts_context_and_parses_quick_fix_modes():
    source = Path("static/app.js").read_text(encoding="utf-8")
    redact_source = source[source.index("function redactTerminalContext"):source.index("function terminalBufferText")]
    parse_source = source[source.index("function parseTerminalAgentRequest"):source.index("async function resetTerminalAgent")]
    context = quickjs.Context()
    redacted = context.eval(redact_source + "JSON.stringify(redactTerminalContext('api_key=secret\\nAuthorization: Bearer token'))")
    assert "secret" not in redacted
    assert "token" not in redacted
    assert "已隐藏" in redacted
    modes = context.eval(parse_source + "JSON.stringify([parseTerminalAgentRequest('--explain 原因'),parseTerminalAgentRequest('继续，验证')])")
    assert '"explainOnly":true' in modes
    assert '"continueIncident":true' in modes
    mode_context = quickjs.Context()
    switched = mode_context.eval(parse_source + """
      const output=[];
      const tab={quickAgentPermissionMode:'request_approval',term:{writeln(value){output.push(value)}}};
      handleTerminalAgentModeCommand(tab,'mode full');
      JSON.stringify({mode:tab.quickAgentPermissionMode,output});
    """)
    assert '"mode":"full_access"' in switched
    assert "完全访问模式" in switched


def test_terminal_agent_autocomplete_lists_every_documented_subcommand():
    source = Path("static/app.js").read_text(encoding="utf-8")
    completion_source = source[source.index("const TERMINAL_AGENT_COMPLETIONS="):source.index("function createTerminalAgentAutocomplete")]
    context = quickjs.Context()
    values = context.eval(completion_source + "JSON.stringify(TERMINAL_AGENT_COMPLETIONS.map(item=>item.value.trim()))")
    assert values == '["--explain","--no-context","继续","mode","mode approval","mode full","clear"]'


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


def test_agent_markdown_renders_table_after_unseparated_title():
    source = Path("static/app.js").read_text(encoding="utf-8")
    markdown_functions = source[source.index("function appendAgentMarkdownInline"):source.index("function renderAgentChat(){")]
    dom_stub = """
    const document={
      createElement(tag){return {tag,childNodes:[],dataset:{},style:{},append(...items){this.childNodes.push(...items)}}},
      createTextNode(text){return {text}}
    };
    """
    markdown = "**关键文件路径**\n| 用途 | 路径 |\n|------|------|\n| 配置文件 | `/etc/caddy/Caddyfile` |\n| 站点根目录 | `/usr/share/caddy` |\n| 运行数据 | `/var/lib/caddy` |"
    context = quickjs.Context()
    rendered = context.eval(dom_stub + markdown_functions + f"JSON.stringify(renderAgentChatMarkdown({markdown!r}).childNodes.map(node=>node.tag))")
    assert rendered == '["p","table"]'
