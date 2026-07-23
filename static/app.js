const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => [...root.querySelectorAll(q)];
const storedSidebarAgentPermissionMode=sessionStorage.getItem('coshell-sidebar-agent-permission-mode');
const state = {tabs: [], activeId: null, servers: [], shortcuts: [], sshKeys:[], mcpServers:[], vault: null, agentSettings:null, sidebarAgentPermissionMode:storedSidebarAgentPermissionMode==='full_access'?'full_access':'request_approval', sidebarAgentPermissionPrompt:false, selectedFiles: new Set(), uploadTasks: new Map(), remoteClipboard: null, pendingReconnect:null, editor: {cm:null,sessionId:null,path:null,mtime:null,dirty:false,saving:false}};

async function api(path, options={}) {
  const res = await fetch(path, {headers: options.body instanceof FormData ? {} : {'Content-Type':'application/json'}, ...options});
  if (!res.ok) { let msg=res.statusText; try {msg=(await res.json()).detail||msg} catch{} const error=new Error(msg);error.status=res.status;throw error; }
  const type=res.headers.get('content-type')||''; return type.includes('json') ? res.json() : res;
}
function toast(message, error=false) { const el=document.createElement('div'); el.className='toast'+(error?' error':''); el.textContent=message;const dialogs=$$('dialog[open]');const dialog=dialogs[dialogs.length-1];let root=$('#toast-stack');if(dialog){root=$('.modal-toast-stack',dialog);if(!root){root=document.createElement('div');root.className='toast-stack modal-toast-stack';dialog.append(root)}}root.append(el);setTimeout(()=>{el.remove();if(root.classList.contains('modal-toast-stack')&&!root.children.length)root.remove()},4200); }
let promptQueue=Promise.resolve();
function appPrompt({title='请确认',message='',value='',label='输入内容',confirmText='确认',danger=false,options=null,input=false}){const task=()=>new Promise(resolve=>{const dialog=$('#app-prompt-dialog'),form=$('#app-prompt-form'),field=$('#app-prompt-field'),inputEl=$('#app-prompt-input'),optionsEl=$('#app-prompt-options'),submit=$('#app-prompt-submit');let selected=null,settled=false;$('#app-prompt-title').textContent=title;$('#app-prompt-message').textContent=message;$('#app-prompt-label').textContent=label;submit.textContent=confirmText;submit.classList.toggle('danger',danger);submit.classList.toggle('primary',!danger);field.classList.toggle('hidden',!input);optionsEl.classList.toggle('hidden',!options);optionsEl.replaceChildren();inputEl.value=value;if(options){for(const option of options){const button=document.createElement('button');button.type='button';button.className='app-prompt-option';button.textContent=option.label;button.onclick=()=>{selected=option.value;$$('.app-prompt-option',optionsEl).forEach(x=>x.classList.toggle('selected',x===button));submit.focus()};optionsEl.append(button)}}const finish=result=>{if(settled)return;settled=true;dialog.close();resolve(result)};form.onsubmit=e=>{e.preventDefault();finish(options?selected:(input?inputEl.value:true))};$('#app-prompt-cancel').onclick=()=>finish(null);$('#app-prompt-close').onclick=()=>finish(null);dialog.oncancel=e=>{e.preventDefault();finish(null)};dialog.showModal();setTimeout(()=>input?inputEl.select():(optionsEl.firstElementChild||submit).focus(),0)});const result=promptQueue.then(task,task);promptQueue=result.then(()=>undefined,()=>undefined);return result}
const themedConfirm=(message,{title='请确认',confirmText='确认',danger=false}={})=>appPrompt({title,message,confirmText,danger});
const themedInput=(message,value='',options={})=>appPrompt({title:options.title||'请输入',message,value,label:options.label||'输入内容',confirmText:options.confirmText||'确定',input:true});
const themedChoice=(message,options,title='请选择操作')=>appPrompt({title,message,options,confirmText:'继续'});
let pendingHostKey=null;
function answerHostKey(accept){
  const pending=pendingHostKey;pendingHostKey=null;
  const dialog=$('#host-key-dialog');if(dialog.open)dialog.close();
  if(pending?.ws.readyState===WebSocket.OPEN)pending.ws.send(JSON.stringify({type:'trust_host',accept:!!accept}));
}
function showHostKeyDialog(ws,message){
  if(pendingHostKey)answerHostKey(false);
  pendingHostKey={ws};
  $('#host-key-host').textContent=`${message.host}:${message.port}`;$('#host-key-algorithm').textContent=message.algorithm;$('#host-key-fingerprint').textContent=message.fingerprint;
  const changed=!!message.changed;$('#host-key-title').textContent=changed?'主机身份已变化':'验证主机身份';$('#host-key-message').textContent=changed?'服务器返回的主机密钥与已信任记录不一致。':'首次连接此服务器，请核对以下指纹后再决定是否信任。';
  $('#host-key-warning').classList.toggle('hidden',!changed);$('#host-key-warning').textContent=changed?'这可能表示服务器已重装，也可能存在中间人攻击。请先通过可信渠道核对新指纹；确认无误后，可以信任新指纹并重新连接。':'';
  $('#host-key-accept').classList.remove('hidden');$('#host-key-accept').textContent=changed?'信任新指纹并连接':'信任并连接';$('#host-key-cancel').textContent='取消';
  $('#host-key-dialog').showModal();$('#host-key-cancel').focus();
}
$('#host-key-form').addEventListener('submit',e=>{e.preventDefault();answerHostKey(true)});$('#host-key-cancel').onclick=()=>answerHostKey(false);$('#host-key-dialog').addEventListener('cancel',e=>{e.preventDefault();answerHostKey(false)});
function esc(s=''){ return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function uid(){ return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`; }
const WELCOME_GREETINGS=[
  {start:0,messages:['夜深了。终端可以继续连着，人不用，有情况我盯着。','凌晨了。如果你还醒着，那我就陪你一起加班一会儿。']},
  {start:5,messages:['早呀~我已经在线，准备好陪你建立今天的第一条连接。','早上好。咖啡可以慢慢来，终端这边我帮你守着。']},
  {start:11,messages:['中午好。忙了一上午，记得给自己和服务器都喘口气。我在。','中午好～远程的世界还在转，这里可以安静一点。想连哪边？']},
  {start:14,messages:['嗨，下午好。还有主机要处理的话，我陪你；想歇一会儿也行。','下午好。午后容易犯困，终端这边我帮你盯着。需要连机还是排查？']},
  {start:18,messages:['嗨，傍晚好。想把今天的活儿收个尾，还是先歇会儿再说？','傍晚了。忙了一天，终于能喘口气了吧？我在。']},
  {start:20,messages:['嗨，晚上好。不着急下线的话，我陪你多待会儿。','晚上好。夜色下来了，终端的光刚好。今天想要做什么？']}
];
function welcomeGreeting(date=new Date()){
  const hour=date.getHours();let period=WELCOME_GREETINGS[0],periodIndex=0;
  for(let i=1;i<WELCOME_GREETINGS.length&&hour>=WELCOME_GREETINGS[i].start;i++){period=WELCOME_GREETINGS[i];periodIndex=i}
  const daySeed=date.getFullYear()*372+(date.getMonth()+1)*31+date.getDate()+periodIndex;
  return period.messages[daySeed%period.messages.length]
}
function updateWelcomeGreeting(date=new Date()){const target=$('#welcome-greeting');if(target)target.textContent=welcomeGreeting(date)}
updateWelcomeGreeting();
setInterval(()=>updateWelcomeGreeting(),60_000);
function activeTab(){ return state.tabs.find(t=>t.id===state.activeId); }
function rawTerminalInput(tab,data){if(tab.ws?.readyState!==WebSocket.OPEN||tab.status!=='connected')return;const bytes=new TextEncoder().encode(data);let binary='';for(let i=0;i<bytes.length;i+=0x8000)binary+=String.fromCharCode(...bytes.subarray(i,i+0x8000));tab.ws.send(JSON.stringify({type:'input',encoding:'base64',data:btoa(binary)}))}
function eraseTerminalCells(term,width){if(width>0)term.write('\b'.repeat(width)+' '.repeat(width)+'\b'.repeat(width))}
function eraseLocalInput(term,text){for(const char of [...text].reverse())eraseTerminalCells(term,terminalCellWidth(char))}
function eraseLastLocalCharacter(term,text){const chars=[...text],last=chars.pop()||'';eraseTerminalCells(term,terminalCellWidth(last));return chars.join('')}
function terminalText(value=''){return String(value).replace(/\x1b(?:\[[0-?]*[ -\/]*[@-~]|\][^\x07]*(?:\x07|$))?/g,'').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g,'')}
function terminalLines(value=''){return terminalText(value).replace(/\r\n?/g,'\n').split('\n')}
function renderAgentCommand(value=''){
  const lines=terminalLines(value);while(lines.length&& !lines[0].trim())lines.shift();while(lines.length&& !lines[lines.length-1].trim())lines.pop();
  return lines.map(line=>{const comment=line.trimStart().startsWith('#');return `\x1b[38;5;${comment?'245':'117'}m│ ${line||' '}\x1b[0m`}).join('\r\n')
}
function renderMarkdownInline(value=''){
  const tokens=[];const hold=text=>{const key=`\uE000${tokens.length}\uE001`;tokens.push(text);return key};let text=terminalText(value);
  text=text.replace(/`([^`\n]+)`/g,(_,code)=>hold(`\x1b[38;5;229m\x1b[48;5;236m ${code} \x1b[0m`));
  text=text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g,(_,label,url)=>hold(`\x1b[4;38;5;111m${label}\x1b[0m \x1b[90m(${url})\x1b[0m`));
  text=text.replace(/\*\*([^*\n]+)\*\*/g,'\x1b[1m$1\x1b[0m').replace(/__([^_\n]+)__/g,'\x1b[1m$1\x1b[0m');
  text=text.replace(/~~([^~\n]+)~~/g,'\x1b[9m$1\x1b[0m');
  text=text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g,'$1\x1b[3m$2\x1b[0m').replace(/(^|[^_])_([^_\n]+)_(?!_)/g,'$1\x1b[3m$2\x1b[0m');
  return text.replace(/\uE000(\d+)\uE001/g,(_,index)=>tokens[Number(index)]||'')
}
function markdownTableCells(line=''){
  let text=line.trim();if(!text.includes('|'))return null;if(text.startsWith('|'))text=text.slice(1);if(text.endsWith('|'))text=text.slice(0,-1);
  const cells=[];let cell='',escaped=false;for(const char of text){if(escaped){cell+=char;escaped=false;continue}if(char==='\\'){escaped=true;continue}if(char==='|'){cells.push(cell.trim());cell='';continue}cell+=char}if(escaped)cell+='\\';cells.push(cell.trim());return cells
}
function markdownTableAlignment(line=''){
  const cells=markdownTableCells(line);if(!cells||!cells.length||cells.some(cell=>!/^:?(?:-{3,}|─{3,}):?$/.test(cell.replace(/\s/g,''))))return null;
  return cells.map(cell=>{const value=cell.replace(/\s/g,'');return value.startsWith(':')&&value.endsWith(':')?'center':value.endsWith(':')?'right':'left'})
}
function terminalCellWidth(value=''){
  const text=terminalText(value).replace(/\x1b\[[0-?]*[ -\/]*[@-~]/g,'');let width=0;for(const char of text){const cp=char.codePointAt(0);if(cp===0x200d||(cp>=0xfe00&&cp<=0xfe0f)||(/\p{Mark}/u.test(char)))continue;width+=(cp>=0x1100&&(cp<=0x115f||cp===0x2329||cp===0x232a||(cp>=0x2e80&&cp<=0xa4cf&&cp!==0x303f)||(cp>=0xac00&&cp<=0xd7a3)||(cp>=0xf900&&cp<=0xfaff)||(cp>=0xfe10&&cp<=0xfe19)||(cp>=0xfe30&&cp<=0xfe6f)||(cp>=0xff00&&cp<=0xff60)||(cp>=0xffe0&&cp<=0xffe6)||(cp>=0x1f300&&cp<=0x1faff)||(cp>=0x20000&&cp<=0x3fffd)))?2:1}return width
}
function renderMarkdownTable(rows,alignments){
  const columns=Math.max(...rows.map(row=>row.length),alignments.length);const widths=Array.from({length:columns},(_,index)=>Math.max(3,...rows.map(row=>terminalCellWidth(row[index]||''))));
  const border=(left,middle,right,fill='─')=>left+widths.map(width=>fill.repeat(width+2)).join(middle)+right;
  const renderRow=(row,header=false)=>'│'+widths.map((width,index)=>{const value=renderMarkdownInline(row[index]||''),space=width-terminalCellWidth(value),alignment=alignments[index]||'left';let before=0,after=space;if(alignment==='right'){before=space;after=0}else if(alignment==='center'){before=Math.floor(space/2);after=space-before}return ` ${' '.repeat(before)}${header?'\x1b[1m'+value+'\x1b[0m':value}${' '.repeat(after)} `}).join('│')+'│';
  const output=[border('┌','┬','┐'),renderRow(rows[0],true),border('├','┼','┤')];rows.slice(1).forEach(row=>output.push(renderRow(row)));output.push(border('└','┴','┘'));return output
}
function renderAgentMarkdown(value=''){
  const lines=terminalText(value).replace(/\r\n/g,'\n').split('\n');let inCode=false,language='';const output=[];
  for(let lineIndex=0;lineIndex<lines.length;lineIndex++){const raw=lines[lineIndex];
    const fence=raw.match(/^\s*```\s*([^`]*)$/);if(fence){if(!inCode){inCode=true;language=fence[1].trim();if(language)output.push(`\x1b[90m[${language}]\x1b[0m`)}else{inCode=false;language=''}continue}
    if(inCode){output.push(`\x1b[38;5;252m\x1b[48;5;236m ${raw||' '} \x1b[0m`);continue}
    const tableHeader=markdownTableCells(raw),tableAlignments=lineIndex+1<lines.length?markdownTableAlignment(lines[lineIndex+1]):null;
    if(tableHeader&&tableAlignments&&tableHeader.length===tableAlignments.length){const rows=[tableHeader];lineIndex+=2;while(lineIndex<lines.length){const row=markdownTableCells(lines[lineIndex]);if(!row)break;rows.push(row);lineIndex++}lineIndex--;output.push(...renderMarkdownTable(rows,tableAlignments));continue}
    let match;if((match=raw.match(/^\s*(#{1,6})\s+(.+)$/))){const color=match[1].length<=2?'117':'111';output.push(`\x1b[1;38;5;${color}m${renderMarkdownInline(match[2])}\x1b[0m`);continue}
    if(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(raw)){output.push('\x1b[90m────────────────────────────────────────\x1b[0m');continue}
    if((match=raw.match(/^(\s*)[-*+]\s+(.+)$/))){output.push(`${match[1]}\x1b[38;5;111m•\x1b[0m ${renderMarkdownInline(match[2])}`);continue}
    if((match=raw.match(/^(\s*)\d+[.)]\s+(.+)$/))){const number=raw.trim().match(/^\d+/)?.[0]||'1';output.push(`${match[1]}\x1b[38;5;111m${number}.\x1b[0m ${renderMarkdownInline(match[2])}`);continue}
    if((match=raw.match(/^\s*>\s?(.*)$/))){output.push(`\x1b[38;5;111m│\x1b[0m \x1b[90m${renderMarkdownInline(match[1])}\x1b[0m`);continue}
    if(/^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/.test(raw)){output.push('\x1b[90m'+raw.replace(/-/g,'─')+'\x1b[0m');continue}
    output.push(renderMarkdownInline(raw))
  }
  if(inCode)output.push('\x1b[0m');return output.join('\r\n')
}
function redactTerminalContext(value=''){
  let count=0,text=String(value);const replace=(pattern,replacer)=>{text=text.replace(pattern,(...args)=>{count++;return typeof replacer==='function'?replacer(...args):replacer})};
  replace(/-----BEGIN [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----[\s\S]*?-----END [^-]*(?:PRIVATE KEY|OPENSSH PRIVATE KEY)-----/gi,'[已隐藏私钥]');
  replace(/\b(authorization\s*:\s*(?:bearer|basic)\s+)[^\s]+/gi,(_,prefix)=>`${prefix}[已隐藏]`);
  replace(/\b((?:api[_-]?key|access[_-]?token|secret|password|passwd|pwd)\s*[=:]\s*)[^\s'";]+/gi,(_,prefix)=>`${prefix}[已隐藏]`);
  return {text,count}
}
function terminalBufferText(tab,start,end){const buffer=tab.term.buffer.active;let text='',lines=0;for(let index=Math.max(0,start);index<Math.min(end,buffer.length);index++){const line=buffer.getLine(index);if(!line)continue;const value=line.translateToString(true);if(text&&!line.isWrapped){text+='\n';lines++}text+=value}return {text:text.trimEnd(),lines:Math.max(lines,text?1:0)}}
function recentTerminalContext(tab,command=''){
  const selected=tab.term.hasSelection?.()?tab.term.getSelection().trim():'';let raw,source;
  if(selected){raw={text:selected,lines:selected.split(/\r?\n/).length};source='selection'}else{const buffer=tab.term.buffer.active,end=Math.min(buffer.length,buffer.baseY+buffer.cursorY+1),start=tab.lastCommandStart===null?Math.max(0,end-120):Math.max(tab.lastCommandStart,end-120);raw=terminalBufferText(tab,start,end);source=tab.lastCommandStart===null?'recent':'last_command'}
  let text=raw.text;if(command&&text.endsWith(command))text=text.slice(0,-command.length).trimEnd();text=text.slice(-20000);const redacted=redactTerminalContext(text);return {text:redacted.text,lineCount:redacted.text?redacted.text.split('\n').length:0,source,redactions:redacted.count}
}
function parseTerminalAgentRequest(value=''){
  let message=value.trim(),explainOnly=false,noContext=false,continueIncident=false;
  if(/^--explain(?:\s|$)/i.test(message)){explainOnly=true;message=message.replace(/^--explain\s*/i,'')}
  if(/^--no-context(?:\s|$)/i.test(message)){noContext=true;message=message.replace(/^--no-context\s*/i,'')}
  if(/^(?:继续|continue)(?:[\s,，:：]|$)/i.test(message)){continueIncident=true;message=message.replace(/^(?:继续|continue)[\s,，:：]*/i,'').trim()||'继续处理刚才的终端故障并验证结果'}
  return {message,explainOnly,noContext,continueIncident}
}
function terminalAgentPermissionModeLabel(mode){return mode==='full_access'?'完全访问模式':'请求批准模式'}
const TERMINAL_AGENT_COMPLETIONS=[
  {value:'--explain ',label:'--explain',description:'只读分析和解释终端问题'},
  {value:'--no-context ',label:'--no-context',description:'不附带最近终端输出'},
  {value:'继续 ',label:'继续',description:'继续处理最近一次终端故障'},
  {value:'mode',label:'mode',description:'查看当前终端 Agent 权限模式'},
  {value:'mode approval',label:'mode approval',description:'高风险命令执行前请求批准'},
  {value:'mode full',label:'mode full',description:'完全访问，执行命令时不请求批准'},
  {value:'clear',label:'clear',description:'清除终端 Agent 临时故障上下文'},
];
function createTerminalAgentAutocomplete(tab){
  const root=document.createElement('div');root.className='terminal-agent-autocomplete hidden';root.setAttribute('role','listbox');root.setAttribute('aria-label','Agent 子命令');
  const title=document.createElement('div');title.className='terminal-agent-autocomplete-title';title.textContent='Agent 子命令';root.append(title);
  TERMINAL_AGENT_COMPLETIONS.forEach((item,index)=>{const button=document.createElement('button');button.type='button';button.className='terminal-agent-autocomplete-option';button.setAttribute('role','option');const command=document.createElement('code');command.textContent=item.label;const description=document.createElement('span');description.textContent=item.description;button.append(command,description);button.onpointerdown=event=>event.preventDefault();button.onmouseenter=()=>{tab.agentAutocompleteIndex=index;renderTerminalAgentAutocompleteSelection(tab)};button.onclick=()=>selectTerminalAgentAutocomplete(tab,index);root.append(button)});
  tab.host.append(root);tab.agentAutocomplete=root
}
function renderTerminalAgentAutocompleteSelection(tab){
  $$('.terminal-agent-autocomplete-option',tab.agentAutocomplete).forEach((option,index)=>{const selected=index===tab.agentAutocompleteIndex;option.classList.toggle('selected',selected);option.setAttribute('aria-selected',String(selected))})
}
function positionTerminalAgentAutocomplete(tab){
  const root=tab.agentAutocomplete,screen=$('.xterm-screen',tab.host);if(!root||!screen||!tab.agentAutocompleteOpen)return;const hostRect=tab.host.getBoundingClientRect(),screenRect=screen.getBoundingClientRect(),cellWidth=screenRect.width/Math.max(1,tab.term.cols),cellHeight=screenRect.height/Math.max(1,tab.term.rows),buffer=tab.term.buffer.active,startColumn=Math.max(0,buffer.cursorX-terminalCellWidth('/agent '));let left=screenRect.left-hostRect.left+startColumn*cellWidth,top=screenRect.top-hostRect.top+(buffer.cursorY+1)*cellHeight;left=Math.max(8,Math.min(left,hostRect.width-root.offsetWidth-8));root.classList.remove('above');if(top+root.offsetHeight>hostRect.height-8){top=Math.max(8,screenRect.top-hostRect.top+buffer.cursorY*cellHeight-root.offsetHeight-4);root.classList.add('above')}root.style.left=`${left}px`;root.style.top=`${top}px`;root.style.visibility='visible'
}
function showTerminalAgentAutocomplete(tab){
  if(tab.id!==state.activeId||tab.agentBuffer!=='/agent '||!tab.agentAutocomplete)return;tab.agentAutocompleteOpen=true;tab.agentAutocompleteIndex=0;tab.agentAutocomplete.classList.remove('hidden');tab.agentAutocomplete.style.visibility='hidden';renderTerminalAgentAutocompleteSelection(tab);requestAnimationFrame(()=>positionTerminalAgentAutocomplete(tab))
}
function hideTerminalAgentAutocomplete(tab){
  if(!tab?.agentAutocomplete)return;tab.agentAutocompleteOpen=false;tab.agentAutocomplete.classList.add('hidden');tab.agentAutocomplete.style.visibility='';tab.agentAutocomplete.classList.remove('above')
}
function selectTerminalAgentAutocomplete(tab,index=tab.agentAutocompleteIndex){
  if(!tab.agentAutocompleteOpen||tab.agentBuffer!=='/agent ')return;const item=TERMINAL_AGENT_COMPLETIONS[index];if(!item)return;tab.agentBuffer+=item.value;tab.term.write(item.value);hideTerminalAgentAutocomplete(tab);tab.term.focus()
}
function handleTerminalAgentAutocompleteKey(tab,data){
  if(!tab.agentAutocompleteOpen)return false;
  if(['\x1b[A','\x1bOA','\x1b[B','\x1bOB'].includes(data)){const direction=data.endsWith('A')?-1:1;tab.agentAutocompleteIndex=(tab.agentAutocompleteIndex+direction+TERMINAL_AGENT_COMPLETIONS.length)%TERMINAL_AGENT_COMPLETIONS.length;renderTerminalAgentAutocompleteSelection(tab);return true}
  if(data==='\t'||data==='\r'||data==='\n'){selectTerminalAgentAutocomplete(tab);return true}
  if(data==='\x1b'){hideTerminalAgentAutocomplete(tab);return true}
  hideTerminalAgentAutocomplete(tab);return false
}
function handleTerminalAgentModeCommand(tab,message=''){
  const match=message.trim().match(/^(?:mode|模式)(?:\s+(.*))?$/i);if(!match)return false;const value=(match[1]||'').trim().toLowerCase();
  if(!value){tab.term.writeln(`\x1b[38;5;111m[终端 Agent 当前为${terminalAgentPermissionModeLabel(tab.quickAgentPermissionMode)}]\x1b[0m\r\n\x1b[90m切换：/agent mode approval 或 /agent mode full\x1b[0m`);return true}
  if(['approval','request','request_approval','ask','请求批准','批准'].includes(value)){tab.quickAgentPermissionMode='request_approval';tab.term.writeln('\x1b[38;5;111m[终端 Agent 已切换为请求批准模式]\x1b[0m');return true}
  if(['full','full_access','access','完全访问'].includes(value)){tab.quickAgentPermissionMode='full_access';tab.term.writeln('\x1b[33m[终端 Agent 已切换为完全访问模式；高风险命令将不再请求授权]\x1b[0m');return true}
  tab.term.writeln('\x1b[31m[未知模式。请使用 /agent mode approval 或 /agent mode full]\x1b[0m');return true
}
async function answerTerminalAgentApproval(tab,approved){
  const pending=tab.quickAgentApproval;if(!pending||pending.submitting)return;pending.submitting=true;try{await api('/api/agent/approval',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,approval_id:pending.approval_id,approved})});if(tab.quickAgentApproval===pending)tab.quickAgentApproval=null;tab.quickApprovalBuffer='';tab.term.writeln(`\x1b[${approved?'38;5;111':'33'}m[已${approved?'批准':'拒绝'}执行该命令]\x1b[0m`)}catch(err){pending.submitting=false;tab.term.writeln(`\x1b[31m[授权响应失败：${terminalText(err.message)}]\x1b[0m\r\n\x1b[1;33m请重新输入 是/否：\x1b[0m`)}
}
function handleTerminalAgentApprovalInput(tab,data){
  if(data.includes('\x03')){tab.quickAgentAbort?.abort();return}
  if(tab.quickAgentApproval?.submitting)return;
  for(const char of data){
    if(char==='\r'||char==='\n'){
      const answer=(tab.quickApprovalBuffer||'').trim().toLowerCase();tab.quickApprovalBuffer='';tab.term.write('\r\n');
      if(['是','y','yes'].includes(answer)){answerTerminalAgentApproval(tab,true);return}
      if(['否','n','no'].includes(answer)){answerTerminalAgentApproval(tab,false);return}
      tab.term.write('\x1b[33m请输入 是 或 否：\x1b[0m');continue
    }
    if(char==='\x7f'||char==='\b'){if(tab.quickApprovalBuffer)tab.quickApprovalBuffer=eraseLastLocalCharacter(tab.term,tab.quickApprovalBuffer);continue}
    if(char>=' '){tab.quickApprovalBuffer=(tab.quickApprovalBuffer||'')+char;tab.term.write(char)}
  }
}
async function resetTerminalAgent(tab){await api('/api/agent/quick-fix/reset',{method:'POST',body:JSON.stringify({session_id:tab.sessionId})});tab.quickIncidentCommandStart=null;tab.term.writeln('\x1b[90m[已清除终端 Agent 的临时故障上下文]\x1b[0m')}
function handleTerminalAgentEvent(tab,event,state){
  if(event.type==='thinking_start'&&!state.thinkingShown){state.thinkingShown=true;tab.term.writeln('\x1b[90m[分析] 正在定位故障原因…\x1b[0m')}
  else if(event.type==='command_approval_required'){tab.quickAgentApproval={...event,submitting:false};tab.quickApprovalBuffer='';tab.term.writeln(`\r\n\x1b[1;33m[Agent 请求执行权限]\x1b[0m\r\n\x1b[33m${terminalText(event.reason||'该命令可能造成不可逆变更')}\x1b[0m\r\n${renderAgentCommand(event.command)}\r\n\x1b[1;33m是否允许执行？请输入 是/否：\x1b[0m`)}
  else if(event.type==='command_approval_resolved'){if(tab.quickAgentApproval?.approval_id===event.approval_id){tab.quickAgentApproval=null;tab.quickApprovalBuffer=''}}
  else if(event.type==='command_start'){state.commandOpen=true;tab.term.writeln(`\r\n\x1b[1;38;5;111m┌─ 检查或修复\x1b[0m\r\n${renderAgentCommand(event.command)}`)}
  else if(event.type==='command_output'){const value=terminalText(event.data||'').replace(/\r?\n/g,'\r\n');if(value)tab.term.write(event.stream==='stderr'?`\x1b[31m${value}\x1b[0m`:value)}
  else if(event.type==='command_end'){state.commandOpen=false;const failed=Number(event.exit_code)!==0;tab.term.writeln(`\r\n\x1b[${failed?'31':'90'}m└─ ${failed?'失败':'完成'} · 退出码 ${event.exit_code}\x1b[0m`)}
  else if(event.type==='activity'){const names={search:'在线搜索',fetch:'读取网页'};tab.term.writeln(`\x1b[90m[${names[event.activity]||'辅助检查'}] ${terminalText(event.label)}\x1b[0m`)}
  else if(event.type==='answer'){state.answerShown=true;tab.term.writeln(`\r\n\x1b[38;5;111mAgent:\x1b[0m ${renderAgentMarkdown(event.message)}${event.limit_reached?'\r\n\x1b[33m已达到本次快速处置上限，可使用 /agent 继续 接着处理。\x1b[0m':''}`)}
  else if(event.type==='cancelled'){state.cancelled=true;tab.term.writeln('\r\n\x1b[33m[终端 Agent 已停止]\x1b[0m')}
  else if(event.type==='error'){state.error=true;tab.term.writeln(`\r\n\x1b[31m[Agent] ${terminalText(event.message)}\x1b[0m`)}
}
async function runAgent(tab,value,context){
  const request=parseTerminalAgentRequest(value);if(/^clear$/i.test(request.message)){try{await resetTerminalAgent(tab)}catch(err){tab.term.writeln(`\x1b[31m[Agent] ${terminalText(err.message)}\x1b[0m`)}finally{rawTerminalInput(tab,'\r')}return}
  if(handleTerminalAgentModeCommand(tab,request.message)){rawTerminalInput(tab,'\r');return}
  if(!request.message){tab.term.writeln('\x1b[33m用法：/agent 帮我解决这个报错 · /agent --explain 解释报错 · /agent mode · /agent clear\x1b[0m');rawTerminalInput(tab,'\r');return}
  const noNewContext=request.continueIncident&&tab.quickIncidentCommandStart===tab.lastCommandStart;const terminalContext=request.noContext||noNewContext?{text:'',lineCount:0,source:'none',redactions:0}:context;if(!request.continueIncident)tab.quickIncidentCommandStart=tab.lastCommandStart;tab.quickAgentBusy=true;tab.quickAgentAbort=new AbortController();const progress={thinkingShown:false,answerShown:false,cancelled:false,error:false,commandOpen:false};
  const sourceName=terminalContext.source==='selection'?'选中内容':terminalContext.source==='last_command'?'上一条命令现场':'最近终端输出';tab.term.writeln(`\x1b[38;5;111m[终端 Agent · ${terminalAgentPermissionModeLabel(tab.quickAgentPermissionMode)} · ${terminalContext.text?`已附带${sourceName} ${terminalContext.lineCount} 行${terminalContext.redactions?`，隐藏 ${terminalContext.redactions} 处敏感信息`:''}`:'未附带终端上下文'} · Ctrl+C 停止]\x1b[0m`);
  renderAgentChat();try{const response=await fetch('/api/agent/quick-fix/stream',{method:'POST',headers:{'Content-Type':'application/json'},signal:tab.quickAgentAbort.signal,body:JSON.stringify({session_id:tab.sessionId,message:request.message,terminal_context:terminalContext.text||null,continue_incident:request.continueIncident,explain_only:request.explainOnly,permission_mode:tab.quickAgentPermissionMode})});if(!response.ok){let detail=response.statusText;try{detail=(await response.json()).detail||detail}catch{}throw new Error(detail)}if(!response.body)throw new Error('当前环境不支持流式响应');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {value:chunk,done}=await reader.read();buffer+=decoder.decode(chunk||new Uint8Array(),{stream:!done});const lines=buffer.split('\n');buffer=lines.pop()||'';for(const line of lines)if(line.trim())handleTerminalAgentEvent(tab,JSON.parse(line),progress);if(done)break}if(buffer.trim())handleTerminalAgentEvent(tab,JSON.parse(buffer),progress)}catch(err){if(err.name!=='AbortError')tab.term.writeln(`\r\n\x1b[31m[Agent] ${terminalText(err.message)}\x1b[0m`);else if(!progress.cancelled)tab.term.writeln('\r\n\x1b[33m[正在停止终端 Agent…]\x1b[0m')}finally{tab.quickAgentBusy=false;tab.quickAgentAbort=null;tab.quickAgentApproval=null;tab.quickApprovalBuffer='';renderAgentChat();tab.term.write('\r\n');rawTerminalInput(tab,'\r');tab.term.focus()}
}
function agentEntry(tab,entry){tab.agentChat.push(entry);renderAgentChat();return entry}
function agentProcessLabel(process){const seconds=Math.max(0,Math.round(((process.completedAt||Date.now())-process.startedAt)/1000));return `${process.status==='running'?'处理中':process.status==='failed'?'处理失败':'已处理'} ${seconds}s`}
function startAgentProcess(tab){
  if(tab.agentProcessTimer)clearInterval(tab.agentProcessTimer);const process={kind:'process',id:uid(),status:'running',startedAt:Date.now(),completedAt:null,open:true,items:[],currentText:null};tab.agentProcess=process;agentEntry(tab,process);
  tab.agentProcessTimer=setInterval(()=>{if(process.status!=='running')return;const label=$(`.agent-process[data-process-id="${process.id}"] .agent-process-label`);if(label)label.textContent=agentProcessLabel(process)},1000);return process
}
function completeAgentProcess(tab,status='done'){const process=tab.agentProcess;if(!process||process.status!=='running')return;process.status=status;process.completedAt=Date.now();process.open=false;if(process.currentThinking){process.currentThinking.status=status==='failed'?'failed':'done';process.currentThinking.text=status==='failed'?'思考中断':'思考完成';process.currentThinking=null}if(tab.agentProcessTimer){clearInterval(tab.agentProcessTimer);tab.agentProcessTimer=null}}
function renderAgentProcess(process){
  const root=document.createElement('section');root.className=`agent-process ${process.status}`;root.dataset.processId=process.id;const toggle=document.createElement('button');toggle.type='button';toggle.className='agent-process-toggle';toggle.setAttribute('aria-expanded',String(process.open));const label=document.createElement('span');label.className='agent-process-label';label.textContent=agentProcessLabel(process);const chevron=document.createElement('span');chevron.className='agent-process-chevron';chevron.setAttribute('aria-hidden','true');toggle.append(label,chevron);const body=document.createElement('div');body.className='agent-process-body';body.hidden=!process.open;
  for(const item of process.items){if(item.type==='text'){const note=document.createElement('div');note.className='agent-process-note';note.append(renderAgentChatMarkdown(item.text));body.append(note);continue}const row=document.createElement('div');row.className=`agent-activity ${item.status||''}${item.type==='command'?' agent-command':''}`;const label=document.createElement('span');label.className='agent-activity-label';label.textContent=item.text;label.title=item.text;row.append(label);if(item.output){const output=document.createElement('pre');output.className='agent-command-output';output.textContent=item.output;row.append(output)}body.append(row)}
  toggle.onclick=()=>{process.open=!process.open;body.hidden=!process.open;toggle.setAttribute('aria-expanded',String(process.open))};root.append(toggle,body);return root
}
function appendAgentMarkdownInline(parent,text){
  const pattern=/(`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_([^_\n]+)_|\[[^\]\n]+\]\([^)\n]+\))/g;let cursor=0,match;
  while((match=pattern.exec(text))){if(match.index>cursor)parent.append(document.createTextNode(text.slice(cursor,match.index)));const token=match[0];let node;if(token.startsWith('`')){node=document.createElement('code');node.textContent=token.slice(1,-1)}else if(token.startsWith('**')||token.startsWith('__')){node=document.createElement('strong');node.textContent=token.slice(2,-2)}else if(token.startsWith('*')||token.startsWith('_')){node=document.createElement('em');node.textContent=token.slice(1,-1)}else{const parts=token.match(/^\[([^\]]+)\]\(([^)]+)\)$/),url=parts?.[2]?.trim()||'';if(/^https?:\/\//i.test(url)){node=document.createElement('a');node.href=url;node.target='_blank';node.rel='noopener noreferrer';node.textContent=parts[1]}else node=document.createTextNode(token)}parent.append(node);cursor=match.index+token.length}if(cursor<text.length)parent.append(document.createTextNode(text.slice(cursor)))
}
function agentTableCells(line){let value=line.trim();if(!value.includes('|'))return null;if(value.startsWith('|'))value=value.slice(1);if(value.endsWith('|'))value=value.slice(0,-1);const cells=[];let cell='',escaped=false;for(const char of value){if(escaped){cell+=char;escaped=false;continue}if(char==='\\'){cell+=char;escaped=true;continue}if(char==='|'){cells.push(cell.trim());cell='';continue}cell+=char}cells.push(cell.trim());return cells}
function agentTableBlock(lines,index){const header=agentTableCells(lines[index]||''),alignment=agentTableCells(lines[index+1]||'');return header&&alignment&&alignment.length===header.length&&alignment.every(cell=>/^:?-{3,}:?$/.test(cell))?{header,alignment}:null}
function renderAgentChatMarkdown(value=''){
  const root=document.createElement('div');root.className='agent-markdown';const lines=String(value).replace(/\r\n?/g,'\n').split('\n');
  for(let i=0;i<lines.length;){const line=lines[i];if(!line.trim()){i++;continue}const fence=line.match(/^\s*```\s*([^ ]*)\s*$/);if(fence){const pre=document.createElement('pre'),code=document.createElement('code');code.dataset.language=fence[1]||'';const body=[];i++;while(i<lines.length&&!/^\s*```/.test(lines[i]))body.push(lines[i++]);if(i<lines.length)i++;code.textContent=body.join('\n');pre.append(code);root.append(pre);continue}const heading=line.match(/^\s*(#{1,4})\s+(.+)$/);if(heading){const el=document.createElement(`h${heading[1].length}`);appendAgentMarkdownInline(el,heading[2]);root.append(el);i++;continue}if(/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)){root.append(document.createElement('hr'));i++;continue}const tableBlock=agentTableBlock(lines,i);if(tableBlock){const {header,alignment}=tableBlock,table=document.createElement('table'),thead=document.createElement('thead'),headRow=document.createElement('tr');header.forEach((cell,index)=>{const th=document.createElement('th');th.style.textAlign=alignment[index].startsWith(':')&&alignment[index].endsWith(':')?'center':alignment[index].endsWith(':')?'right':'left';appendAgentMarkdownInline(th,cell);headRow.append(th)});thead.append(headRow);table.append(thead);const tbody=document.createElement('tbody');i+=2;while(i<lines.length){const cells=agentTableCells(lines[i]);if(!cells)break;const tr=document.createElement('tr');header.forEach((_,index)=>{const td=document.createElement('td');td.style.textAlign=headRow.childNodes[index].style.textAlign;appendAgentMarkdownInline(td,cells[index]||'');tr.append(td)});tbody.append(tr);i++}table.append(tbody);root.append(table);continue}const list=line.match(/^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/);if(list){const ordered=!!list[2],el=document.createElement(ordered?'ol':'ul');while(i<lines.length){const item=lines[i].match(/^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/);if(!item||!!item[2]!==ordered)break;const li=document.createElement('li');appendAgentMarkdownInline(li,item[3]);el.append(li);i++}root.append(el);continue}if(/^\s*>/.test(line)){const quote=document.createElement('blockquote');while(i<lines.length&&/^\s*>/.test(lines[i])){if(quote.childNodes.length)quote.append(document.createElement('br'));appendAgentMarkdownInline(quote,lines[i].replace(/^\s*>\s?/,''));i++}root.append(quote);continue}const paragraph=document.createElement('p'),paragraphStart=i;while(i<lines.length&&lines[i].trim()&&!/^\s*(?:```|#{1,4}\s|>|[-+*]\s+|\d+\.\s+)/.test(lines[i])&&!agentTableBlock(lines,i)){if(paragraph.childNodes.length)paragraph.append(document.createElement('br'));appendAgentMarkdownInline(paragraph,lines[i++])}if(i===paragraphStart){appendAgentMarkdownInline(paragraph,lines[i]);i++}root.append(paragraph)}return root
}
function renderAgentApproval(tab){
  const root=$('#agent-approval-popover'),approval=tab?.agentApproval;if(!approval){root.classList.add('hidden');return}root.classList.remove('hidden');$('#agent-approval-title').textContent=approval.title||'请求执行权限';$('#agent-approval-reason').textContent=approval.reason||'该命令可能造成不可逆变更';$('#agent-approval-command').textContent=approval.command||'';$('#agent-approval-yes').disabled=!!approval.submitting;$('#agent-approval-no').disabled=!!approval.submitting
}
function renderAgentPermissionMode(tab){
  const button=$('#agent-permission-mode'),full=state.sidebarAgentPermissionMode==='full_access';if(full||tab?.agentBusy)state.sidebarAgentPermissionPrompt=false;const confirming=state.sidebarAgentPermissionPrompt;button.textContent=full?'完全访问':'请求批准';button.classList.toggle('full-access',full);button.setAttribute('aria-pressed',String(full));button.setAttribute('aria-expanded',String(confirming));button.disabled=!!tab?.agentBusy;$('#agent-permission-confirm').classList.toggle('hidden',!confirming)
}
function answerAgentPermissionMode(enable){
  if(!state.sidebarAgentPermissionPrompt)return;state.sidebarAgentPermissionPrompt=false;if(enable){state.sidebarAgentPermissionMode='full_access';sessionStorage.setItem('coshell-sidebar-agent-permission-mode',state.sidebarAgentPermissionMode)}renderAgentChat();$('#agent-permission-mode').focus()
}
async function answerAgentApproval(approved){
  const tab=activeTab(),pending=tab?.agentApproval;if(!tab||!pending||pending.submitting)return;pending.submitting=true;renderAgentApproval(tab);try{await api('/api/agent/approval',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,approval_id:pending.approval_id,approved})});if(tab.agentApproval===pending)tab.agentApproval=null;renderAgentChat()}catch(err){if(tab.agentApproval===pending){pending.submitting=false;renderAgentApproval(tab)}toast(err.message,true)}
}
const AGENT_WELCOME_PROMPTS=[
  '帮我看看服务器现在运行得怎么样',
  '检查一下磁盘空间，找出占用最多的目录',
  '查询 GitHub 仓库文档并在服务器上部署',
  '帮我排查 Nginx 启动失败的原因',
  '分析最近的错误日志并给出修复建议',
  '检查网站是否正常，并定位访问变慢的原因',
  '帮我配置 HTTPS 证书和自动续期',
  '把本地配置文件上传到服务器并安全替换',
  '查找最新版 Docker 安装方法并完成安装',
  '整理这台服务器的安全风险和优化建议',
];
let agentWelcomePromptIndex=0,agentWelcomeTypewriterTimer=null;
function stopAgentWelcomeTypewriter(){if(agentWelcomeTypewriterTimer!==null){clearTimeout(agentWelcomeTypewriterTimer);agentWelcomeTypewriterTimer=null}}
function startAgentWelcomeTypewriter(){
  stopAgentWelcomeTypewriter();const target=$('#agent-welcome-prompt');if(!target)return;
  if(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches){target.textContent=AGENT_WELCOME_PROMPTS[agentWelcomePromptIndex];return}
  let length=0,deleting=false;
  const tick=()=>{if(!target.isConnected){agentWelcomeTypewriterTimer=null;return}const prompt=AGENT_WELCOME_PROMPTS[agentWelcomePromptIndex];length+=deleting?-1:1;target.textContent=prompt.slice(0,length);let delay=deleting?45:90;if(!deleting&&length===prompt.length){deleting=true;delay=1800}else if(deleting&&length===0){deleting=false;agentWelcomePromptIndex=(agentWelcomePromptIndex+1)%AGENT_WELCOME_PROMPTS.length;delay=320}agentWelcomeTypewriterTimer=setTimeout(tick,delay)};
  tick()
}
function renderAgentChat(){
  const tab=activeTab(),root=$('#agent-chat'),input=$('#agent-chat-input'),send=$('#agent-chat-send');stopAgentWelcomeTypewriter();root.replaceChildren();
  $('#agent-session-label').textContent=tab?`${tab.title} · ${tab.status==='connected'?'已连接':'未连接'}`:'未连接终端';
  if(!tab||!tab.agentChat.length){root.innerHTML='<div class="agent-welcome"><span>✦</span><strong>SSH Agent</strong><p class="agent-welcome-prompt" aria-hidden="true"><span id="agent-welcome-prompt"></span></p></div>';startAgentWelcomeTypewriter()}
  else for(const entry of tab.agentChat){if(entry.kind==='process'){root.append(renderAgentProcess(entry));continue}const el=document.createElement('div');el.className=`agent-message ${entry.role}${entry.error?' error':''}`;el.append(renderAgentChatMarkdown(entry.text));root.append(el)}
  const disconnected=!tab||tab.status!=='connected',busy=!!tab?.agentBusy;input.disabled=disconnected||busy;send.disabled=disconnected;send.classList.toggle('primary',!busy);send.classList.toggle('stop',busy);send.textContent=busy?'■':'↑';send.setAttribute('aria-label',busy?'停止当前任务':'发送');send.title=busy?'停止当前任务':'发送';renderAgentApproval(tab);renderAgentPermissionMode(tab);root.scrollTop=root.scrollHeight
}
function localAgentToolAction(event){if(event.tool==='workspace_list')return '列出本地目录';if(event.tool==='workspace_read')return '读取本地文件';if(event.tool==='workspace_write')return '写入本地文件';if(event.tool==='sftp_transfer')return event.direction==='download'?'下载文件':'上传文件';if(event.tool==='workspace_root_list')return '列出共享根目录';if(event.tool==='workspace_root_read')return '读取共享文件';if(event.tool==='workspace_root_write')return '写入共享文件';if(event.tool==='workspace_root_sftp_transfer')return event.direction==='download'?'下载到共享根目录':'从共享根目录上传';return '访问本地 workspace'}
function localAgentToolText(event,phase){const action=localAgentToolAction(event),label=String(event.label||'workspace');if(phase==='start')return `正在${action}：${label}`;if(!event.success)return `${action}失败：${label}${event.error?` · ${event.error}`:''}`;const detail=event.size!==null&&event.size!==undefined?` · ${formatSize(Number(event.size)||0)}`:event.entry_count!==null&&event.entry_count!==undefined?` · ${event.entry_count} 项`:'';return `${action}完成：${label}${detail}`}
function handleAgentChatEvent(tab,event){
  const process=tab.agentProcess;
  if(event.type==='thinking_start'){if(process&&!process.currentThinking){process.currentThinking={type:'thinking',status:'running',text:'正在思考'};process.items.push(process.currentThinking);renderAgentChat()}}
  else if(event.type==='command_approval_required'){tab.agentApproval={...event,source:'sidebar',submitting:false};renderAgentChat()}
  else if(event.type==='command_approval_resolved'){if(tab.agentApproval?.approval_id===event.approval_id)tab.agentApproval=null;renderAgentChat()}
  else if(event.type==='command_denied'){process?.items.push({type:'command',status:'failed',text:`已拒绝执行：${String(event.command||'').replace(/\s+/g,' ').trim()}`});renderAgentChat()}
  else if(event.type==='thinking_end'){if(process?.currentThinking){process.currentThinking.status='done';process.currentThinking.text='思考完成';process.currentThinking=null;renderAgentChat()}}
  else if(event.type==='command_start'){const command=String(event.command||'').replace(/\s+/g,' ').trim();tab.agentActivity={type:'command',command,status:'running',text:`正在执行命令：${command}`,output:''};process?.items.push(tab.agentActivity);if(process)process.currentText=null;renderAgentChat()}
  else if(event.type==='command_output'&&tab.agentActivity){tab.agentActivity.output=(tab.agentActivity.output+String(event.data||'')).slice(-8000);renderAgentChat()}
  else if(event.type==='command_end'&&tab.agentActivity){const success=Number(event.exit_code)===0;tab.agentActivity.status=success?'done':'failed';tab.agentActivity.text=`${success?'命令执行完成':'命令执行失败'}：${tab.agentActivity.command}`;renderAgentChat()}
  else if(event.type==='local_tool_prepare'){const key=event.id||`preparing:${event.tool}`,item={type:'tool',tool:event.tool,status:'running',text:event.tool==='workspace_write'?'正在编辑文件：生成内容中…':'正在准备本地操作…'};if(!tab.agentLocalActivities)tab.agentLocalActivities=new Map();tab.agentLocalActivities.set(key,item);process?.items.push(item);if(process)process.currentText=null;renderAgentChat()}
  else if(event.type==='local_tool_start'){const key=event.id||`${event.tool}:${event.label}`;if(!tab.agentLocalActivities)tab.agentLocalActivities=new Map();let item=tab.agentLocalActivities.get(key);if(item){item.text=localAgentToolText(event,'start')}else{item={type:'tool',tool:event.tool,status:'running',text:localAgentToolText(event,'start')};tab.agentLocalActivities.set(key,item);process?.items.push(item)}if(process)process.currentText=null;renderAgentChat()}
  else if(event.type==='local_tool_end'){const key=event.id||`${event.tool}:${event.label}`,item=tab.agentLocalActivities?.get(key);if(item){item.status=event.success?'done':'failed';item.text=localAgentToolText(event,'end');tab.agentLocalActivities.delete(key)}else process?.items.push({type:'tool',status:event.success?'done':'failed',text:localAgentToolText(event,'end')});renderAgentChat()}
  else if(event.type==='activity'){const names={search:'在线搜索',fetch:'读取网页',mcp:'调用 MCP',workspace:'访问本地 workspace',sftp:'SFTP 文件传输'};process?.items.push({type:'tool',status:'done',text:`${names[event.activity]||'执行工具'}：${event.label}`});renderAgentChat()}
  else if(event.type==='answer_delta'){if(process){if(!process.currentText){process.currentText={type:'text',text:''};process.items.push(process.currentText)}process.currentText.text+=event.delta||'';renderAgentChat()}}
  else if(event.type==='answer_cancel'){if(process)process.currentText=null;renderAgentChat()}
  else if(event.type==='answer'){const text=event.message+(event.limit_reached?'\n\n已达到本轮执行上限，可发送“继续”接着处理。':'');if(process?.currentText){const index=process.items.indexOf(process.currentText);if(index>=0)process.items.splice(index,1);process.currentText=null}completeAgentProcess(tab);agentEntry(tab,{kind:'message',role:'assistant',text})}
  else if(event.type==='error'){completeAgentProcess(tab,'failed');agentEntry(tab,{kind:'message',role:'assistant',error:true,text:event.message})}
  else if(event.type==='cancelled'){completeAgentProcess(tab,'failed');agentEntry(tab,{kind:'message',role:'assistant',text:event.message||'任务已停止'})}
  else if(event.type==='done'&&process?.status==='running'){completeAgentProcess(tab);renderAgentChat()}
}
async function runAgentChat(tab,message){
  if(!message.trim()||tab.agentBusy)return;tab.agentBusy=true;tab.agentAbortController=new AbortController();tab.agentActivity=null;tab.agentLocalActivities=new Map();tab.agentStreamingMessage=null;const pendingContext=tab.agentPendingContext;tab.agentPendingContext=null;agentEntry(tab,{kind:'message',role:'user',text:message.trim()});startAgentProcess(tab);renderAgentChat();
  try{const response=await fetch('/api/agent/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},signal:tab.agentAbortController.signal,body:JSON.stringify({session_id:tab.sessionId,message:message.trim(),terminal_context:pendingContext?.text||null,permission_mode:state.sidebarAgentPermissionMode})});if(!response.ok){let detail=response.statusText;try{detail=(await response.json()).detail||detail}catch{}throw new Error(detail)}if(!response.body)throw new Error('当前环境不支持流式响应');const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});const lines=buffer.split('\n');buffer=lines.pop()||'';for(const line of lines)if(line.trim())handleAgentChatEvent(tab,JSON.parse(line));if(done)break}if(buffer.trim())handleAgentChatEvent(tab,JSON.parse(buffer))}
  catch(err){if(err.name==='AbortError'){completeAgentProcess(tab,'failed');agentEntry(tab,{kind:'message',role:'assistant',text:'任务已停止'})}else{completeAgentProcess(tab,'failed');agentEntry(tab,{kind:'message',role:'assistant',error:true,text:err.message});toast(err.message,true)}}finally{tab.agentBusy=false;tab.agentAbortController=null;tab.agentActivity=null;tab.agentLocalActivities=null;tab.agentStreamingMessage=null;if(tab.agentApproval?.source==='sidebar')tab.agentApproval=null;if(tab.agentProcess?.status==='running')completeAgentProcess(tab);renderAgentChat()}
}
$('#agent-chat-form').onsubmit=e=>{e.preventDefault();const tab=activeTab(),input=$('#agent-chat-input'),message=input.value;if(!tab?.sessionId)return toast('请先连接并选择一个终端',true);if(tab.agentBusy){tab.agentAbortController?.abort();return}if(!message.trim())return;input.value='';runAgentChat(tab,message)};
$('#agent-chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#agent-chat-form').requestSubmit()}};
$('#agent-approval-yes').onclick=()=>answerAgentApproval(true);
$('#agent-approval-no').onclick=()=>answerAgentApproval(false);
$('#agent-permission-confirm-yes').onclick=()=>answerAgentPermissionMode(true);
$('#agent-permission-confirm-no').onclick=()=>answerAgentPermissionMode(false);
$('#agent-permission-mode').onclick=()=>{const tab=activeTab();if(tab?.agentBusy)return;const full=state.sidebarAgentPermissionMode==='full_access';if(full){state.sidebarAgentPermissionMode='request_approval';state.sidebarAgentPermissionPrompt=false;sessionStorage.setItem('coshell-sidebar-agent-permission-mode',state.sidebarAgentPermissionMode);renderAgentChat();return}state.sidebarAgentPermissionPrompt=!state.sidebarAgentPermissionPrompt;renderAgentChat();if(state.sidebarAgentPermissionPrompt)$('#agent-permission-confirm-no').focus()};
$('#agent-new-chat').onclick=async()=>{const tab=activeTab();if(!tab||tab.agentBusy)return;if(tab.sessionId)try{await api('/api/agent/chat/reset',{method:'POST',body:JSON.stringify({session_id:tab.sessionId})})}catch(err){return toast(err.message,true)}tab.agentChat=[];tab.agentPendingContext=null;renderAgentChat();toast('已新建 Agent 对话')};
$('#agent-attach-terminal').onclick=()=>{const tab=activeTab();if(!tab?.sessionId)return toast('请先连接并选择一个终端',true);tab.agentPendingContext=recentTerminalContext(tab);renderAgentChat();toast(`已附加${tab.agentPendingContext.source==='selection'?'选中内容':'终端内容'} ${tab.agentPendingContext.lineCount} 行到上下文`)};
$('#agent-chat').addEventListener('contextmenu',event=>{
  const root=$('#agent-chat'),selection=window.getSelection(),insideSelection=selection&&!selection.isCollapsed&&root.contains(selection.anchorNode)&&root.contains(selection.focusNode),selected=insideSelection?selection.toString():'';
  const message=event.target.closest('.agent-message'),messageText=message?.innerText?.trim()||'';
  if(!selected&&!messageText)return;
  showContextMenu(event,[
    {label:selected?'复制选中内容':'复制此消息',run:async()=>{await writeClipboard(selected||messageText);toast('聊天内容已复制')}},
    ...(selected&&messageText&&selected.trim()!==messageText?[{label:'复制整条消息',run:async()=>{await writeClipboard(messageText);toast('整条消息已复制')}}]:[]),
  ])
});
function handleAgentTerminalInput(tab,data){
  if(tab.quickAgentBusy){if(tab.quickAgentApproval)return handleTerminalAgentApprovalInput(tab,data);if(data.includes('\x03')){tab.quickAgentAbort?.abort();return}if(!tab.agentBusyNotice){tab.term.writeln('\r\n\x1b[33m终端 Agent 正在处置，按 Ctrl+C 停止…\x1b[0m');tab.agentBusyNotice=true}return}
  tab.agentBusyNotice=false;
  if(tab.agentAutocompleteOpen&&handleTerminalAgentAutocompleteKey(tab,data))return;
  if(tab.agentBuffer===null&&data.includes('\x1b')){rawTerminalInput(tab,data);tab.typedLine='?';return}
  for(const char of data){
    if(tab.agentBuffer!==null){
      if(char==='\x7f'||char==='\b'){hideTerminalAgentAutocomplete(tab);if(tab.agentBuffer)tab.agentBuffer=eraseLastLocalCharacter(tab.term,tab.agentBuffer);if(!tab.agentBuffer)tab.agentBuffer=null;continue}
      if(char==='\r'||char==='\n'){
        const captured=tab.agentBuffer;hideTerminalAgentAutocomplete(tab);tab.agentBuffer=null;tab.typedLine='';
        if(captured==='/agent'||captured.startsWith('/agent ')){const context=recentTerminalContext(tab,captured);tab.term.write('\r\n');runAgent(tab,captured.slice(6).trim(),context)}else{eraseLocalInput(tab.term,captured);tab.lastCommandStart=Math.max(0,tab.term.buffer.active.baseY+tab.term.buffer.active.cursorY-Math.floor(terminalCellWidth(captured)/Math.max(1,tab.term.cols)));tab.lastCommand=captured;rawTerminalInput(tab,captured+'\r')}continue
      }
      if(char>=' '){tab.agentBuffer+=char;const showCompletions=tab.agentBuffer==='/agent ';tab.term.write(char,showCompletions?()=>showTerminalAgentAutocomplete(tab):undefined);if(!showCompletions)hideTerminalAgentAutocomplete(tab);const valid='/agent'.startsWith(tab.agentBuffer)||tab.agentBuffer==='/agent'||tab.agentBuffer.startsWith('/agent ');if(!valid){const buffered=tab.agentBuffer;hideTerminalAgentAutocomplete(tab);eraseLocalInput(tab.term,buffered);tab.agentBuffer=null;tab.typedLine=buffered;rawTerminalInput(tab,buffered)}}
      else{const buffered=tab.agentBuffer;hideTerminalAgentAutocomplete(tab);eraseLocalInput(tab.term,buffered);tab.agentBuffer=null;tab.typedLine=buffered;rawTerminalInput(tab,buffered+char)}
      continue
    }
    if(char==='/'&&!tab.typedLine&&tab.term.buffer.active.type!=='alternate'){tab.agentBuffer='/';tab.term.write('/');continue}
    if(char==='\r'||char==='\n'){tab.lastCommandStart=Math.max(0,tab.term.buffer.active.baseY+tab.term.buffer.active.cursorY-Math.floor(terminalCellWidth(tab.typedLine)/Math.max(1,tab.term.cols)));tab.lastCommand=tab.typedLine}
    rawTerminalInput(tab,char);
    if(char==='\r'||char==='\n'||char==='\x03'||char==='\x15')tab.typedLine='';else if(char==='\x7f'||char==='\b')tab.typedLine=[...tab.typedLine].slice(0,-1).join('');else if(char>=' ')tab.typedLine+=char
  }
}
function sendTerminalInput(tab,data){if(tab.ws?.readyState===WebSocket.OPEN&&tab.status==='connected'){handleAgentTerminalInput(tab,data);return}if(tab.status==='idle'||tab.status==='error')handleLocalTerminalInput(tab,data)}
function terminalAppendRows(term){const buffer=term.buffer.active,cursorRow=buffer.baseY+buffer.cursorY;let contentRow=-1;for(let row=buffer.length-1;row>=0;row--){if(buffer.getLine(row)?.translateToString(true)){contentRow=row;break}}return Math.max(1,contentRow-cursorRow+1)}
function writeAtTerminalBottom(tab,data){tab.term.write('\x1b[?1049l',()=>{const rows=terminalAppendRows(tab.term);tab.term.write(`\x1b[0m${'\r\n'.repeat(rows)}\x1b[2K\r${data}`,()=>tab.term.scrollToBottom())});requestAnimationFrame(()=>tab.term.scrollToBottom())}
function showReconnectPrompt(tab,message='连接已断开'){if(tab.localPromptShown)return;tab.localPromptShown=true;tab.localInput='';tab.sessionId=null;writeAtTerminalBottom(tab,`\x1b[90m[${terminalText(message)}，输入 /reconnect 重新连接]\x1b[0m\r\n\x1b[36mlocal> \x1b[0m`)}
function handleLocalTerminalInput(tab,data){if(data.includes('\x1b'))return;data=data.replace(/\r\n/g,'\r');for(const char of data){if(char==='\r'||char==='\n'){const command=(tab.localInput||'').trim();tab.term.write('\r\n');tab.localInput='';if(command==='/reconnect'){reconnectTab(tab);return}if(command)tab.term.writeln('\x1b[33m离线状态仅支持 /reconnect\x1b[0m');tab.term.write('\x1b[36mlocal> \x1b[0m')}else if(char==='\x7f'||char==='\b'){if(tab.localInput){tab.localInput=[...tab.localInput].slice(0,-1).join('');tab.term.write('\b \b')}}else if(char>=' '){tab.localInput=(tab.localInput||'')+char;tab.term.write(char)}}}
function reconnectTab(tab){if(tab.status==='connecting'||tab.status==='connected')return;if(tab.server_id){if(!state.vault?.vault_unlocked){state.pendingReconnect=tab;openVault();return}connectTab(tab,{server_id:tab.server_id})}else openConnect({name:tab.title},tab)}
function syncTerminalSize(tab,cols=tab?.term.cols,rows=tab?.term.rows){if(tab?.ws?.readyState===WebSocket.OPEN&&tab.status==='connected')tab.ws.send(JSON.stringify({type:'resize',cols,rows}))}
function fitTerminal(tab,focus=false){if(!tab||tab.id!==state.activeId||!tab.host.classList.contains('active'))return;const rect=tab.host.getBoundingClientRect();if(rect.width<20||rect.height<20)return;try{tab.fit.fit();syncTerminalSize(tab);tab.term.refresh(0,Math.max(0,tab.term.rows-1));if(focus)tab.term.focus()}catch{}}
function scheduleTerminalFit(tab,focus=false){requestAnimationFrame(()=>{fitTerminal(tab,focus);setTimeout(()=>fitTerminal(tab,focus),60);setTimeout(()=>fitTerminal(tab,false),250)})}

const terminalThemes={
  dark:{background:'#1d1325',foreground:'#f8f1f7',cursor:'#f08a62',selectionBackground:'#b75c7a66',black:'#30203a',red:'#ff7184',green:'#73d6a2',yellow:'#efc46b',blue:'#8da6ed',magenta:'#d99aef',cyan:'#79d0d1',white:'#f8f1f7'},
  light:{background:'#fffaf0',foreground:'#42372f',cursor:'#c96643',selectionBackground:'#dfa88166',black:'#42372f',red:'#c94f5f',green:'#4c8b69',yellow:'#a87826',blue:'#587cb5',magenta:'#8d6198',cyan:'#397f83',white:'#fffaf0'},
  fresh:{background:'#fbfbed',foreground:'#293426',cursor:'#65a653',selectionBackground:'#82b97055',black:'#293426',red:'#c85d63',green:'#4f9954',yellow:'#a7812c',blue:'#54809b',magenta:'#9a6b96',cyan:'#438b84',white:'#fffdf4'},
  ocean:{background:'#f4f9fa',foreground:'#26383d',cursor:'#438b9a',selectionBackground:'#67a8b455',black:'#26383d',red:'#c45d68',green:'#459172',yellow:'#a77e30',blue:'#3e789d',magenta:'#8d6d9b',cyan:'#368b91',white:'#f9fcfc'},
  midnight:{background:'#0c1522',foreground:'#edf5f7',cursor:'#63c7c6',selectionBackground:'#4e95ad66',black:'#172438',red:'#ff7885',green:'#6ed3a5',yellow:'#e8bd68',blue:'#76a9e8',magenta:'#bb8ddd',cyan:'#63c7c6',white:'#edf5f7'}
};
const themeModes={dark:'dark',light:'light',fresh:'light',ocean:'light',midnight:'dark'};
function currentTheme(){ return document.documentElement.dataset.theme; }
function applyTheme(theme, persist=true){
  if(!terminalThemes[theme])theme='dark';
  document.documentElement.dataset.theme=theme;
  document.documentElement.dataset.themeMode=themeModes[theme];
  $$('.theme-card').forEach(card=>{const selected=card.dataset.themeChoice===theme;card.classList.toggle('selected',selected);card.setAttribute('aria-pressed',String(selected))});
  state.tabs.forEach(t=>{if(t.term)t.term.options.theme=terminalThemes[theme]});
  if(state.editor.cm)state.editor.cm.setOption('theme',themeModes[theme]==='dark'?'material-darker':'default');
  if(persist){ localStorage.setItem('webssh-theme',theme); api('/api/settings/theme',{method:'PUT',body:JSON.stringify({value:theme})}).catch(()=>{}); }
}
async function restoreTheme(){
  try{const saved=await api('/api/settings/theme');if(terminalThemes[saved.value])applyTheme(saved.value,false)}catch{}
}

async function refreshStatus(){
  state.vault=await api('/api/status');
  $('#vault-help').textContent=state.vault.vault_initialized?'输入主密码即可使用保存的服务器凭据。':'首次使用，请设置至少 8 位的主密码。';
  const initialized=state.vault.vault_initialized,unlocked=state.vault.vault_unlocked;$('#settings-vault-status').textContent=!initialized?'尚未初始化':unlocked?'保险库已解锁':'保险库已锁定';$('#settings-vault-indicator').textContent=unlocked?'已解锁':'已锁定';$('#settings-vault-indicator').classList.toggle('good',unlocked);$('#settings-vault-lock').disabled=!unlocked;$('#settings-vault-unlock').textContent=initialized?'解锁':'初始化';
}
function openVault(){ $('#vault-form').reset(); $('#vault-dialog').showModal(); }
$('#vault-form').addEventListener('submit',async e=>{e.preventDefault();const form=new FormData(e.target),password=form.get('password');try{await api(state.vault.vault_initialized?'/api/vault/unlock':'/api/vault/initialize',{method:'POST',body:JSON.stringify({password})});if(form.get('remember'))await api('/api/vault/remember',{method:'POST',body:JSON.stringify({password})});else await api('/api/vault/remember',{method:'DELETE'});localStorage.removeItem('webssh-vault-password');sessionStorage.removeItem('webssh-vault-password');$('#vault-dialog').close();await refreshStatus();toast('保险库已解锁');if(state.pendingReconnect){const tab=state.pendingReconnect;state.pendingReconnect=null;reconnectTab(tab)}}catch(err){toast(err.message,true)}});
async function loadAgentSettings(){state.agentSettings=await api('/api/agent/settings');const f=$('#agent-settings-form');f.elements.api_url.value=state.agentSettings.api_url;f.elements.api_key.value='';f.elements.builtin_web_search.checked=state.agentSettings.builtin_web_search!==false;$('#agent-key-status').textContent=state.agentSettings.api_key_configured?'已保存加密密钥；留空不会覆盖。':'尚未保存密钥；无鉴权的兼容接口可以留空。';setModelOptions([],state.agentSettings.model)}
function toggleCustomModel(){const f=$('#agent-settings-form'),custom=f.elements.model_custom,isCustom=f.elements.model.value==='__custom__';custom.classList.toggle('hidden',!isCustom);custom.required=isCustom;if(isCustom)custom.focus()}
function setModelOptions(models,selected=''){const f=$('#agent-settings-form'),select=f.elements.model,current=selected||select.value;select.replaceChildren();const values=[...new Set([current,...models].filter(value=>value&&value!=='__custom__'))];if(!values.length)select.add(new Option('请先获取模型列表',''));values.forEach(value=>select.add(new Option(value,value)));select.add(new Option('手动输入模型 ID…','__custom__'));select.value=values.includes(current)?current:(values[0]||'');toggleCustomModel()}
async function openSettings(){try{await Promise.all([refreshStatus(),loadAgentSettings(),loadSSHKeys(),loadMCPServers()]);$('#settings-dialog').showModal()}catch(err){toast(err.message,true)}}
$('#settings-btn').onclick=openSettings;
$$('.theme-card').forEach(card=>card.onclick=()=>applyTheme(card.dataset.themeChoice));
$$('.settings-tab').forEach(btn=>btn.onclick=()=>{$$('.settings-tab').forEach(x=>x.classList.toggle('active',x===btn));$$('.settings-panel').forEach(x=>x.classList.toggle('active',x.id===`settings-${btn.dataset.settingsPanel}`||x.id===`${btn.dataset.settingsPanel}-settings-form`));if(btn.dataset.settingsPanel==='agent')refreshAgentModels(false)});
$('#settings-vault-unlock').onclick=openVault;
$('#settings-vault-lock').onclick=async()=>{localStorage.removeItem('webssh-vault-password');sessionStorage.removeItem('webssh-vault-password');await api('/api/vault/lock',{method:'POST'});await refreshStatus();toast('保险库已锁定')};
$('#backup-download').onclick=async()=>{const button=$('#backup-download');button.disabled=true;button.textContent='正在生成…';try{const response=await api('/api/backup'),blob=await response.blob(),disposition=response.headers.get('content-disposition')||'',match=disposition.match(/filename="([^"]+)"/),link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download=match?.[1]||'light-ssh-terminal-backup.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);toast('备份已下载')}catch(err){toast(err.message,true)}finally{button.disabled=false;button.textContent='下载备份'}};
$('#backup-restore').onclick=async()=>{const file=$('#backup-file').files[0];if(!file)return toast('请先选择备份文件',true);if(!await themedConfirm('还原会整体替换当前连接、配置和加密凭据，并断开现有 SSH 连接。此操作无法撤销。',{title:'确认还原备份',confirmText:'还原并替换',danger:true}))return;const button=$('#backup-restore');button.disabled=true;button.textContent='正在还原…';try{const form=new FormData();form.append('file',file);await api('/api/restore',{method:'POST',body:form});localStorage.removeItem('webssh-vault-password');sessionStorage.removeItem('webssh-vault-password');toast('还原完成，正在重新加载…');setTimeout(()=>location.reload(),600)}catch(err){toast(err.message,true);button.disabled=false;button.textContent='还原并替换当前数据'}};
async function refreshAgentModels(showToast=true){const f=$('#agent-settings-form'),button=$('#agent-models-refresh'),status=$('#agent-models-status');if(button.disabled)return;button.disabled=true;button.textContent='获取中…';status.textContent='正在连接模型接口…';status.classList.remove('error-text');try{const selected=f.elements.model.value,data=await api('/api/agent/models',{method:'POST',body:JSON.stringify({api_url:f.elements.api_url.value,api_key:f.elements.api_key.value||null})});f.elements.api_url.value=data.api_url||f.elements.api_url.value;setModelOptions(data.models,selected||data.models[0]);status.textContent=`已获取 ${data.models.length} 个模型，API 基础地址：${data.api_url}`;if(showToast)toast(`已获取 ${data.models.length} 个模型`)}catch(err){status.textContent=err.message;status.classList.add('error-text');if(showToast)toast(err.message,true)}finally{button.disabled=false;button.textContent='获取模型'}}
$('#agent-models-refresh').onclick=()=>refreshAgentModels();
$('#agent-settings-form').elements.api_key.onchange=()=>refreshAgentModels(false);
$('#agent-settings-form').elements.model.onchange=toggleCustomModel;
$('#agent-settings-form').onsubmit=async e=>{e.preventDefault();const f=e.target,model=f.elements.model.value==='__custom__'?f.elements.model_custom.value.trim():f.elements.model.value;if(!model)return toast('请选择或输入模型 ID',true);try{await api('/api/agent/settings',{method:'PUT',body:JSON.stringify({api_url:f.elements.api_url.value,api_key:f.elements.api_key.value||null,model,builtin_web_search:f.elements.builtin_web_search.checked})});await loadAgentSettings();toast('Agent 设置已保存')}catch(err){toast(err.message,true)}};

async function loadSSHKeys(){state.sshKeys=await api('/api/ssh-keys');renderSSHKeys();updateSSHKeyOptions()}
function renderSSHKeys(){const root=$('#ssh-key-list');root.replaceChildren();for(const key of state.sshKeys){const row=document.createElement('div');row.className='settings-list-item';row.innerHTML=`<div class="settings-list-main"><strong>${esc(key.name)}</strong><small>${esc(key.key_type)} · ${esc(key.fingerprint)}</small></div><div class="settings-list-actions"><button class="danger">删除</button></div>`;$('.danger',row).onclick=async()=>{if(!await themedConfirm(`删除密钥“${key.name}”？使用它的已保存连接将不再关联此密钥。`))return;await api(`/api/ssh-keys/${key.id}`,{method:'DELETE'});await Promise.all([loadSSHKeys(),loadServers()]);toast('密钥已删除')};root.append(row)}if(!root.children.length)root.innerHTML='<div class="empty">尚未导入 SSH 私钥</div>'}
function updateSSHKeyOptions(){const select=$('#connect-form').elements.ssh_key_id,current=select.value;select.replaceChildren(new Option('手动粘贴私钥',''));for(const key of state.sshKeys)select.add(new Option(`${key.name} · ${key.key_type}`,String(key.id)));select.value=[...select.options].some(x=>x.value===current)?current:'';authFields()}
function closeSSHKeyForms(){for(const form of [$('#ssh-key-generate-form'),$('#ssh-key-import-form')]){form.reset();form.classList.add('hidden')}$('#ssh-key-rsa-bits').classList.add('hidden')}
$('#ssh-key-generate-toggle').onclick=()=>{closeSSHKeyForms();$('#ssh-key-generate-form').classList.remove('hidden')};
$('#ssh-key-import-toggle').onclick=()=>{closeSSHKeyForms();$('#ssh-key-import-form').classList.remove('hidden')};
$('#ssh-key-generate-cancel').onclick=closeSSHKeyForms;
$('#ssh-key-import-cancel').onclick=()=>{$('#ssh-key-import-form').reset();$('#ssh-key-import-form').classList.add('hidden')};
$('#ssh-key-generate-form').elements.key_type.onchange=e=>$('#ssh-key-rsa-bits').classList.toggle('hidden',e.target.value!=='rsa');
$('#ssh-key-generate-form').onsubmit=async e=>{e.preventDefault();const f=e.target,button=$('button[type=submit]',f);button.disabled=true;button.textContent='生成中…';try{const result=await api('/api/ssh-keys/generate',{method:'POST',body:JSON.stringify({name:f.elements.name.value,file_name:f.elements.file_name.value,key_type:f.elements.key_type.value,rsa_bits:Number(f.elements.rsa_bits.value),passphrase:f.elements.passphrase.value||null,auto_import:f.elements.auto_import.checked})});closeSSHKeyForms();if(result.imported)await loadSSHKeys();toast(`密钥对已保存到 ${result.private_key_path}${result.imported?'，并已导入密码库':''}`)}catch(err){if(err.message.includes('解锁'))openVault();toast(err.message,true)}finally{button.disabled=false;button.textContent='生成密钥对'}};
$('#ssh-key-import-form').onsubmit=async e=>{e.preventDefault();const f=e.target,file=f.elements.key_file.files[0];if(!file)return toast('请选择私钥文件',true);try{const private_key=await file.text();await api('/api/ssh-keys',{method:'POST',body:JSON.stringify({name:f.elements.name.value,private_key,passphrase:f.elements.passphrase.value||null})});f.reset();f.classList.add('hidden');await loadSSHKeys();toast('私钥已加密导入')}catch(err){if(err.message.includes('解锁'))openVault();toast(err.message,true)}};

async function loadMCPServers(){state.mcpServers=await api('/api/mcp/servers');renderMCPServers()}
function renderMCPServers(){const root=$('#mcp-server-list');root.replaceChildren();for(const server of state.mcpServers){const row=document.createElement('div');row.className='settings-list-item';row.innerHTML=`<div class="settings-list-main"><strong>${esc(server.name)}</strong><small>${esc(server.search_tools.join(', ')||'未发现搜索工具')} · ${esc(server.url)}</small></div><div class="settings-list-actions"><label class="check"><input type="checkbox" ${server.enabled?'checked':''}>启用</label><button class="refresh">刷新</button><button class="danger">卸载</button></div>`;const enabled=$('input',row);enabled.onchange=async()=>{try{await api(`/api/mcp/servers/${server.id}/enabled`,{method:'PUT',body:JSON.stringify({enabled:enabled.checked})});server.enabled=enabled.checked;toast(enabled.checked?'MCP 已启用':'MCP 已关闭')}catch(err){enabled.checked=!enabled.checked;toast(err.message,true)}};$('.refresh',row).onclick=async()=>{try{await api(`/api/mcp/servers/${server.id}/refresh`,{method:'POST'});await loadMCPServers();toast('MCP 工具列表已刷新')}catch(err){toast(err.message,true)}};$('.danger',row).onclick=async()=>{if(!await themedConfirm(`卸载 MCP 服务“${server.name}”？`))return;await api(`/api/mcp/servers/${server.id}`,{method:'DELETE'});await loadMCPServers();toast('MCP 已卸载')};root.append(row)}if(!root.children.length)root.innerHTML='<div class="empty">尚未安装 MCP 搜索服务</div>'}
$('#mcp-install-toggle').onclick=()=>$('#mcp-install-form').classList.remove('hidden');
$('#mcp-install-cancel').onclick=()=>{$('#mcp-install-form').classList.add('hidden')};
$('#mcp-install-submit').onclick=async()=>{const root=$('#mcp-install-form'),name=$('[name=mcp_name]',root).value.trim(),url=$('[name=mcp_url]',root).value.trim(),auth_token=$('[name=mcp_token]',root).value;if(!name||!url)return toast('请填写 MCP 名称和地址',true);const button=$('#mcp-install-submit');button.disabled=true;button.textContent='连接中…';try{await api('/api/mcp/servers',{method:'POST',body:JSON.stringify({name,url,auth_token:auth_token||null})});$$('input',root).forEach(x=>x.value='');root.classList.add('hidden');await loadMCPServers();toast('MCP 搜索服务已安装并启用')}catch(err){toast(err.message,true)}finally{button.disabled=false;button.textContent='连接并安装'}};
async function autoUnlockVault(){
  if(!state.vault?.vault_initialized||state.vault.vault_unlocked)return;
  try{const result=await api('/api/vault/auto-unlock',{method:'POST'});if(result.unlocked){await refreshStatus();return}}catch{}
  // One-time migration from older builds that stored the password in WebView localStorage.
  const password=localStorage.getItem('webssh-vault-password')||sessionStorage.getItem('webssh-vault-password');
  if(!password)return;
  try{await api('/api/vault/unlock',{method:'POST',body:JSON.stringify({password})});await api('/api/vault/remember',{method:'POST',body:JSON.stringify({password})});await refreshStatus()}catch{}
  finally{localStorage.removeItem('webssh-vault-password');sessionStorage.removeItem('webssh-vault-password')}
}
$$('.dialog-close').forEach(x=>x.addEventListener('click',()=>x.closest('dialog').close()));

function newTerminal(tabData={}){
  const id=tabData.id||uid(); if(state.tabs.some(t=>t.id===id))return;
  const host=document.createElement('div'); host.className='terminal-host'; host.dataset.id=id; $('#terminals').append(host);
  const term=new Terminal({cursorBlink:true,fontSize:14,fontFamily:'Cascadia Mono, Sarasa Mono SC, Noto Sans Mono CJK SC, Microsoft YaHei UI, Consolas, monospace',letterSpacing:0,lineHeight:1.08,scrollback:6000,theme:terminalThemes[currentTheme()],allowProposedApi:true});
  const fit=new FitAddon.FitAddon(); term.loadAddon(fit); term.open(host);
  const tab={id,title:tabData.title||'新终端',server_id:tabData.server_id??null,last_path:tabData.last_path||'.',position:state.tabs.length,status:'idle',term,fit,host,ws:null,sessionId:null,localInput:'',localPromptShown:false,typedLine:'',lastCommand:'',lastCommandStart:null,agentBuffer:null,agentAutocomplete:null,agentAutocompleteOpen:false,agentAutocompleteIndex:0,agentBusy:false,agentBusyNotice:false,agentAbortController:null,agentPendingContext:null,agentApproval:null,agentChat:[],agentActivity:null,agentStreamingMessage:null,agentProcess:null,agentProcessTimer:null,quickAgentBusy:false,quickAgentAbort:null,quickAgentApproval:null,quickApprovalBuffer:'',quickAgentPermissionMode:'request_approval',quickIncidentCommandStart:null};
  createTerminalAgentAutocomplete(tab);
  term.writeln('\x1b[38;5;111mCoShell\x1b[0m — 点击连接开始会话\r\n');
  // xterm.js owns key-to-sequence conversion so application cursor/keypad modes
  // selected by ncurses programs are preserved and each key is emitted once.
  term.attachCustomKeyEventHandler(event=>{
    if(event.type!=='keydown'||!event.ctrlKey||event.altKey||event.metaKey)return true;
    const key=event.key.toLowerCase();
    if(key==='c'&&term.hasSelection()){writeClipboard(term.getSelection()).then(()=>toast('已复制终端内容')).catch(err=>toast(err.message,true));return false}
    if(key==='v'){readClipboard().then(text=>{if(text)term.paste(text)}).catch(err=>toast(err.message,true));return false}
    return true;
  });
  term.onData(data=>sendTerminalInput(tab,data));
  term.onResize(({cols,rows})=>{syncTerminalSize(tab,cols,rows);if(tab.agentAutocompleteOpen)positionTerminalAgentAutocomplete(tab)});
  host.addEventListener('pointerdown',event=>{if(tab.agentAutocompleteOpen&&!tab.agentAutocomplete.contains(event.target))hideTerminalAgentAutocomplete(tab)});
  host.addEventListener('contextmenu',event=>showTerminalMenu(event,tab));
  state.tabs.push(tab); renderTabs(); activateTab(id); saveTabs(); return tab;
}
function activateTab(id){
  if(id)hideHostManager();
  state.tabs.forEach(tab=>{if(tab.id!==id)hideTerminalAgentAutocomplete(tab)});
  state.activeId=id; $$('.terminal-host').forEach(x=>x.classList.toggle('active',x.dataset.id===id));
  $$('.terminal-tab').forEach(x=>x.classList.toggle('active',x.dataset.id===id));
  $('#welcome').classList.toggle('hidden',!!id); const tab=activeTab();
  if(tab){scheduleTerminalFit(tab,true);if(tab.agentBuffer==='/agent ')requestAnimationFrame(()=>showTerminalAgentAutocomplete(tab));$('#sftp-path').value=tab.last_path||'.'; loadSftp();}
  renderAgentChat();
  loadSystemInfo();
}
function renderTabs(){
  const root=$('#terminal-tabs'); root.replaceChildren();
  state.tabs.forEach(tab=>{const el=document.createElement('div');el.className='terminal-tab'+(tab.id===state.activeId?' active':'');el.dataset.id=tab.id;el.draggable=true;
    const dot=document.createElement('span');dot.className='status-dot '+tab.status;const title=document.createElement('span');title.className='tab-title';title.textContent=tab.title;title.title='双击重命名';const close=document.createElement('button');close.className='tab-close';close.textContent='×';
    el.append(dot,title,close);el.onclick=()=>activateTab(tab.id);el.oncontextmenu=async e=>{e.preventDefault();const action=await themedInput('标签操作：reconnect / rename / close');if(action==='reconnect'){activateTab(tab.id);tab.server_id?connectTab(tab,{server_id:tab.server_id}):openConnect({name:tab.title})}else if(action==='rename'){const name=await themedInput('标签名称',tab.title);if(name?.trim()){tab.title=name.trim();renderTabs();saveTabs()}}else if(action==='close')closeTab(tab)};title.ondblclick=async e=>{e.stopPropagation();const name=await themedInput('标签名称',tab.title);if(name?.trim()){tab.title=name.trim();renderTabs();saveTabs()}};close.onclick=e=>{e.stopPropagation();closeTab(tab)};
    el.ondragstart=()=>el.classList.add('dragging');el.ondragend=()=>el.classList.remove('dragging');el.ondragover=e=>e.preventDefault();el.ondrop=e=>{e.preventDefault();const from=$('.terminal-tab.dragging');if(!from||from===el)return;const a=state.tabs.findIndex(t=>t.id===from.dataset.id),b=state.tabs.findIndex(t=>t.id===tab.id);const [m]=state.tabs.splice(a,1);state.tabs.splice(b,0,m);renderTabs();saveTabs()};root.append(el);
  }); updateCount();
}
async function closeTab(tab){
  if(tab.status==='connected'&&!await themedConfirm(`关闭 ${tab.title} 并断开 SSH？`))return;
  if(tab.agentProcessTimer)clearInterval(tab.agentProcessTimer);tab.agentAbortController?.abort();tab.quickAgentAbort?.abort();
  if(tab.ws?.readyState===WebSocket.OPEN){tab.ws.send(JSON.stringify({type:'close'}));tab.ws.close()}
  tab.term.dispose();tab.host.remove();const i=state.tabs.indexOf(tab);state.tabs.splice(i,1);if(state.activeId===tab.id)state.activeId=state.tabs[Math.max(0,i-1)]?.id||null;renderTabs();activateTab(state.activeId);saveTabs();
}
function updateCount(){const n=state.tabs.filter(t=>t.status==='connected').length;$('#connection-count').textContent=`${n} 个连接`}
let saveTimer;function saveTabs(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>api('/api/tabs',{method:'PUT',body:JSON.stringify(state.tabs.map((t,i)=>({id:t.id,title:t.title,server_id:t.server_id,position:i,last_path:t.last_path||'.'})))}).catch(()=>{}),250)}

function openConnect(prefill={},reuseTab=null){
  const f=$('#connect-form'),saveOnly=!!prefill.saveOnly;f.reset();f.dataset.reuseTabId=reuseTab?.id||'';f.dataset.saveOnly=String(saveOnly);f.elements.port.value=prefill.port||22;Object.entries(prefill).forEach(([k,v])=>{if(f.elements[k]&&v!=null)f.elements[k].value=v});
  updateSSHKeyOptions();
  f.elements.save.checked=!!prefill.forceSave||saveOnly;$('#connect-save-field').classList.toggle('hidden',saveOnly);$('#connect-dialog-title').textContent=saveOnly?'新建主机':'新建 SSH 连接';$('#connect-submit').textContent=saveOnly?'保存主机':'连接';authFields(); $('#connect-dialog').showModal();
}
function authFields(){const f=$('#connect-form'),key=f.elements.auth_type.value==='private_key',saved=!!f.elements.ssh_key_id.value;$$('.key-field').forEach(x=>x.classList.toggle('hidden',!key));$$('.manual-key-field').forEach(x=>x.classList.toggle('hidden',!key||saved));$('.password-field').classList.toggle('hidden',key)}
$('#connect-form').elements.auth_type.addEventListener('change',authFields);
$('#connect-form').elements.ssh_key_id.addEventListener('change',authFields);
$('#connect-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,d=Object.fromEntries(new FormData(f));let serverId=d.server_id?Number(d.server_id):null;
  const payload={name:d.name||d.host,host:d.host,port:Number(d.port),username:d.username,auth_type:d.auth_type,password:d.password||null,private_key:d.private_key||null,passphrase:d.passphrase||null,ssh_key_id:d.ssh_key_id?Number(d.ssh_key_id):null,note:''};
  try{if(d.save||f.dataset.saveOnly==='true'){const saved=await api('/api/servers',{method:'POST',body:JSON.stringify(payload)});serverId=saved.id;await loadServers()}
    if(f.dataset.saveOnly==='true'){$('#connect-dialog').close();toast('主机已保存');return}
    const reused=state.tabs.find(t=>t.id===f.dataset.reuseTabId),tab=reused||newTerminal({title:payload.name,server_id:serverId});tab.title=payload.name;tab.server_id=serverId;renderTabs();saveTabs();$('#connect-dialog').close();connectTab(tab,serverId?{server_id:serverId}:payload);
  }catch(err){if(err.message.includes('锁定'))openVault();toast(err.message,true)}});
function connectTab(tab,connection){
  fitTerminal(tab);
  const reconnecting=tab.localPromptShown;if(tab.ws?.readyState===WebSocket.OPEN)tab.ws.close();tab.status='connecting';tab.localInput='';tab.typedLine='';tab.lastCommand='';tab.lastCommandStart=null;tab.agentBuffer=null;tab.localPromptShown=false;renderTabs();writeAtTerminalBottom(tab,`\x1b[33m${reconnecting?'正在重新连接':'正在连接'}…\x1b[0m\r\n`);
  const proto=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${proto}://${location.host}/ws/terminal`);tab.ws=ws;
  ws.onopen=()=>{let payload={type:'connect',cols:tab.term.cols,rows:tab.term.rows,...connection};ws.send(JSON.stringify(payload))};
  ws.onmessage=e=>{if(tab.ws!==ws)return;const m=JSON.parse(e.data);if(m.type==='connected'){tab.status='connected';tab.sessionId=m.session_id;fitTerminal(tab);tab.term.writeln('\x1b[32m已连接\x1b[0m');renderTabs();renderAgentChat();scheduleTerminalFit(tab,true);loadSftp();loadSystemInfo();loadServers()}
    else if(m.type==='output'&&!tab.localPromptShown&&(tab.status==='connecting'||tab.status==='connected')){const bytes=Uint8Array.from(atob(m.data),c=>c.charCodeAt(0));tab.term.write(bytes)}
    else if(m.type==='host_key')showHostKeyDialog(ws,m)
    else if(m.type==='error'){tab.status='error';renderTabs();renderAgentChat();showReconnectPrompt(tab,m.message);toast(m.message,true)}else if(m.type==='disconnected'){tab.status='idle';renderTabs();renderAgentChat();showReconnectPrompt(tab);loadSystemInfo()}};
  ws.onerror=()=>{if(tab.ws!==ws)return;tab.status='error';renderTabs();showReconnectPrompt(tab,'连接失败')};ws.onclose=()=>{if(pendingHostKey?.ws===ws){pendingHostKey=null;if($('#host-key-dialog').open)$('#host-key-dialog').close()}if(tab.ws!==ws)return;if(tab.status==='connected'||tab.status==='connecting'){tab.status='idle';renderTabs();showReconnectPrompt(tab);loadSftp();loadSystemInfo()}};
}
function markTabDisconnected(tab){if(!tab||tab.status==='idle'&&!tab.sessionId)return;tab.status='idle';tab.sessionId=null;if(tab.ws?.readyState===WebSocket.OPEN)tab.ws.close();renderTabs();showReconnectPrompt(tab);loadSftp();loadSystemInfo()}
function showHostManager(){
  renderHostManager();
  $('#host-manager').classList.remove('hidden');
  $('#host-manager-search').focus();
}
function hideHostManager(){ $('#host-manager').classList.add('hidden');closeContextMenu() }
function connectSavedServer(server){
  hideHostManager();
  const tab=newTerminal({title:server.name,server_id:server.id});
  if(!state.vault?.vault_unlocked){state.pendingReconnect=tab;openVault();return}
  connectTab(tab,{server_id:server.id});
}
function recentConnectionLabel(value){
  if(!value)return '尚未连接';
  const time=new Date(value.includes('T')?value:`${value.replace(' ','T')}Z`),seconds=Math.max(0,(Date.now()-time.getTime())/1000);
  if(!Number.isFinite(seconds))return '最近使用';
  if(seconds<60)return '刚刚连接';
  if(seconds<3600)return `${Math.floor(seconds/60)} 分钟前`;
  if(seconds<86400)return `${Math.floor(seconds/3600)} 小时前`;
  if(seconds<604800)return `${Math.floor(seconds/86400)} 天前`;
  return time.toLocaleDateString('zh-CN',{month:'short',day:'numeric'});
}
const hostSystemIcons={
  default:{label:'未知系统'},linux:{label:'Linux'},ubuntu:{label:'Ubuntu'},debian:{label:'Debian'},
  fedora:{label:'Fedora'},centos:{label:'CentOS'},rhel:{label:'Red Hat'},rocky:{label:'Rocky Linux'},
  alma:{label:'AlmaLinux'},alpine:{label:'Alpine Linux'},arch:{label:'Arch Linux'},opensuse:{label:'openSUSE'},
  kali:{label:'Kali Linux'},mint:{label:'Linux Mint'},amazon:{label:'Amazon Linux'},oracle:{label:'Oracle Linux'},
  freebsd:{label:'FreeBSD'},macos:{label:'macOS'},windows:{label:'Windows'},gentoo:{label:'Gentoo'},void:{label:'Void Linux'}
};
function hostIconMarkup(server){const type=hostSystemIcons[server.os_type]?server.os_type:'default',icon=hostSystemIcons[type];return `<span class="host-icon host-icon-${type}" title="${icon.label}" aria-hidden="true">${type==='default'?'<span class="host-icon-server"><i></i><i></i></span>':`<img src="/static/icons/os/${type}.svg" alt="">`}</span>`}
function showHostMenu(event,server){showContextMenu(event,[
  {label:'连接',run:()=>connectSavedServer(server)},
  {label:'编辑',run:()=>editServer(server)},
  {label:'打开workspace',run:()=>openServerWorkspace(server)},
  null,
  {label:'删除',run:()=>deleteServer(server)}
])}
function renderHostManager(){
  const query=$('#host-manager-search').value.trim().toLowerCase(),root=$('#host-manager-list');root.replaceChildren();
  const servers=state.servers.filter(server=>`${server.name} ${server.host} ${server.username} ${server.note||''}`.toLowerCase().includes(query));
  $('#host-manager-count').textContent=query?`${servers.length} 个结果`:`${state.servers.length} 台主机`;
  for(const server of servers){
    const card=document.createElement('article');card.className='host-card';card.tabIndex=0;card.setAttribute('aria-label',`${server.name}，双击连接`);
    card.innerHTML=`${hostIconMarkup(server)}<span class="host-card-copy"><strong>${esc(server.name)}</strong><span>${esc(server.username)}@${esc(server.host)}${server.port===22?'':`:${server.port}`}</span><small>${esc(recentConnectionLabel(server.last_connected_at))}</small></span><button class="host-card-more" type="button" aria-label="${esc(server.name)} 的更多操作">•••</button>`;
    card.onclick=()=>{$$('.host-card',root).forEach(item=>item.classList.toggle('selected',item===card))};
    card.ondblclick=()=>connectSavedServer(server);card.oncontextmenu=event=>showHostMenu(event,server);
    card.onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();connectSavedServer(server)}};
    $('.host-card-more',card).onclick=event=>{event.stopPropagation();showHostMenu(event,server)};root.append(card)
  }
  if(!root.children.length){
    root.innerHTML=query?'<div class="host-manager-empty"><span class="host-manager-empty-search" aria-hidden="true"></span><strong>没有匹配的主机</strong></div>':'<div class="host-manager-empty"><span class="host-manager-empty-icon" aria-hidden="true"></span><strong>点击右上新建主机</strong></div>';
  }
}
$('#host-library-button').onclick=showHostManager;
$('#host-manager-close').onclick=hideHostManager;
$('#host-manager-new').onclick=()=>openConnect({forceSave:true,saveOnly:true});
$('#host-manager-search').oninput=renderHostManager;
$('#host-manager').addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();hideHostManager();$('#host-library-button').focus()}});
$('#welcome-connect').onclick=showHostManager;

function formatBytes(value){if(!value)return '0M';const units=['B','K','M','G','T'];let n=value,i=0;while(n>=1024&&i<units.length-1){n/=1024;i++}return `${n>=10||i<2?Math.round(n):n.toFixed(1)}${units[i]}`}
function formatUptime(seconds){seconds=Math.max(0,Number(seconds)||0);const days=Math.floor(seconds/86400),hours=Math.floor(seconds%86400/3600),minutes=Math.floor(seconds%3600/60);return [days&&`${days}天`,hours&&`${hours}小时`,(!days&&minutes||(!days&&!hours))&&`${minutes}分钟`].filter(Boolean).join(' ')}
function setMetric(name,percent,value,detail){percent=Math.max(0,Math.min(100,Number(percent)||0));$(`#${name}-bar`).style.width=`${percent}%`;$(`#${name}-value`).textContent=value;if(detail!==undefined)$(`#${name}-detail`).textContent=detail}
function resetSystemInfo(){$('#system-ip').textContent='--';$('#copy-system-ip').disabled=true;$('#system-uptime').textContent='--';$('#system-load').textContent='--, --, --';setMetric('cpu',0,'--');setMetric('memory',0,'--','--/--');setMetric('swap',0,'--','--/--')}
let systemInfoRequest=0;
async function loadSystemInfo(){const tab=activeTab(),request=++systemInfoRequest;if(!tab?.sessionId)return resetSystemInfo();try{const data=await api(`/api/system-info?session_id=${encodeURIComponent(tab.sessionId)}`);if(request!==systemInfoRequest||tab!==activeTab())return;$('#system-ip').textContent=data.ip;$('#copy-system-ip').disabled=false;$('#system-uptime').textContent=formatUptime(data.uptime);$('#system-load').textContent=data.load.join(', ');const memoryPercent=data.memory_total?data.memory_used*100/data.memory_total:0,swapPercent=data.swap_total?data.swap_used*100/data.swap_total:0;setMetric('cpu',data.cpu_percent,`${Math.round(data.cpu_percent)}%`);setMetric('memory',memoryPercent,`${Math.round(memoryPercent)}%`,`${formatBytes(data.memory_used)}/${formatBytes(data.memory_total)}`);setMetric('swap',swapPercent,`${Math.round(swapPercent)}%`,`${formatBytes(data.swap_used)}/${formatBytes(data.swap_total)}`)}catch(err){if(request!==systemInfoRequest)return;resetSystemInfo();if((err.status===404||err.status===410)&&tab.sessionId)markTabDisconnected(tab)}}
$('#copy-system-ip').onclick=async()=>{const ip=$('#system-ip').textContent;if(ip==='--')return;try{await writeClipboard(ip);toast('IP 已复制')}catch{toast('复制失败',true)}};
$('#system-info-toggle').onclick=()=>{const collapsed=$('#system-info').classList.toggle('collapsed');$('#system-info-toggle').setAttribute('aria-expanded',String(!collapsed))};
setInterval(()=>{if(!document.hidden)loadSystemInfo()},5000);

async function loadServers(){state.servers=await api('/api/servers');renderHostManager()}
async function openServerWorkspace(server){try{await api(`/api/servers/${server.id}/workspace/open`,{method:'POST'});toast('已在资源管理器中打开 workspace')}catch(err){toast(err.message||'打开 workspace 失败',true)}}
async function deleteServer(server){if(!await themedConfirm(`删除主机“${server.name}”？`,{title:'删除主机',confirmText:'删除',danger:true}))return;const deleteWorkspace=await themedConfirm(`是否同时删除“${server.name}”对应 workspace 中的所有文件？\n\n选择“取消”将保留这些文件。`);await api(`/api/servers/${server.id}?delete_workspace=${deleteWorkspace}`,{method:'DELETE'});await loadServers();toast(deleteWorkspace?'主机和 workspace 已删除':'主机已删除，workspace 已保留')}
function updateServerEditorAuthFields(form=$('#editor-form')){
  const usesKey=form.elements.auth_type.value==='private_key',usesSavedKey=!!form.elements.ssh_key_id.value;
  for(const [name,visible] of [['password',!usesKey],['ssh_key_id',usesKey],['private_key',usesKey&&!usesSavedKey],['passphrase',usesKey&&!usesSavedKey]]){
    const input=form.elements[name];
    input.closest('label').classList.toggle('hidden',!visible);
    input.disabled=!visible;
  }
}
function editServer(s){
  openEditor('编辑主机',[
  ['name','名称',s.name],['host','主机',s.host],['port','端口',s.port,'number'],['username','用户名',s.username],['auth_type','认证方式',s.auth_type,'select'],['ssh_key_id','密码库密钥',s.ssh_key_id||'','sshkey'],['password','新密码（留空保持不变）','', 'password'],['private_key','新私钥（留空保持不变）','','textarea'],['passphrase','新私钥口令（留空保持不变）','','password'],['note','备注',s.note,'textarea']
  ],async d=>{
    const usesKey=d.auth_type==='private_key';
    await api(`/api/servers/${s.id}`,{method:'PUT',body:JSON.stringify({
      ...d,
      port:Number(d.port),
      ssh_key_id:usesKey&&d.ssh_key_id?Number(d.ssh_key_id):null,
      password:!usesKey&&d.password?d.password:null,
      private_key:usesKey&&d.private_key?d.private_key:null,
      passphrase:usesKey&&d.passphrase?d.passphrase:null,
    })});
    loadServers();
  });
  const form=$('#editor-form');
  form.elements.auth_type.addEventListener('change',()=>updateServerEditorAuthFields(form));
  form.elements.ssh_key_id.addEventListener('change',()=>updateServerEditorAuthFields(form));
  updateServerEditorAuthFields(form);
}

async function loadShortcuts(){state.shortcuts=await api('/api/shortcuts');renderShortcuts()}
function shortcutActions(s){return [
  {label:'编辑',run:()=>editShortcut(s)},
  {label:'删除',run:()=>deleteShortcut(s)},
  null,
  {label:'填入',run:()=>sendShortcut(s,false)},
  {label:'执行',run:()=>sendShortcut(s,true)}
]}
function showShortcutMenu(event,s){showContextMenu(event,shortcutActions(s))}
async function deleteShortcut(s){if(await themedConfirm(`删除“${s.name}”？`)){await api(`/api/shortcuts/${s.id}`,{method:'DELETE'});await loadShortcuts()}}
function renderShortcuts(){const q=$('#shortcut-search').value.trim().toLowerCase(),root=$('#shortcut-list');root.replaceChildren();state.shortcuts.filter(s=>`${s.name} ${s.command}`.toLowerCase().includes(q)).forEach(s=>{const row=document.createElement('div');row.className='shortcut-row';row.title=s.name;const name=document.createElement('span');name.className='shortcut-name';name.textContent=s.name;const menu=document.createElement('button');menu.type='button';menu.className='shortcut-menu-button';menu.textContent='⋮';menu.title='更多操作';menu.setAttribute('aria-label',`${s.name} 的更多操作`);menu.onclick=event=>{event.stopPropagation();const rect=menu.getBoundingClientRect();showShortcutMenu({preventDefault(){},clientX:rect.right,clientY:rect.bottom},s)};row.append(name,menu);row.ondblclick=event=>{if(!event.target.closest('button'))sendShortcut(s,true)};row.oncontextmenu=event=>showShortcutMenu(event,s);root.append(row)});if(!root.children.length)root.innerHTML='<div class="empty">暂无快捷指令</div>'}
async function sendShortcut(s,run){const tab=activeTab();if(!tab||tab.status!=='connected')return toast('请先连接并选择一个终端',true);if(run&&s.command.includes('\n')&&!await themedConfirm('执行多行脚本？'))return;sendTerminalInput(tab,s.command+(run?'\n':''));tab.term.focus()}
function editShortcut(s={}){openEditor(s.id?'编辑快捷指令':'新建快捷指令',[['name','名称',s.name||'','text',{maxlength:30,required:true,placeholder:'最多 30 个字符'}],['command','命令或脚本',s.command||'','code']],async d=>{await api(s.id?`/api/shortcuts/${s.id}`:'/api/shortcuts',{method:s.id?'PUT':'POST',body:JSON.stringify({...d,sort_order:s.sort_order||0})});loadShortcuts()})}
$('#shortcut-search').oninput=renderShortcuts;$('#shortcut-add').onclick=()=>editShortcut();

function shortcutEditorNewline(editor){const cursor=editor.getCursor(),before=editor.getLine(cursor.line).slice(0,cursor.ch),leading=before.match(/^\s*/)?.[0]||'',opensBlock=/(?:\bthen|\bdo|\bin|\{|\(|\[)\s*$/.test(before.trimEnd()),extra=opensBlock?' '.repeat(editor.getOption('indentUnit')):'';editor.replaceSelection(`\n${leading}${extra}`,'end')}
function openEditor(title,fields,submit){$('#editor-title').textContent=title;const root=$('#editor-fields'),dialog=$('#editor-dialog'),codeEditors=[];root.replaceChildren();dialog.classList.toggle('shortcut-editor-dialog',fields.some(field=>field[3]==='code'));fields.forEach(([name,label,value,type='text',options={}])=>{const l=document.createElement('label');const caption=document.createElement('span');caption.textContent=label;l.append(caption);let input;if(type==='textarea'||type==='code'){input=document.createElement('textarea');input.rows=type==='code'?10:6}else if(type==='select'){input=document.createElement('select');input.innerHTML='<option value="password">密码</option><option value="private_key">私钥</option>'}else if(type==='sshkey'){input=document.createElement('select');input.add(new Option('不使用密码库密钥',''));for(const key of state.sshKeys)input.add(new Option(`${key.name} · ${key.key_type}`,String(key.id)))}else{input=document.createElement('input');input.type=type}input.name=name;input.autocomplete=type==='password'?'new-password':'off';input.value=value??'';for(const [key,option] of Object.entries(options)){if(typeof option==='boolean')input[key]=option;else input.setAttribute(key,String(option))}if(type==='code')l.classList.add('shortcut-code-field');l.append(input);root.append(l);if(type==='code'){const cm=CodeMirror.fromTextArea(input,{mode:'shell',lineNumbers:true,indentUnit:2,tabSize:2,indentWithTabs:false,smartIndent:true,matchBrackets:true,styleActiveLine:true,lineWrapping:false,theme:themeModes[currentTheme()]==='dark'?'material-darker':'default',extraKeys:{Enter:shortcutEditorNewline,Tab:editor=>editor.somethingSelected()?editor.indentSelection('add'):editor.execCommand('insertSoftTab'),'Shift-Tab':editor=>editor.indentSelection('subtract')}});codeEditors.push(cm)}});$('#editor-form').onsubmit=async e=>{e.preventDefault();codeEditors.forEach(cm=>cm.save());try{const data=Object.fromEntries(new FormData(e.target));if(codeEditors.length&&!String(data.command||'').trim())throw new Error('请输入命令或脚本');await submit(data);dialog.close();toast('已保存')}catch(err){toast(err.message,true)}};dialog.showModal();setTimeout(()=>{codeEditors.forEach(cm=>cm.refresh());(codeEditors[0]||$('input,textarea,select',root))?.focus()},30)}

async function loadSftp(path){const tab=activeTab(),root=$('#file-list');if(!tab?.sessionId){root.innerHTML='<div class="empty">连接 SSH 后浏览文件</div>';return}path=path||$('#sftp-path').value||tab.last_path||'.';root.innerHTML='<div class="empty">正在加载…</div>';try{const data=await api(`/api/sftp/list?session_id=${encodeURIComponent(tab.sessionId)}&path=${encodeURIComponent(path)}`);tab.last_path=data.path;$('#sftp-path').value=data.path;saveTabs();renderFiles(data.items)}catch(err){root.innerHTML=`<div class="empty">${esc(err.message)}</div>`}}
function renderFiles(items){const root=$('#file-list');root.replaceChildren();state.selectedFiles.clear();updateBatch();items.forEach(item=>{const row=document.createElement('div');row.className='file-row';row.innerHTML=`<input type="checkbox" aria-label="选择 ${esc(item.name)}"><span>${item.is_dir?'📁':'📄'}</span><span class="file-name" title="${esc(item.name)}">${esc(item.name)}</span><span class="file-size">${item.is_dir?'':formatSize(item.size)}</span>`;const check=$('input',row);check.onchange=()=>{check.checked?state.selectedFiles.add(item):state.selectedFiles.delete(item);updateBatch()};row.ondblclick=e=>{if(e.target!==check)(item.is_dir?loadSftp(joinRemote(activeTab().last_path,item.name)):openFileEditor(joinRemote(activeTab().last_path,item.name)))};row.oncontextmenu=e=>showFileMenu(e,item);root.append(row)});const tab=activeTab();for(const task of state.uploadTasks.values())if(task.tabId===tab?.id&&task.directory===tab.last_path)renderUploadTask(task);if(!root.children.length)root.innerHTML='<div class="empty">空目录</div>'}
function updateBatch(){$('#batch-btn').classList.toggle('hidden',!state.selectedFiles.size);$('#batch-btn').textContent=`批量操作 (${state.selectedFiles.size})`}
$('#batch-btn').onclick=async()=>{const items=[...state.selectedFiles],tab=activeTab();if(!items.length||!tab)return;const action=await themedInput('输入批量操作：move / copy / delete');if(!action)return;try{if(action==='delete'){if(!await themedConfirm(`递归删除选中的 ${items.length} 项？`))return;for(const item of items)await api('/api/sftp/delete',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,path:joinRemote(tab.last_path,item.name)})})}else if(action==='move'||action==='copy'){const target=await themedInput('目标目录完整路径');if(!target)return;for(const item of items)await api(`/api/sftp/${action}`,{method:'POST',body:JSON.stringify({session_id:tab.sessionId,source:joinRemote(tab.last_path,item.name),destination:joinRemote(target,item.name),overwrite:false})})}else return toast('未知操作',true);toast('批量操作完成');loadSftp()}catch(err){toast(err.message,true)}};
function formatSize(n){if(n<1024)return `${n} B`;if(n<1048576)return `${(n/1024).toFixed(1)} KB`;return `${(n/1048576).toFixed(1)} MB`}
function joinRemote(a,b){return `${a.replace(/\/$/,'')}/${b}`}
async function fileAction(item){const action=await themedInput(`操作 ${item.name}\n输入：rename / move / copy / delete${item.is_dir?'':' / download'}`);if(!action)return;const tab=activeTab(),source=joinRemote(tab.last_path,item.name);try{if(action==='download'&&!item.is_dir)return downloadFile(item.name);if(action==='delete'){if(!await themedConfirm(`递归删除“${item.name}”？`))return;await api('/api/sftp/delete',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,path:source})})}else if(['rename','move','copy'].includes(action)){const dst=await themedInput('目标完整路径',source);if(!dst||dst===source)return;await api(`/api/sftp/${action==='copy'?'copy':'move'}`,{method:'POST',body:JSON.stringify({session_id:tab.sessionId,source,destination:dst,overwrite:false})})}else return toast('未知操作',true);loadSftp();toast('操作完成')}catch(err){if(err.message.includes('exist')&&await themedConfirm('目标已存在，是否覆盖？'))toast('请重新操作并使用其他目标路径',true);else toast(err.message,true)}}
function chosenItems(item){return state.selectedFiles.has(item)&&state.selectedFiles.size?[...state.selectedFiles]:[item]}
function closeContextMenu(){$('.context-menu')?.remove()}
function showContextMenu(event,actions){event.preventDefault();closeContextMenu();const menu=document.createElement('div');menu.className='context-menu';for(const action of actions){if(action===null){menu.append(document.createElement('hr'));continue}const button=document.createElement('button');button.textContent=action.label;button.disabled=!!action.disabled;button.onclick=async()=>{closeContextMenu();try{await action.run()}catch(err){toast(err.message,true)}};menu.append(button)}document.body.append(menu);const left=Math.min(event.clientX,innerWidth-menu.offsetWidth-8),top=Math.min(event.clientY,innerHeight-menu.offsetHeight-8);menu.style.left=`${Math.max(8,left)}px`;menu.style.top=`${Math.max(8,top)}px`}
function desktopClipboard(){return window.pywebview?.api}
async function readClipboard(){
  const desktop=desktopClipboard();
  if(desktop?.read_clipboard)try{return await desktop.read_clipboard()}catch{}
  try{return await navigator.clipboard.readText()}catch{return await themedInput('浏览器无法直接读取剪贴板，请在下方粘贴内容。','',{title:'粘贴到终端',label:'粘贴内容'})}
}
async function writeClipboard(value){
  const desktop=desktopClipboard();
  if(desktop?.write_clipboard)try{await desktop.write_clipboard(value);return}catch{}
  try{await navigator.clipboard.writeText(value)}catch{const area=document.createElement('textarea');area.value=value;area.style.position='fixed';area.style.opacity='0';document.body.append(area);area.select();document.execCommand('copy');area.remove()}
}
async function disconnectTerminal(tab){if(!await themedConfirm(`断开 ${tab.title} 的 SSH 连接？`,{title:'断开连接',confirmText:'断开',danger:true}))return;if(tab.ws?.readyState===WebSocket.OPEN)tab.ws.send(JSON.stringify({type:'close'}));tab.ws?.close()}
function showTerminalMenu(event,tab){const connected=tab.status==='connected',hasSelection=tab.term.hasSelection();showContextMenu(event,[
  {label:'复制',disabled:!hasSelection,run:async()=>{await writeClipboard(tab.term.getSelection());toast('已复制终端内容')}},
  {label:'粘贴',disabled:!connected,run:async()=>{const text=await readClipboard();if(text)sendTerminalInput(tab,text);tab.term.focus()}},
  {label:'Agent 命令',disabled:!connected,run:()=>{sendTerminalInput(tab,'/agent ');tab.term.focus()}},
  null,
  {label:'全选终端内容',run:()=>{tab.term.selectAll();tab.term.focus()}},
  {label:'清空终端显示',run:()=>{tab.term.clear();tab.term.focus()}},
  null,
  ...(connected||tab.status==='connecting'?[{label:'断开连接',run:()=>disconnectTerminal(tab)}]:[{label:'重新连接',run:()=>reconnectTab(tab)}]),
  {label:'关闭标签页',run:()=>closeTab(tab)}
])}
function showFileMenu(event,item){const tab=activeTab(),items=chosenItems(item),paths=items.map(x=>joinRemote(tab.last_path,x.name));showContextMenu(event,[
  ...(!item.is_dir&&items.length===1?[{label:'编辑',run:()=>openFileEditor(paths[0])}]:[]),
  ...(!item.is_dir&&items.length===1?[{label:'下载',run:()=>downloadFile(item.name)}]:[]),
  {label:items.length>1?`复制 ${items.length} 项`:'复制',run:async()=>{state.remoteClipboard={sessionId:tab.sessionId,items:paths.map((path,i)=>({path,name:items[i].name})),mode:'copy'};toast('已复制，可在目标目录右键粘贴')}},
  {label:'粘贴到此目录',disabled:!item.is_dir||!state.remoteClipboard,run:()=>pasteRemote(joinRemote(tab.last_path,item.name))},
  {label:'重命名',disabled:items.length!==1,run:async()=>{const name=await themedInput('新名称',item.name);if(!name||name===item.name)return;await api('/api/sftp/move',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,source:paths[0],destination:joinRemote(tab.last_path,name),overwrite:false})});loadSftp()}},
  null,
  {label:items.length>1?`删除 ${items.length} 项`:'删除',run:async()=>{if(!await themedConfirm(`确定递归删除选中的 ${items.length} 项？`))return;for(const path of paths)await api('/api/sftp/delete',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,path})});loadSftp()}},
  {label:'复制文件路径',disabled:items.length!==1,run:()=>copyText(paths[0])}
])}
async function pasteRemote(destination){const tab=activeTab(),clip=state.remoteClipboard;if(!clip)return;if(clip.sessionId!==tab.sessionId)throw new Error('当前版本仅支持在同一个 SSH 会话内粘贴');for(const item of clip.items){const target=joinRemote(destination,item.name);try{await api('/api/sftp/copy',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,source:item.path,destination:target,overwrite:false})})}catch(err){if(!err.message.includes('存在')||!await themedConfirm(`${item.name} 已存在，是否覆盖？`))throw err;await api('/api/sftp/copy',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,source:item.path,destination:target,overwrite:true})})}}toast('粘贴完成');loadSftp()}
async function copyText(value){await writeClipboard(value);toast('路径已复制')}
function editorMode(name){const ext=(name.split('.').pop()||'').toLowerCase(),map={py:'python',js:'javascript',mjs:'javascript',cjs:'javascript',json:{name:'javascript',json:true},ts:'javascript',tsx:'javascript',html:'htmlmixed',htm:'htmlmixed',css:'css',scss:'text/x-scss',xml:'xml',svg:'xml',sh:'shell',bash:'shell',zsh:'shell',yaml:'yaml',yml:'yaml',md:'markdown',markdown:'markdown',c:'text/x-csrc',h:'text/x-csrc',cpp:'text/x-c++src',cc:'text/x-c++src',hpp:'text/x-c++src',java:'text/x-java',cs:'text/x-csharp'};return map[ext]||null}
function editorModeLabel(name){const mode=editorMode(name);return typeof mode==='string'?mode:(mode?.json?'JSON':'纯文本')}
function ensureEditor(){if(state.editor.cm)return state.editor.cm;const cm=CodeMirror.fromTextArea($('#file-editor-textarea'),{lineNumbers:true,indentUnit:2,tabSize:2,indentWithTabs:false,smartIndent:true,matchBrackets:true,autoCloseBrackets:true,styleActiveLine:true,lineWrapping:false,theme:themeModes[currentTheme()]==='dark'?'material-darker':'default',extraKeys:{'Ctrl-S':()=>saveFileEditor(),'Cmd-S':()=>saveFileEditor(),Tab:editor=>editor.somethingSelected()?editor.indentSelection('add'):editor.execCommand('insertSoftTab'),'Shift-Tab':editor=>editor.indentSelection('subtract')}});cm.on('change',()=>{if(!state.editor.loading){state.editor.dirty=true;updateEditorState()}});cm.on('cursorActivity',()=>{const pos=cm.getCursor();$('#file-editor-position').textContent=`Ln ${pos.line+1}, Col ${pos.ch+1}`});state.editor.cm=cm;return cm}
function updateEditorState(){const e=state.editor;$('#file-editor-dirty').classList.toggle('hidden',!e.dirty);$('#file-editor-state').textContent=e.saving?'正在保存…':e.dirty?'UTF-8 · 未保存':'UTF-8 · 已保存';$('#file-editor-save').disabled=e.saving||!e.dirty}
async function openFileEditor(path){const tab=activeTab();if(!tab?.sessionId)return toast('请先连接终端',true);try{const data=await api(`/api/sftp/editor?session_id=${encodeURIComponent(tab.sessionId)}&path=${encodeURIComponent(path)}`),cm=ensureEditor();Object.assign(state.editor,{sessionId:tab.sessionId,path:data.path,mtime:data.mtime,dirty:false,saving:false,loading:true});cm.setOption('mode',editorMode(data.name));cm.setValue(data.content);cm.clearHistory();state.editor.loading=false;$('#file-editor-name').textContent=data.name;$('#file-editor-path').textContent=data.path;$('#file-editor-mode').textContent=editorModeLabel(data.name);updateEditorState();if(!$('#file-editor-dialog').open)$('#file-editor-dialog').showModal();setTimeout(()=>{cm.refresh();cm.focus()},30)}catch(err){toast(err.message,true)}}
async function saveFileEditor(force=false){const e=state.editor;if(!e.cm||!e.path||e.saving||(!e.dirty&&!force))return;e.saving=true;updateEditorState();try{const result=await api('/api/sftp/editor',{method:'PUT',body:JSON.stringify({session_id:e.sessionId,path:e.path,content:e.cm.getValue(),expected_mtime:e.mtime,force})});e.mtime=result.mtime;e.dirty=false;toast('文件已保存');updateEditorState();loadSftp()}catch(err){e.saving=false;updateEditorState();if(err.message.includes('其他程序修改')&&await themedConfirm(`${err.message}\n\n仍要覆盖远端文件吗？`))return saveFileEditor(true);toast(err.message,true);return}e.saving=false;updateEditorState()}
async function reloadFileEditor(){if(state.editor.dirty&&!await themedConfirm('放弃未保存的更改并重新载入？'))return;await openFileEditor(state.editor.path)}
async function closeFileEditor(){if(state.editor.dirty&&!await themedConfirm('文件尚未保存，确定关闭？'))return;$('#file-editor-dialog').close()}
async function createNewFile(){const tab=activeTab();if(!tab?.sessionId)return toast('请先连接终端',true);const name=await themedInput('新文件名');if(!name)return;if(name.includes('/')||name.includes('\\')||name==='.'||name==='..')return toast('文件名不能包含路径分隔符',true);const path=joinRemote(tab.last_path,name);try{await api('/api/sftp/file',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,path})});await loadSftp();await openFileEditor(path)}catch(err){toast(err.message,true)}}
function downloadFile(name){const tab=activeTab(),path=joinRemote(tab.last_path,name);const a=document.createElement('a');a.href=`/api/sftp/download?session_id=${encodeURIComponent(tab.sessionId)}&path=${encodeURIComponent(path)}`;a.download=name;a.click()}
function renderUploadTask(task){const root=$('#file-list');let row=$(`.upload-row[data-upload-key="${CSS.escape(task.key)}"]`,root);if(!row){$('.empty',root)?.remove();row=document.createElement('div');row.className='file-row upload-row';row.dataset.uploadKey=task.key;row.innerHTML=`<span></span><span>📄</span><span class="file-name"></span><span class="upload-progress"><span class="upload-ring"></span><span class="upload-percent">0%</span></span>`;$('.file-name',row).textContent=task.file.name;root.append(row)}const percent=task.file.size?Math.min(100,Math.round(task.written/task.file.size*100)):100;$('.upload-ring',row).style.setProperty('--progress',`${percent*3.6}deg`);$('.upload-percent',row).textContent=task.status==='error'?'失败':task.status==='finishing'?'校验中':`${percent}%`;row.classList.toggle('upload-error',task.status==='error')}
async function beginUpload(task,overwrite=false){const tab=state.tabs.find(t=>t.id===task.tabId);if(!tab?.sessionId)throw new Error('SSH 会话已断开');const init=await api('/api/sftp/uploads',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,path:task.directory,filename:task.file.name,size:task.file.size,overwrite})});task.uploadId=init.upload_id;const chunkSize=1024*1024;for(let offset=0;offset<task.file.size;offset+=chunkSize){const chunk=task.file.slice(offset,Math.min(offset+chunkSize,task.file.size));const result=await api(`/api/sftp/uploads/${task.uploadId}?offset=${offset}`,{method:'PUT',headers:{'Content-Type':'application/octet-stream'},body:chunk});task.written=result.written;renderUploadTask(task)}task.status='finishing';renderUploadTask(task);await api(`/api/sftp/uploads/${task.uploadId}/finish`,{method:'POST'});task.status='done'}
async function uploadFiles(files) {
  const tab = activeTab();
  if (!tab?.sessionId) return toast('请先连接终端', true);
  const tasks = [...files].map(file => ({
    key: uid(), file, tabId: tab.id, directory: tab.last_path,
    written: 0, status: 'uploading', uploadId: null
  }));
  tasks.forEach(task => {
    state.uploadTasks.set(task.key, task);
    renderUploadTask(task);
  });
  for (const task of tasks) {
    try {
      await beginUpload(task);
    } catch (err) {
      if (task.uploadId) {
        await api(`/api/sftp/uploads/${task.uploadId}`, {method: 'DELETE'}).catch(() => {});
        task.uploadId = null;
      }
      if (err.message.includes('存在') && await themedConfirm(`${task.file.name} 已存在，是否覆盖？`)) {
        try {
          await beginUpload(task, true);
        } catch (retry) {
          if (task.uploadId) await api(`/api/sftp/uploads/${task.uploadId}`, {method: 'DELETE'}).catch(() => {});
          task.status = 'error';
          toast(retry.message, true);
        }
      } else {
        task.status = 'error';
        toast(`${task.file.name}: ${err.message}`, true);
      }
      renderUploadTask(task);
    }
    if (task.status === 'error') continue;
    state.uploadTasks.delete(task.key);
    toast(`已上传 ${task.file.name}`);
  }
  loadSftp();
}
$('#upload-btn').onclick=()=>$('#upload-input').click();$('#upload-input').onchange=e=>uploadFiles(e.target.files);$('#sftp-refresh').onclick=()=>loadSftp();$('#sftp-go').onclick=()=>loadSftp();$('#sftp-path').onkeydown=e=>{if(e.key==='Enter')loadSftp()};$('#sftp-up').onclick=()=>{const p=activeTab()?.last_path||'.';loadSftp(p==='/'?'/':p.replace(/\/[^/]+\/?$/,'')||'/')};$('#mkdir-btn').onclick=async()=>{const tab=activeTab();if(!tab?.sessionId)return toast('请先连接终端',true);const name=await themedInput('目录名称');if(name)try{await api('/api/sftp/mkdir',{method:'POST',body:JSON.stringify({session_id:tab.sessionId,path:joinRemote(tab.last_path,name)})});loadSftp()}catch(err){toast(err.message,true)}};
$('#new-file-btn').onclick=createNewFile;$('#file-editor-save').onclick=()=>saveFileEditor();$('#file-editor-reload').onclick=reloadFileEditor;$('#file-editor-close').onclick=closeFileEditor;$('#file-editor-dialog').addEventListener('cancel',e=>{if(state.editor.dirty){e.preventDefault();closeFileEditor()}});
const drop=$('#file-list');['dragenter','dragover'].forEach(x=>drop.addEventListener(x,e=>{e.preventDefault();drop.classList.add('drag')}));['dragleave','drop'].forEach(x=>drop.addEventListener(x,e=>{e.preventDefault();drop.classList.remove('drag')}));drop.addEventListener('drop',e=>uploadFiles(e.dataTransfer.files));
drop.addEventListener('contextmenu',e=>{if(e.target.closest('.file-row'))return;const tab=activeTab();showContextMenu(e,[{label:'新建文件',run:createNewFile},{label:'新建目录',run:()=>$('#mkdir-btn').click()},{label:'上传文件',run:()=>$('#upload-input').click()},null,{label:'粘贴',disabled:!state.remoteClipboard,run:()=>pasteRemote(tab.last_path)},{label:'刷新',run:()=>loadSftp()}])});
document.addEventListener('pointerdown',e=>{if(!e.target.closest('.context-menu'))closeContextMenu()});window.addEventListener('blur',closeContextMenu);

$$('.side-tab').forEach(btn=>btn.onclick=()=>{$$('.side-tab').forEach(x=>x.classList.toggle('active',x===btn));$$('.side-panel').forEach(x=>x.classList.toggle('active',x.id===`panel-${btn.dataset.panel}`))});
$('#sidebar-toggle').onclick=()=>{$('#sidebar').classList.toggle('collapsed');scheduleTerminalFit(activeTab())};let resizing=false;$('#resizer').onmousedown=()=>resizing=true;window.addEventListener('mousemove',e=>{if(resizing){const ratio=Math.max(240/innerWidth,Math.min(.55,e.clientX/innerWidth));$('#sidebar').style.width=`${ratio*100}%`;localStorage.setItem('webssh-sidebar-ratio',ratio)}});window.addEventListener('mouseup',()=>{if(resizing)scheduleTerminalFit(activeTab());resizing=false});let sr=Number(localStorage.getItem('webssh-sidebar-ratio'));if(!sr){const oldWidth=Number(localStorage.getItem('webssh-sidebar-width'));if(oldWidth)sr=oldWidth/innerWidth}if(sr>0)$('#sidebar').style.width=`${Math.max(240/innerWidth,Math.min(.55,sr))*100}%`;
window.addEventListener('resize',()=>scheduleTerminalFit(activeTab()));
new ResizeObserver(()=>fitTerminal(activeTab())).observe($('#terminals'));

async function init(){applyTheme(currentTheme(),false);await restoreTheme();await refreshStatus();await autoUnlockVault();await Promise.all([loadServers(),loadShortcuts(),loadSSHKeys()]);const tabs=await api('/api/tabs');tabs.forEach(data=>{const tab=newTerminal(data);if(tab)showReconnectPrompt(tab)});if(!tabs.length)activateTab(null);else activateTab(tabs[0].id)}
init().catch(err=>toast(err.message,true));
