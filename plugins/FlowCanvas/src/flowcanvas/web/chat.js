/** Conversation controller: sessions, drafts, model capabilities and turn lifecycle. */
import {request, subscribe} from './chat-api.js';
import {ConversationView} from './chat-view.js';
const $=id=>document.getElementById(id);
const view=new ConversationView($('conversation'));
const active=new Set(['starting','running','stopping']);
let session=null, models=[], unsubscribe=null, busy=false, selected=null, switchNumber=0;
let sessionRows=[],showArchived=false;
const drafts=new Map();
try{for(const [key,value]of Object.entries(JSON.parse(localStorage.getItem('flowcanvas.drafts')||'{}')))if(typeof value==='string')drafts.set(key,value);}catch{}
/** Keep drafts scoped to conversations and survive a page reload. */
function saveDraft(){if(selected){if($('message').value)drafts.set(selected,$('message').value);else drafts.delete(selected);}try{localStorage.setItem('flowcanvas.drafts',JSON.stringify(Object.fromEntries(drafts)));}catch{}}
/** Resize restored drafts as well as text entered by the user. */
function resizeDraft(){$('message').style.height='auto';$('message').style.height=Math.min($('message').scrollHeight,180)+'px';}
let speedMode='standard';
let quoteDraft=[];
const quotes=new Map();
try{for(const [id,items]of Object.entries(JSON.parse(localStorage.getItem('flowcanvas.annotations')||'{}')))if(Array.isArray(items))quotes.set(id,items.filter(item=>typeof item.text==='string'&&typeof item.comment==='string'));}catch{}
/** Render numbered quote attachments with independent comments and editing controls. */
function paintQuote(){
  $('quote-draft').hidden=!quoteDraft.length;$('quote-toggle').textContent=quoteDraft.length+' 条批注';$('quote-panel').replaceChildren();
  quoteDraft.forEach((item,index)=>{
    const row=document.createElement('section');row.className='annotation-row';const head=document.createElement('div');head.className='annotation-head';const label=document.createElement('span');label.textContent=(index+1)+'. 所选文本：';head.append(label);
    const text=document.createElement('textarea');text.className='annotation-text';text.value=item.text;text.readOnly=true;text.rows=3;text.ariaLabel='引用文字';text.oninput=()=>{item.text=text.value;saveQuote();};
    for(const [label,path,action]of [['编辑','M12 4l4 4M4 16l4-1L17 6a2.1 2.1 0 0 0-3-3L5 12Z','edit'],['删除','M3 5h14M8 5V3h4v2M5 5l1 12h8l1-12M8 8v6M12 8v6','remove']]){
      const button=document.createElement('button');button.type='button';button.title=label;button.ariaLabel=label;
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 20 20');const shape=document.createElementNS(svg.namespaceURI,'path');shape.setAttribute('d',path);svg.append(shape);button.append(svg);
      button.onclick=()=>{if(action==='remove'){quoteDraft.splice(index,1);saveQuote();paintQuote();}else{text.readOnly=false;text.focus();text.select();}};head.append(button);
    }
    const comment=document.createElement('textarea');comment.className='annotation-comment';comment.placeholder='添加可选评论…';comment.ariaLabel='批注评论';comment.value=item.comment;comment.rows=1;comment.oninput=()=>{item.comment=comment.value;saveQuote();};row.append(head,text,comment);$('quote-panel').append(row);
  });
}
/** Persist structured annotations per conversation without inserting them into the prompt. */
function saveQuote(){if(selected){if(quoteDraft.length)quotes.set(selected,quoteDraft);else quotes.delete(selected);}try{localStorage.setItem('flowcanvas.annotations',JSON.stringify(Object.fromEntries(quotes)));}catch{}}
/** Serialize attachments only at the backend boundary. */
function quoteText(){return quoteDraft.map((item,index)=>(index+1)+'. 所选文本：\n'+item.text+(item.comment?'\n评论：'+item.comment:'')).join('\n\n');}
// UI-only attachments are kept separate from message payloads and execution permissions.
const attachedWorkflows=new Map();
try{for(const [id,items]of Object.entries(JSON.parse(localStorage.getItem('flowcanvas.workflowAttachments')||'{}')))if(Array.isArray(items))attachedWorkflows.set(id,items.filter(item=>typeof item.id==='string'&&typeof item.name==='string'));}catch{}
/** Render removable workflow chips scoped to the selected conversation, without execution. */
function paintAttachments(){
  const host=$('attached-workflows');host.replaceChildren();
  for(const item of attachedWorkflows.get(selected)||[]){
    const card=document.createElement('div');card.className='workflow-chip';card.title=item.name+' · 仅附加展示，暂不执行';
    const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 20 20');icon.setAttribute('aria-hidden','true');const path=document.createElementNS(icon.namespaceURI,'path');path.setAttribute('d','M7 4h6M10 7v3M4 10h12M4 10v3M16 10v3M7 2h6v5H7ZM1.5 13h5v5h-5ZM13.5 13h5v5h-5Z');icon.append(path);
    const name=document.createElement('span');name.textContent=item.name;
    const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.ariaLabel='移除工作流 '+item.name;remove.title='移除';remove.onclick=()=>{attachedWorkflows.set(selected,(attachedWorkflows.get(selected)||[]).filter(row=>row.id!==item.id));saveAttachments();paintAttachments();};
    card.append(icon,name,remove);host.append(card);
  }
}
/** Save presentation state only; backend messages do not receive these attachments yet. */
function saveAttachments(){try{localStorage.setItem('flowcanvas.workflowAttachments',JSON.stringify(Object.fromEntries(attachedWorkflows)));}catch{}}
/** Show actionable API failures independently of generated message content. */
function error(message){$('chat-error').textContent=message||'';$('chat-error').hidden=!message;}
/** Keep send/stop state tied to the real backend turn. */
function controls(){
  const running=active.has(session?.status);
  $('send').textContent=running&&!$('message').value.trim()?'■':'↑';$('send').classList.toggle('is-stop',running);$('send').ariaLabel=running?($('message').value.trim()?'加入队列':'停止回复'):'发送消息';$('send').title=$('send').ariaLabel;
  $('send').disabled=busy||session?.status==='stopping'||(!running&&(!models.length||!$('message').value.trim()));
  $('model').disabled=$('effort').disabled=$('model-toggle').disabled=running||!models.length;
  $('backend-toggle').disabled=running;
  $('permission-toggle').disabled=running||busy||!session;
  paintPermissions();
  updateSpeed();
  if(running)closePicker();
  $('connection').textContent=running?'Codex 正在处理':'Codex · '+(models.length?'已连接':'连接中');
  $('connection').classList.toggle('working',running);
}
/** Persist permission choices before sending; never optimistically grant access. */
function paintPermissions(){
  const mode=session?.permission_mode||'workflow';
  const choices=[['full','完全访问','可直接修改文件、执行命令'],['workflow','工作流执行','对话只读，通过工作流执行（入口尚未接入）'],['readonly','只读分析','仅阅读和分析，不允许启动工作流']];
  $('permission-label').textContent=choices.find(row=>row[0]===mode)?.[1]||'工作流执行';
  $('permission-toggle').dataset.mode=mode;
  $('permission-options').replaceChildren();
  for(const [value,label,description]of choices){
    const button=document.createElement('button');button.type='button';button.className='model-option permission-option';button.setAttribute('aria-pressed',String(mode===value));button.disabled=active.has(session?.status)||busy||!session;
    const text=document.createElement('span');text.textContent=label;
    const note=document.createElement('small');note.textContent=description;text.append(note);
    const mark=document.createElement('span');mark.className='model-check';mark.textContent=mode===value?'✓':'';button.append(text,mark);
    button.onclick=async()=>{
      const id=session.id;busy=true;controls();
      try{const data=await request('/sessions/'+id+'/permissions',{mode:value});if(selected===id)render(data);closePicker(true,'permission');}
      catch(exc){error(exc.message);}finally{busy=false;controls();}
    };$('permission-options').append(button);
  }
}
/** Populate reasoning options from the selected model, not a hard-coded list. */
function efforts(preferred){
  const model=models.find(m=>m.id===$('model').value);$('effort').replaceChildren();
  for(const value of model?.efforts||[])$('effort').add(new Option(value,value));
  $('effort').value=(model?.efforts||[]).includes(preferred)?preferred:model?.default_effort||'';
  paintPicker();
  updateSpeed();
}
/** Build speed choices from the backend's actual per-model tier catalog. */
function updateSpeed(){
  const model=models.find(model=>model.id===$('model').value);
  const tiers=model?.speed_tiers||{};
  if(speedMode!=='standard'&&!tiers[speedMode])speedMode='standard';
  const labels={standard:'标准',fast:'Fast',ultrafast:'Ultrafast'};
  $('model-speed-badge').hidden=speedMode==='standard';
  $('model-speed-badge').textContent=labels[speedMode];
  $('speed-options').replaceChildren();
  for(const [value,label]of Object.entries(labels)){
    const button=document.createElement('button');button.type='button';button.className='model-option';
    button.disabled=!model||active.has(session?.status)||(value!=='standard'&&!tiers[value]);
    button.setAttribute('aria-pressed',String(value===speedMode));
    const name=document.createElement('span');name.textContent=label;
    const mark=document.createElement('span');mark.className='model-check';mark.textContent=button.disabled?'不可用':value===speedMode?'✓':'';
    button.append(name,mark);button.onclick=()=>{speedMode=value;updateSpeed();closePicker(true,'model');};
    $('speed-options').append(button);
  }
}
/** Reflect persisted selections when restoring a conversation. */
function modelSelection(){
  const fallback=models.find(m=>m.default)||models[0];
  $('model').value=models.some(m=>m.id===session?.model)?session.model:fallback?.id||'';
  speedMode=session?.speed||'standard';
  efforts(session?.effort);
}
/** Render the selected session; other active conversations keep running independently. */
function render(data){
  session=data;view.render(data);controls();error(data.error);paintQueue();
  const option=[...$('sessions').options].find(o=>o.value===data.id);
  if(option)option.textContent=data.title||'新对话';
  $('session-label').textContent=data.title||'新对话';
  $('session-toggle').title=data.title||'新对话';
  const menuTitle=$('session-options').querySelector('[data-session="'+data.id+'"] .session-option-title');
  if(menuTitle)menuTitle.textContent=data.title||'新对话';
}
/** Show accepted queued messages, with explicit interrupt and remove actions. */
function paintQueue(){
  const queue=session?.queue||[];$('message-queue').hidden=!queue.length;$('message-queue').replaceChildren();
  for(const item of queue){
    const row=document.createElement('div');row.className='queued-message';
    const marker=document.createElementNS('http://www.w3.org/2000/svg','svg');marker.setAttribute('viewBox','0 0 20 20');marker.setAttribute('aria-hidden','true');marker.classList.add('queue-marker');
    const path=document.createElementNS(marker.namespaceURI,'path');path.setAttribute('d','M4 3v10a3 3 0 0 0 3 3h9m-3-3 3 3-3 3M8 4h7M8 8h5');marker.append(path);row.append(marker);
    const text=document.createElement('span');text.className='queued-text';text.textContent=item.message.text;text.title=item.message.text;row.append(text);
    if(session.queue_paused){const paused=document.createElement('small');paused.className='queue-paused';paused.textContent='已暂停';row.append(paused);}
    for(const [label,action,shape]of [[active.has(session.status)?'引导':'发送',active.has(session.status)?'steer':'send','M3 5v4a3 3 0 0 0 3 3h10m-4-4 4 4-4 4'],['删除排队消息','remove','M3 5h14M8 5V3h4v2M5 5l1 12h8l1-12M8 8v6M12 8v6']]){
      const button=document.createElement('button');button.type='button';button.className=action==='remove'?'queue-remove':'queue-guide';button.ariaLabel=label;button.title=action==='steer'?'引导当前任务':action==='send'?'发送此消息':label;
      const icon=document.createElementNS(marker.namespaceURI,'svg');icon.setAttribute('viewBox','0 0 20 20');icon.setAttribute('aria-hidden','true');const line=document.createElementNS(marker.namespaceURI,'path');line.setAttribute('d',shape);icon.append(line);button.append(icon);
      if(action!=='remove'){const caption=document.createElement('span');caption.textContent=label;button.append(caption);}
      button.onclick=async()=>{const id=session.id;button.disabled=true;try{const data=await request('/sessions/'+id+'/queue/'+item.id+'/'+action,{});if(selected===id)render(data);}catch(exc){error(exc.message);button.disabled=false;}};row.append(button);
    }
    $('message-queue').append(row);
  }
}
/** Close the anchored picker without changing the current model selection. */
function closePicker(focus=false,kind='model'){for(const name of ['model','session','backend','permission','attach']){$(name+'-popover').hidden=true;$(name+'-toggle').setAttribute('aria-expanded','false');}if(focus)$(kind+'-toggle').focus();}
/** Render catalog choices and reasoning controls with a persistent selected marker. */
function paintPicker(){
  const chosen=models.find(m=>m.id===$('model').value);
  $('model-label').textContent=chosen?.name||'选择模型';
  const effort=$('effort').value;
  $('model-effort-badge').textContent=effort;
  $('model-effort-badge').hidden=!effort;
  $('model-effort-badge').title='推理强度：'+effort;
  $('model-options').replaceChildren();
  for(const model of models){
    const button=document.createElement('button');button.className='model-option';button.type='button';
    button.setAttribute('aria-pressed',String(model.id===chosen?.id));
    const name=document.createElement('span');name.textContent=model.name;
    const mark=document.createElement('span');mark.className='model-check';mark.textContent=model.id===chosen?.id?'✓':'';
    button.append(name,mark);button.onclick=()=>{$('model').value=model.id;efforts();$('model-options').querySelector('[aria-pressed="true"]')?.focus();};
    $('model-options').append(button);
  }
  $('effort-options').replaceChildren();
  for(const value of chosen?.efforts||[]){
    const button=document.createElement('button');button.type='button';button.textContent=value;
    button.setAttribute('aria-pressed',String(value===$('effort').value));
    button.onclick=()=>{$('effort').value=value;paintPicker();closePicker(true,'model');};
    $('effort-options').append(button);
  }
}
/** Refresh conversation choices without changing the currently selected session. */
async function list(){
  const rows=await request('/sessions');sessionRows=rows;$('sessions').replaceChildren();
  for(const row of rows)$('sessions').add(new Option((active.has(row.status)?'● ':'')+(row.title||'新对话'),row.id));
  if(selected)$('sessions').value=selected;
  paintSessions();
  return rows;
}
/** Reuse model-menu rows for conversations, preserving opaque session identities. */
function paintSessions(){
  const query=$('session-search').value.trim().toLocaleLowerCase();
  $('session-options').replaceChildren();
  const rows=sessionRows.filter(row=>Boolean(row.archived)===showArchived&&(row.title||'新对话').toLocaleLowerCase().includes(query)).sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b.updated-a.updated);
  for(const row of rows){
    const wrap=document.createElement('div');wrap.className='session-row';
    const button=document.createElement('button');button.type='button';button.className='model-option session-option';
    button.dataset.session=row.id;button.setAttribute('aria-pressed',String(row.id===selected));
    const title=document.createElement('span');title.className='session-option-title';title.textContent=(row.pinned?'⌖ ':'')+(active.has(row.status)?'● ':'')+(row.title||'新对话');button.title=row.title||'新对话';
    button.append(title);button.onclick=()=>{closePicker(true,'session');choose(row.id).catch(exc=>error(exc.message));};wrap.append(button);
    const icons={delete:'M3 5h14M8 5V3h4v2M5 5l1 12h8l1-12M8 8v6M12 8v6',rename:'M12 4l4 4M4 16l4-1L17 6a2.1 2.1 0 0 0-3-3L5 12Z',pin:'M7 3h6M8 3v5l-3 4v1h10v-1l-3-4V3M10 13v5',archive:'M3 4h14v3H3ZM5 7v10h10V7M8 10h4',restore:'M7 6H3V2M3 6a7 7 0 1 1-1 7'};
    for(const [label,action]of [['重命名','rename'],[row.pinned?'取消置顶':'置顶','pin'],[row.archived?'恢复对话':'归档对话','archive'],['删除对话','delete']]){
      const control=document.createElement('button');control.type='button';control.className='session-row-action';control.ariaLabel=label;
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 20 20');svg.setAttribute('aria-hidden','true');
      const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',icons[action==='archive'&&row.archived?'restore':action]);svg.append(path);control.append(svg);
      const showTip=()=>{const rect=control.getBoundingClientRect();tooltip.textContent=label;tooltip.hidden=false;tooltip.style.left=Math.max(50,Math.min(window.innerWidth-60,rect.left+rect.width/2))+'px';tooltip.style.top=(rect.bottom+6)+'px';};
      control.onmouseenter=control.onfocus=showTip;control.onmouseleave=control.onblur=()=>tooltip.hidden=true;
      control.disabled=(action==='delete'||action==='archive'&&!row.archived)&&active.has(row.status);
      control.onclick=async()=>{
        tooltip.hidden=true;
        if(action==='rename'||action==='delete')await sessionDialog(row,action);
        else if(action==='archive'&&!row.archived)await sessionDialog(row,'archive');
        else await updateSession(row,action==='pin'?{pinned:!row.pinned}:{archived:false});
      };wrap.append(control);
    }
    $('session-options').append(wrap);
  }
  if(!rows.length){const empty=document.createElement('p');empty.className='session-empty';empty.textContent='没有匹配的对话';$('session-options').append(empty);}
}
/** Use a focused modal for renaming and confirmation, with Escape and explicit cancellation. */
async function sessionDialog(row,action){
  closePicker();
  const dialog=document.createElement('dialog');dialog.className='session-dialog';dialog.setAttribute('aria-labelledby','session-dialog-title');
  const form=document.createElement('form');
  const heading=document.createElement('h3');heading.id='session-dialog-title';heading.textContent=action==='rename'?'重命名对话':action==='delete'?'删除这段对话？':'归档这段对话？';
  const description=document.createElement('p');description.textContent=action==='delete'?'将永久删除 FlowCanvas 中的“'+(row.title||'新对话')+'”及其草稿，无法恢复。不会删除项目文件或 Codex 自身保存的历史。':action==='rename'?'给这段对话起一个方便查找的名字。':'“'+(row.title||'新对话')+'”将从当前列表移除。历史记录会保留，可在“已归档”中恢复。';
  const input=document.createElement('input');input.className='session-rename-input';input.value=row.title||'新对话';input.maxLength=120;input.required=true;input.ariaLabel='对话名称';
  const actions=document.createElement('div');actions.className='session-dialog-actions';
  const cancel=document.createElement('button');cancel.type='button';cancel.textContent='取消';cancel.onclick=()=>dialog.close();
  const submit=document.createElement('button');submit.type='submit';submit.className='dialog-primary';submit.textContent=action==='rename'?'保存':action==='delete'?'确认删除':'确认归档';if(action==='delete')submit.classList.add('dialog-danger');
  const feedback=document.createElement('p');feedback.className='dialog-error';feedback.setAttribute('role','alert');feedback.hidden=true;
  form.append(heading,description);if(action==='rename')form.append(input);actions.append(cancel,submit);form.append(feedback,actions);dialog.append(form);document.body.append(dialog);
  form.onsubmit=async event=>{event.preventDefault();if(action==='rename'&&!input.value.trim()){input.setCustomValidity('请输入对话名称');input.reportValidity();return;}submit.disabled=true;cancel.disabled=true;
    const saved=action==='delete'?await deleteSession(row):await updateSession(row,action==='rename'?{title:input.value.trim()}:{archived:true});
    if(saved)dialog.close();else{feedback.textContent='保存失败，请重试。';feedback.hidden=false;submit.disabled=false;cancel.disabled=false;}
  };
  input.oninput=()=>input.setCustomValidity('');
  dialog.addEventListener('cancel',event=>{if(submit.disabled)event.preventDefault();});
  dialog.addEventListener('close',()=>{dialog.remove();$('session-toggle').focus();},{once:true});
  dialog.showModal();if(action==='rename'){input.focus();input.select();}else cancel.focus();
}
const tooltip=document.createElement('div');tooltip.className='session-tooltip';tooltip.setAttribute('role','tooltip');tooltip.hidden=true;document.body.append(tooltip);
/** Delete only after confirmation, then leave no selected identity or draft pointing at it. */
async function deleteSession(row){
  try{
    await request('/sessions/'+row.id+'/delete',{});
    drafts.delete(row.id);
    if(selected===row.id){++switchNumber;unsubscribe?.();unsubscribe=null;selected=null;session=null;$('message').value='';localStorage.removeItem('flowcanvas.session');}
    saveDraft();
    const rows=await list();
    if(!selected){const next=rows.find(item=>!item.archived);if(next)await choose(next.id);else await create();}
    return true;
  }catch(exc){error(exc.message);return false;}
}
/** Change metadata without touching model history, and preserve the active draft. */
async function updateSession(row,changes){
  try{const data=await request('/sessions/'+row.id+'/settings',{title:row.title||'新对话',pinned:!!row.pinned,archived:!!row.archived,...changes});if(selected===row.id)render(data);await list();return true;}catch(exc){error(exc.message);return false;}
}
/** Detach the old SSE view, preserve its draft, then restore the new session. */
async function choose(id){
  const number=++switchNumber;
  saveDraft();saveQuote();
  unsubscribe?.();selected=id;paintAttachments();localStorage.setItem('flowcanvas.session',id);
  $('message').value=drafts.get(id)||'';quoteDraft=quotes.get(id)||[];paintQuote();resizeDraft();session=null;controls();$('reconnect-notice').hidden=true;
  const data=await request('/sessions/'+id);if(number!==switchNumber)return;
  render(data);modelSelection();$('sessions').value=id;paintSessions();
  unsubscribe=subscribe(id,data=>{if(selected===id)render(data);},connected=>{if(selected!==id)return;$('reconnect-notice').hidden=connected;if(!connected)$('connection').textContent='连接中断，正在重新连接…';else controls();});
}
/** Create a conversation without invoking a model. */
async function create(){const data=await request('/sessions',{});await list();await choose(data.id);$('message').focus();}
/** Submit or interrupt exactly one turn; keep a draft when submission is rejected. */
async function send(){
  if(busy)return;busy=true;controls();error('');
  try{
    if(active.has(session?.status)&&!$('message').value.trim()){render(await request('/sessions/'+session.id+'/stop',{}));}
    else{
      const text=$('message').value;if(!text.trim())return;
      if(!session)await create();
      const id=session.id;
      const data=await request('/sessions/'+id+'/messages',{text,quote:quoteText(),model:$('model').value,effort:$('effort').value,speed:speedMode});
      drafts.delete(id);quotes.delete(id);
      if(selected===id){$('message').value='';quoteDraft=[];paintQuote();saveQuote();$('message').style.height='';saveDraft();render(data);}
      await list();
    }
  }catch(exc){error(exc.message);}finally{busy=false;controls();}
}
$('message').addEventListener('input',()=>{saveDraft();controls();resizeDraft();});
$('message').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if($('message').value.trim()&&!$('send').disabled)send();}});
$('send').onclick=send;
$('new-chat').onclick=()=>create().catch(exc=>error(exc.message));
$('sessions').onchange=()=>choose($('sessions').value).catch(exc=>error(exc.message));
$('model').onchange=()=>efforts();
$('backend-codex').onclick=()=>closePicker(true,'backend');
for(const kind of ['model','session','backend','permission','attach']){
  const popup=$(kind+'-popover'),toggle=$(kind+'-toggle');
  toggle.onclick=()=>{
    const open=popup.hidden;closePicker();popup.hidden=!open;
    toggle.setAttribute('aria-expanded',String(open));
    if(open){if(kind==='attach')loadWorkflowMenu();if(kind==='session'){$('session-search').focus();list().catch(exc=>error(exc.message));}else (popup.querySelector('[aria-pressed="true"]')||toggle).focus();}
  };
  popup.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closePicker(true,kind);}
    if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)&&!event.target.matches('input,textarea')){
      const buttons=[...popup.querySelectorAll('button:not(:disabled)')];const index=buttons.indexOf(document.activeElement);
      const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;
      event.preventDefault();buttons[next]?.focus();
    }
  });
  popup.addEventListener('focusout',event=>{if(event.relatedTarget&&!event.relatedTarget.closest('.model-picker'))closePicker();});
}
document.addEventListener('click',event=>{if(!event.target.closest('.model-picker'))closePicker();});
/** Read the curated menu; unsupported launch actions remain explicitly unavailable. */
async function loadWorkflowMenu(){
  const host=$('workflow-options');host.replaceChildren();
  try{
    const {workflows}=await request('/workflows');
    for(const item of workflows){
      const row=document.createElement('button');row.type='button';row.className='workflow-menu-item';row.disabled=!item.registered||!selected;row.setAttribute('aria-pressed',String((attachedWorkflows.get(selected)||[]).some(entry=>entry.id===item.id)));
      const name=document.createElement('div');name.className='workflow-menu-name';name.textContent=item.name;
      const description=document.createElement('p');description.textContent=item.description;
      const status=document.createElement('small');status.textContent=!item.registered?'工作流尚未注册':'点击附加到对话 · 暂不执行';
      row.onclick=()=>{const items=attachedWorkflows.get(selected)||[];if(!items.some(entry=>entry.id===item.id))attachedWorkflows.set(selected,[...items,{id:item.id,name:item.name}]);saveAttachments();paintAttachments();closePicker(true,'attach');};
      row.append(name,description,status);host.append(row);
    }
    if(!workflows.length){const empty=document.createElement('p');empty.className='session-empty';empty.textContent='暂无可用工作流';host.append(empty);}
  }catch{const notice=document.createElement('p');notice.className='session-empty';notice.textContent='工作流列表加载失败，请重新打开菜单重试。';host.append(notice);}
}
/** Load sessions independently of backend readiness so offline history remains readable. */
async function init(){
  try{
    const rows=await list();const saved=localStorage.getItem('flowcanvas.session');
    const visible=rows.filter(row=>!row.archived);if(visible.length)await choose(visible.some(r=>r.id===saved)?saved:visible[0].id);else await create();
    const backends=await request('/backends');models=backends[0].models;
    $('model').replaceChildren();for(const model of models)$('model').add(new Option(model.name,model.id));
    modelSelection();controls();
  }catch(exc){error(exc.message);$('connection').textContent='连接失败 · 刷新页面重试';}
}
/** Append selected context without discarding an unfinished draft. */
$('conversation').addEventListener('compose-quote',event=>{
  quoteDraft.push({text:event.detail,comment:''});paintQuote();saveQuote();$('quote-panel').hidden=false;$('quote-toggle').setAttribute('aria-expanded','true');$('quote-panel').querySelector('.annotation-row:last-child .annotation-comment')?.focus();
});
$('conversation').addEventListener('compose-reuse',event=>{
  $('message').value=[$('message').value.trimEnd(),event.detail].filter(Boolean).join('\n\n');saveDraft();resizeDraft();controls();$('message').focus();
});
$('session-search').oninput=paintSessions;
$('show-archived').onclick=()=>{showArchived=!showArchived;$('show-archived').setAttribute('aria-pressed',String(showArchived));paintSessions();};
$('fold-steps').onclick=()=>{view.foldDetails();closePicker(true,'session');};
$('export-chat').onclick=()=>{
  if(!session)return;
  const text='\n# '+(session.title||'新对话')+'\n\n'+session.items.map(item=>'\n## '+({user:'你',text:'UCAgent',reasoning:'思考摘要',tool:'工具',plan:'计划'}[item.kind]||item.kind)+'\n\n'+(item.input?item.input+'\n\n':'')+(item.text||'')).join('\n\n');
  const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='FlowCanvas-'+session.id.slice(0,8)+'.md';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);closePicker(true,'session');
};
$('quote-toggle').onclick=()=>{const open=$('quote-panel').hidden;$('quote-panel').hidden=!open;$('quote-toggle').setAttribute('aria-expanded',String(open));};
document.addEventListener('click',event=>{if(!event.target.closest('.annotation-tray')&&!event.target.closest('.quote-selection')){$('quote-panel').hidden=true;$('quote-toggle').setAttribute('aria-expanded','false');}});
$('remove-quote').onclick=()=>{quoteDraft=[];paintQuote();saveQuote();};
window.addEventListener('pagehide',()=>{saveDraft();saveQuote();});
document.addEventListener('keydown',event=>{
  if(event.isComposing)return;
  if((event.metaKey||event.ctrlKey)&&event.shiftKey&&event.key.toLowerCase()==='o'){event.preventDefault();create().catch(exc=>error(exc.message));}
  if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();$('session-toggle').click();}
});
$('new-chat').title='新对话 · ⌘/Ctrl Shift O';
$('session-toggle').title='切换对话 · ⌘/Ctrl K';
init();

/** Return home to an empty conversation while preserving existing histories and active tasks. */
document.addEventListener('flowcanvas-home',async()=>{
  closePicker();
  try{
    if(session&&!session.items.length&&!(session.queue||[]).length){
      $('message').focus();return;
    }
    await create();
  }catch(exc){error(exc.message);}
});
