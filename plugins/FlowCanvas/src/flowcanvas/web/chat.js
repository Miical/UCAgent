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
/** Save explicit choices immediately, including choices made before sending a message. */
function rememberModel(){
  const value={model:$('model').value,effort:$('effort').value,speed:speedMode};
  if(!value.model)return;
  try{localStorage.setItem('flowcanvas.model.codex',JSON.stringify(value));if(selected)localStorage.setItem('flowcanvas.model.session.'+selected,JSON.stringify(value));}catch{}
}
/** Restore browser preferences without trusting stale model capabilities. */
function modelPreference(key){try{const value=JSON.parse(localStorage.getItem(key)||'null');return value&&typeof value==='object'?value:null;}catch{return null;}}

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
/** Reflect server-owned authorization as one removable attachment card. */
function paintAttachments(){
  const host=$('attached-workflows');host.replaceChildren();const item=session?.attachment;if(!item||item.task_only)return;
  const card=document.createElement('div');card.className='workflow-chip';card.title=item.name+(item.rtl_file?'\n'+item.rtl_file:'');
  const name=document.createElement('span');name.textContent='◇ '+item.name;
  const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.title='移除工作流';remove.disabled=active.has(session.status);
  remove.onclick=async()=>{try{render(await request('/sessions/'+session.id+'/attachment/remove',{}));}catch(exc){error(exc.message);}};card.append(name,remove);host.append(card);
}
/** Display the execution card in the conversation it belongs to, independent of message text. */
let workflowCardSignature='',allWorkflowRuns=[],workflowRefreshPending=false,workflowRunsLoaded=false;
/** Poll the workspace-wide projection even when another conversation is selected. */
async function refreshWorkflowRuns(){
  if(workflowRefreshPending)return;workflowRefreshPending=true;
  try{allWorkflowRuns=await request('/workflow-runs');workflowRunsLoaded=true;paintWorkflowRun();}catch{}finally{workflowRefreshPending=false;}
}

let selectedWorkflowCard=null;
try{selectedWorkflowCard=localStorage.getItem('flowcanvas.workflow.selected')||null;}catch{}
function selectWorkflowCard(id){
  selectedWorkflowCard=id;
  try{if(id)localStorage.setItem('flowcanvas.workflow.selected',id);else localStorage.removeItem('flowcanvas.workflow.selected');}catch{}
}
/** Navigate to the owning conversation and keep the selected workflow in the detail pane. */
async function openWorkflowDetail(run){
  document.querySelectorAll('.run-expanded:popover-open').forEach(node=>node.hidePopover());
  selectWorkflowCard(run.id);
  document.dispatchEvent(new Event('flowcanvas-open-workflow'));
  paintWorkflowDetail();
  if(selected!==run.conversation_id)await choose(run.conversation_id);
  showWorkflowGroup(run.conversation_id,run.id);
}
/** Reuse the timeline and metadata view without duplicating popover positioning or IDs. */
function paintWorkflowDetail(){
  if(workflowRunsLoaded&&!allWorkflowRuns.some(run=>run.id===selectedWorkflowCard))selectWorkflowCard(null);
  const current=allWorkflowRuns.find(run=>run.id===selectedWorkflowCard);
  $('workflow-label').textContent=current?.name||'选择工作流';
  const stages=current?.stages||[],done=stages.filter(stage=>stage.completed).length;
  const percent=current?.status==='completed'?100:stages.length?Math.round(done/stages.length*100):0;
  $('workflow-detail-ring').style.setProperty('--progress',percent+'%');
  $('workflow-detail-ring').dataset.status=current?.status||'';
  $('workflow-detail-ring').setAttribute('aria-label','工作流进度 '+percent+'%');
  $('workflow-detail-status').textContent=current?current.status==='completed'?'全部阶段已完成':({starting:'启动中',running:'执行中',paused:'已暂停'}[current.status]||current.status)+(current.current_stage?' · '+current.current_stage:''):'选择一个工作流任务';
  paintWorkflowChoices();
  const host=$('detail-content');
  if(!selectedWorkflowCard||!workflowRunsLoaded){host.replaceChildren();return;}
  const source=[...$('workflow-run').querySelectorAll('.run-capsule')].find(node=>node.dataset.id===selectedWorkflowCard);
  const previousDetail=host.querySelector('.workflow-sidebar-detail');
  const expanded=new Map(previousDetail?.dataset.cardId===selectedWorkflowCard?[...previousDetail.querySelectorAll('details[data-section]')].map(node=>[node.dataset.section,node.open]):[]);
  if(!source){host.replaceChildren();const empty=document.createElement('p');empty.className='workflow-detail-empty';empty.textContent='此任务已不存在';host.append(empty);return;}
  const detail=source.querySelector('.run-expanded').cloneNode(true);
  detail.removeAttribute('popover');detail.removeAttribute('style');detail.className='workflow-sidebar-detail';detail.dataset.cardId=selectedWorkflowCard;detail.dataset.status=allWorkflowRuns.find(row=>row.id===selectedWorkflowCard)?.status||'';
  detail.querySelector('.run-expanded-preview')?.remove();
  detail.querySelector(':scope > .run-metadata')?.remove();
  const run=allWorkflowRuns.find(row=>row.id===selectedWorkflowCard);
  if(run){
    const make=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
    const section=(title,open=false)=>{const block=document.createElement('details');block.className='workflow-info-section';block.dataset.section=title;block.open=expanded.get(title)??open;block.append(make('summary',title));const body=document.createElement('div');body.className='workflow-info-body';block.append(body);detail.append(block);return body;};
    const pairs=(block,rows)=>{const dl=document.createElement('dl');dl.className='run-metadata';for(const [label,value]of rows){if(value!==undefined&&value!==null&&value!=='')dl.append(make('dt',label),make('dd',String(value)));}block.append(dl);};
    const overview=document.createElement('section');overview.className='workflow-info-section workflow-overview';
    overview.append(make('h4','运行概览'));
    const runtime=document.createElement('div');runtime.className='workflow-info-body';overview.append(runtime);detail.prepend(overview);
    const runningTime=workflowRunningTime(run);
    pairs(runtime,[['任务',run.tui?.mission],['后端',run.backend],['模型',run.model],['推理强度',run.effort],['开始时间',run.started_at?new Date(run.started_at*1000).toLocaleString():null],['运行时间',runningTime],['工作目录',run.workspace]]);
    if(!run.tui){runtime.append(make('p','此历史任务未采集 TUI 详情；新启动或恢复的任务会提供。'));}
    else{
      const stageInfo=section('阶段详情');
      for(const stage of run.stages||[]){const item=document.createElement('details');item.className='workflow-stage-info';item.dataset.section='stage:'+stage.name;item.open=expanded.get(item.dataset.section)??(stage.name===run.current_stage&&!stage.completed);item.append(make('summary',stage.name));if(stage.description)item.append(make('p',stage.description));pairs(item,[['检查失败次数',stage.fail_count],['检查状态',stage.check_pass===true?'通过':stage.check_pass===false?'未通过':'尚未检查'],['人工检查',stage.human_check?'需要':'无需'],['跳过',stage.skipped?'是':'否'],['耗时',stage.time_cost],['Skill',(stage.skills||[]).join('、')]]);stageInfo.append(item);}
      const files=section('最近变更文件 · '+(run.tui.changed_files?.length||0));
      if(!run.tui.changed_files?.length)files.append(make('p','暂无文件变更'));
      for(const file of run.tui.changed_files||[]){const row=document.createElement('div');row.className='workflow-file-row';row.append(make('span',file.file),make('small',new Date(file.mtime*1000).toLocaleString()));files.append(row);}
      const tools=section('工具调用',true);tools.classList.add('workflow-tool-chips');
      if(!run.tui.tools?.length)tools.append(make('p','暂无工具调用'));
      for(const tool of run.tui.tools||[]){const busy=run.status==='running'&&Boolean(tool.busy);const row=document.createElement('div');row.className='workflow-tool-row';row.classList.toggle('busy',busy);row.append(make('span',tool.name),make('span',(busy?'执行中 · ':'')+tool.count+' 次'));tools.append(row);}
    }
  }
  if(previousDetail?.dataset.cardId===selectedWorkflowCard){
    patchWorkflowNode(previousDetail,detail);
  }else{
    host.replaceChildren(detail);
  }
}
/** Use the same picker rows, search field and selection marks as conversations. */
function paintWorkflowChoices(){
  const host=$('workflow-history-options');host.replaceChildren();
  const query=$('workflow-search').value.trim().toLocaleLowerCase();
  for(const run of allWorkflowRuns.filter(run=>(run.name+' '+run.conversation_title).toLocaleLowerCase().includes(query))){
    const button=document.createElement('button');button.type='button';button.className='model-option session-option';button.setAttribute('aria-pressed',String(run.id===selectedWorkflowCard));
    const text=document.createElement('span');text.className='workflow-option-text';
    const title=document.createElement('span');title.className='session-option-title';title.textContent=run.name;
    const subtitle=document.createElement('small');subtitle.textContent=({starting:'启动中',running:'执行中',paused:'已暂停',completed:'已完成'}[run.status]||run.status)+' · '+run.conversation_title+' · '+new Date(run.started_at*1000).toLocaleTimeString();text.append(title,subtitle);
    const check=document.createElement('span');check.className='model-check';check.textContent=run.id===selectedWorkflowCard?'✓':'';
    button.append(text,check);button.onclick=()=>{selectWorkflowCard(run.id);paintWorkflowDetail();closePicker(true,'workflow');};host.append(button);
  }
  if(!host.children.length){const empty=document.createElement('div');empty.className='picker-heading';empty.textContent=query?'没有匹配的工作流':'暂无工作流任务';host.append(empty);}
}
/** Position the top-layer picker against the viewport, not the sidebar's clipping box. */
function positionWorkflowPicker(){
  const popup=$('workflow-popover'),rect=$('workflow-toggle').getBoundingClientRect();
  const width=Math.min(340,window.innerWidth-24);
  popup.style.width=width+'px';popup.style.left=Math.max(12,Math.min(rect.left,window.innerWidth-width-12))+'px';
  popup.style.top=(rect.bottom+9)+'px';popup.style.maxHeight=Math.max(100,window.innerHeight-rect.bottom-21)+'px';
}
window.addEventListener('resize',()=>{if($('workflow-popover').matches(':popover-open'))positionWorkflowPicker();});
$('workflow-search').oninput=paintWorkflowChoices;
/** Patch existing DOM in place, preserving popover identity, focus and animations. */
function patchWorkflowNode(target,source){
  if(target.nodeType!==source.nodeType||target.nodeName!==source.nodeName){target.replaceWith(source);return;}
  if(target.nodeType===Node.TEXT_NODE){if(target.data!==source.data)target.data=source.data;return;}
  const positioned=target.classList.contains('run-expanded');
  for(const attr of [...target.attributes])if(!source.hasAttribute(attr.name)&&!(positioned&&attr.name==='style'))target.removeAttribute(attr.name);
  for(const attr of source.attributes)if(!(positioned&&attr.name==='style')&&target.getAttribute(attr.name)!==attr.value)target.setAttribute(attr.name,attr.value);
  const children=[...source.childNodes];
  for(let i=0;i<children.length;i++){
    const next=children[i];
    if(!target.childNodes[i]){target.append(next);continue;}
    patchWorkflowNode(target.childNodes[i],next);
  }
  while(target.childNodes.length>children.length)target.lastChild.remove();
}
function workflowRunningTime(run){
  if(run.running_seconds==null)return '未记录';
  const seconds=Math.floor(run.running_seconds);
  return (seconds>=3600?Math.floor(seconds/3600)+' 小时 ':'')+(seconds>=60?Math.floor(seconds%3600/60)+' 分 ':'')+seconds%60+' 秒';
}
function groupedWorkflowRuns(){
  const groups=new Map();
  for(const run of allWorkflowRuns){
    const key=run.conversation_id;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(run);
  }
  return [...groups.values()]
    .sort((a,b)=>Math.min(...b.map(r=>r.started_at||0))-Math.min(...a.map(r=>r.started_at||0)))
    .flatMap(group=>group.sort((a,b)=>(b.started_at||0)-(a.started_at||0)));
}
function showWorkflowGroup(owner,cardId){
  const track=$('workflow-run').querySelector('.run-track');
  if(!track)return;
  const cards=[...track.children].filter(node=>!node.hidden);
  for(const card of cards)card.classList.toggle('same-conversation',card.dataset.conversation===owner);
  const group=cards.filter(card=>card.dataset.conversation===owner).sort((a,b)=>Number(a.style.order)-Number(b.style.order));
  const clicked=group.find(card=>card.dataset.id===cardId);
  if(!group.length)return;
  const first=group[0],last=group.at(-1);
  const fits=last.offsetLeft+last.offsetWidth-first.offsetLeft<=track.clientWidth-40;
  const target=fits?first:clicked||first;
  track.scrollTo({left:Math.max(0,target.offsetLeft-20),behavior:'smooth'});
}
function paintWorkflowRun(){
  const runs=groupedWorkflowRuns(),host=$('workflow-run');
  const signature=JSON.stringify(runs);if(signature===workflowCardSignature)return;
  workflowCardSignature=signature;
  host.hidden=!runs.some(run=>!run.dismissed);
  const focused=document.activeElement?.closest('.run-capsule')?.dataset.id;
  const previousScroll=host.querySelector('.run-track')?.scrollLeft||0;
  const oldTrack=host.querySelector('.run-track');
  const element=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const track=element('div','run-track');track.setAttribute('aria-label','横向滚动工作流任务');
  const left=element('button','run-scroll run-scroll-left','‹'),right=element('button','run-scroll run-scroll-right','›');
  left.type=right.type='button';left.title='向左滚动';right.title='向右滚动';left.setAttribute('aria-label',left.title);right.setAttribute('aria-label',right.title);
  left.onclick=()=>track.scrollBy({left:-280,behavior:'smooth'});right.onclick=()=>track.scrollBy({left:280,behavior:'smooth'});
  const scrollState=()=>{left.hidden=track.scrollLeft<2;right.hidden=track.scrollLeft+track.clientWidth>=track.scrollWidth-2;};
  track.addEventListener('workflow-layout',scrollState);
  track.addEventListener('scroll',()=>{scrollState();track.querySelectorAll(':popover-open').forEach(p=>p.hidePopover());});
  track.addEventListener('wheel',event=>{if(Math.abs(event.deltaY)>Math.abs(event.deltaX)&&track.scrollWidth>track.clientWidth){event.preventDefault();track.scrollLeft+=event.deltaY;}},{passive:false});
  if(!oldTrack)host.append(track,left,right);
  let previousGroup=null;
  for(const [runIndex,run] of runs.entries()){
    const stages=run.stages||[],done=stages.filter(s=>s.completed).length;
    const percent=run.status==='completed'?100:stages.length?Math.round(done/stages.length*100):0;
    const status={starting:'启动中',running:'运行中',completed:'已完成',paused:'已暂停'}[run.status]||run.status;
    const card=element('article','run-capsule');card.hidden=Boolean(run.dismissed);card.dataset.id=run.id;card.dataset.status=run.status;card.tabIndex=0;card.setAttribute('aria-label',run.name+'，'+status+'，'+percent+'%');
    if(run.status==='completed'){
      const remove=element('button','run-remove','×');remove.type='button';remove.title='移除已完成任务';remove.setAttribute('aria-label',remove.title);
      remove.onclick=async event=>{event.stopPropagation();try{await request('/sessions/'+run.conversation_id+'/workflow-runs/'+run.id+'/dismiss',{});await refreshWorkflowRuns();}catch(exc){error(exc.message);}};
      card.append(remove);
    }
    const compact=element('div','run-compact');
    const ring=element('span','run-ring');ring.style.setProperty('--progress',percent+'%');ring.setAttribute('role','progressbar');ring.setAttribute('aria-valuenow',String(percent));ring.setAttribute('aria-valuemin','0');ring.setAttribute('aria-valuemax','100');ring.setAttribute('aria-label','工作流进度');compact.append(ring);
    const labels=element('div','run-labels');labels.append(element('strong','',run.name),element('span','',run.status==='completed'?'全部阶段已完成':run.current_stage||status));compact.append(labels);card.append(compact);
    const expanded=element('div','run-expanded');
    const head=element('div','run-detail-head');head.append(element('span','run-status',status+' · '+done+'/'+stages.length+' 阶段'));
    expanded.append(head);
    const timeline=element('ol','run-timeline');
    const found=stages.findIndex(stage=>stage.name===run.current_stage&&!stage.completed);
    const center=found>=0?found:Math.max(0,stages.findLastIndex(stage=>stage.completed));
    const first=Math.max(0,Math.min(center-1,stages.length-4)),last=Math.min(stages.length,first+4);
    timeline.classList.toggle('fade-top',first>0);timeline.classList.toggle('fade-bottom',last<stages.length);
    for(const [stageIndex,stage] of stages.entries()){const current=stage.name===run.current_stage&&!stage.completed;const row=element('li',stage.completed?'done':current?'current':'pending');row.classList.toggle('near-current',stageIndex>=first&&stageIndex<last);row.append(element('span','run-stage-dot',stage.completed?'✓':''));const text=element('div','');text.append(element('strong','',stage.name));if(stage.completed||current){const duration=element('small','run-stage-duration',stage.time_cost||(stage.kind==='startup'&&run.startup?.started_at&&run.status==='starting'?Math.max(0,Math.floor(Date.now()/1000-run.startup.started_at))+' 秒':run.historical?'未记录':'0 秒'));duration.title='阶段耗时';text.append(duration);}if(current)text.append(element('small','run-stage-state',run.status==='paused'?'暂停':'执行中'));row.append(text);timeline.append(row);}
    if(!stages.length)timeline.append(element('li','run-empty',run.historical?'旧版记录未保存阶段详情':'正在读取工作流阶段…'));expanded.append(timeline);
    const metadata=element('dl','run-metadata');
    for(const [key,value]of [['任务',run.tui?.mission||run.name],['开始时间',run.started_at?new Date(run.started_at*1000).toLocaleString():null],['运行时间',workflowRunningTime(run)],['工作目录',run.workspace]]){if(value){metadata.append(element('dt','',key),element('dd','',value));}}
    expanded.append(metadata);
    expanded.setAttribute('popover','manual');
    let closeTimer;
    const close=()=>{clearTimeout(closeTimer);if(expanded.matches(':popover-open'))expanded.hidePopover();requestAnimationFrame(paintWorkflowRun);};
    const open=()=>{
      clearTimeout(closeTimer);track.querySelectorAll(':popover-open').forEach(p=>{if(p!==expanded)p.hidePopover();});
      const rect=card.getBoundingClientRect(),width=Math.min(340,window.innerWidth-24);
      expanded.style.left=Math.max(12,Math.min(rect.left,window.innerWidth-width-12))+'px';expanded.style.top=(rect.bottom+10)+'px';expanded.style.width=width+'px';expanded.style.maxHeight=Math.max(100,window.innerHeight-rect.bottom-26)+'px';
      if(!expanded.matches(':popover-open'))expanded.showPopover();
    };
    const deferClose=()=>{clearTimeout(closeTimer);closeTimer=setTimeout(()=>{if(!card.matches(':hover')&&!expanded.matches(':hover')&&!card.contains(document.activeElement))close();},180);};
    const preview=compact.cloneNode(true);preview.classList.add('run-expanded-preview');expanded.prepend(preview);
    card.addEventListener('mouseenter',event=>{if(!event.target.closest('.run-remove'))open();});card.addEventListener('mouseleave',deferClose);card.addEventListener('focus',open);card.addEventListener('focusout',deferClose);
    expanded.addEventListener('mouseenter',()=>clearTimeout(closeTimer));expanded.addEventListener('mouseleave',deferClose);expanded.addEventListener('focusout',deferClose);
    card.addEventListener('keydown',event=>{if(event.key==='Escape')close();});
    const navigate=()=>openWorkflowDetail(run).catch(exc=>error(exc.message));
    compact.onclick=navigate;preview.onclick=navigate;
    card.addEventListener('keydown',event=>{if(event.target===card&&(event.key==='Enter'||event.key===' ')){event.preventDefault();navigate();}});
    card.append(expanded);
    const existing=oldTrack&&[...oldTrack.children].find(node=>node.dataset.id===run.id);
    if(existing){
      existing.dataset.status=card.dataset.status;existing.hidden=card.hidden;existing.setAttribute('aria-label',card.getAttribute('aria-label'));
      for(const selector of ['.run-compact','.run-expanded','.run-remove']){
        const old=[...existing.children].find(node=>node.matches(selector)),next=[...card.children].find(node=>node.matches(selector));
        if(old&&next)patchWorkflowNode(old,next);else if(old)old.remove();else if(next)existing.append(next);
      }
    }else (oldTrack||track).append(card);
    const visibleCard=existing||card;
    visibleCard.dataset.conversation=run.conversation_id;visibleCard.style.order=runIndex;
    visibleCard.classList.toggle('same-conversation',run.conversation_id===allWorkflowRuns.find(item=>item.id===selectedWorkflowCard)?.conversation_id);
    visibleCard.style.marginLeft=!run.dismissed&&previousGroup&&previousGroup!==run.conversation_id?'12px':'0';
    if(!run.dismissed)previousGroup=run.conversation_id;
    card.addEventListener('keydown',event=>{if(event.key==='Escape'){card.blur();document.activeElement?.blur();}});
    if(focused===run.id)card.focus({preventScroll:true});
  }
  if(oldTrack){
    for(const node of [...oldTrack.children])if(!runs.some(run=>run.id===node.dataset.id))node.remove();
    oldTrack.dispatchEvent(new Event('workflow-layout'));
  }else{track.scrollLeft=previousScroll;requestAnimationFrame(scrollState);}
  paintWorkflowDetail();
}

/** Attach or launch from the common menu without workflow-specific parameter dialogs. */
async function configureWorkflow(item,direct){
  const owner=selected;closePicker();
  try{const payload={workflow_id:item.id};const data=await request(direct?'/workflows/direct':'/sessions/'+owner+'/attachment',direct?{...payload,model:$('model').value,effort:$('effort').value}:payload);if(direct){await list();await choose(data.id);}else if(selected===owner)render(data);}
  catch(exc){error(exc.message);}
}
/** Show actionable API failures independently of generated message content. */
function error(message){$('chat-error').textContent=message||'';$('chat-error').hidden=!message;}
/** Keep send/stop state tied to the real backend turn. */
function controls(){
  const running=active.has(session?.status)||session?.children_running>0;
  $('send').textContent=running&&!$('message').value.trim()?'■':'↑';$('send').classList.toggle('is-stop',running);$('send').ariaLabel=running?($('message').value.trim()?(active.has(session?.status)?'加入队列':'发送消息'):session?.children_running>0?'停止任务':'停止回复'):'发送消息';$('send').title=$('send').ariaLabel;
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
  const choices=[['full','完全访问','可直接修改文件、执行命令'],['workflow','工作流执行','对话只读，通过已授权的工作流执行'],['readonly','只读分析','仅阅读和分析，不允许启动工作流']];
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
    button.append(name,mark);button.onclick=()=>{speedMode=value;updateSpeed();rememberModel();closePicker(true,'model');};
    $('speed-options').append(button);
  }
}
/** Reflect persisted selections when restoring a conversation. */
function modelSelection(){
  if(!models.length)return;
  const fallback=models.find(m=>m.default)||models[0];
  const remembered=modelPreference('flowcanvas.model.codex');
  const local=selected?modelPreference('flowcanvas.model.session.'+selected):null;
  const preference=[local,session,remembered].find(value=>value?.model&&models.some(m=>m.id===value.model));
  $('model').value=preference?.model||fallback?.id||'';
  speedMode=['standard','fast','ultrafast'].includes(preference?.speed)?preference.speed:'standard';
  efforts(preference?.effort);
}
/** Render the selected session; other active conversations keep running independently. */
const seenAITestRuns=new Map();
function render(data){
  const aiRun=data.ai_test_run;
  if(seenAITestRuns.has(data.id)&&aiRun?.run?.id&&aiRun.project&&seenAITestRuns.get(data.id)!==aiRun.run.id){
    document.dispatchEvent(new CustomEvent('flowcanvas-test-run',{detail:aiRun}));
  }
  seenAITestRuns.set(data.id,aiRun?.run?.id||null);
  session=data;document.dispatchEvent(new CustomEvent('flowcanvas-session',{detail:{id:data.id}}));view.render(data);controls();error(data.error);paintQueue();paintAttachments();paintWorkflowRun();
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
function closePicker(focus=false,kind='model'){for(const name of ['model','session','backend','permission','attach','workflow']){const popup=$(name+'-popover');if(popup.matches(':popover-open'))popup.hidePopover();popup.hidden=true;$(name+'-toggle').setAttribute('aria-expanded','false');}if(focus)$(kind+'-toggle').focus();}
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
    button.append(name,mark);button.onclick=()=>{const previous=$('effort').value;$('model').value=model.id;efforts(previous);rememberModel();$('model-options').querySelector('[aria-pressed="true"]')?.focus();};
    $('model-options').append(button);
  }
  $('effort-options').replaceChildren();
  for(const value of chosen?.efforts||[]){
    const button=document.createElement('button');button.type='button';button.textContent=value;
    button.setAttribute('aria-pressed',String(value===$('effort').value));
    button.onclick=()=>{$('effort').value=value;rememberModel();paintPicker();closePicker(true,'model');};
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
  document.dispatchEvent(new CustomEvent('flowcanvas-session',{detail:{id}}));
  unsubscribe?.();selected=id;localStorage.setItem('flowcanvas.session',id);
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
    if((active.has(session?.status)||session?.children_running>0)&&!$('message').value.trim()){render(await request('/sessions/'+session.id+'/stop',{}));}
    else{
      const text=$('message').value;if(!text.trim())return;
      if(!session)await create();
      const id=session.id;
      rememberModel();
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
$('model').onchange=()=>{efforts($('effort').value);rememberModel();};
$('effort').onchange=()=>{rememberModel();paintPicker();};
$('backend-codex').onclick=()=>closePicker(true,'backend');
for(const kind of ['model','session','backend','permission','attach','workflow']){
  const popup=$(kind+'-popover'),toggle=$(kind+'-toggle');
  toggle.onclick=()=>{
    const open=popup.hidden;closePicker();popup.hidden=!open;
    toggle.setAttribute('aria-expanded',String(open));
    if(open){if(kind==='workflow'){paintWorkflowChoices();positionWorkflowPicker();popup.showPopover();$('workflow-search').focus();return;}if(kind==='attach')loadWorkflowMenu();if(kind==='session'){$('session-search').focus();list().catch(exc=>error(exc.message));}else (popup.querySelector('[aria-pressed="true"]')||toggle).focus();}
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
      const row=document.createElement('div');row.className='workflow-menu-item workflow-choice';
      const info=document.createElement('div');info.className='workflow-choice-info';
      const name=document.createElement('div');name.className='workflow-menu-name';name.textContent=item.name;
      const description=document.createElement('p');description.textContent=item.description;info.append(name,description);
      const footer=document.createElement('div');footer.className='workflow-choice-footer';
      const canAttach=item.can_start&&Boolean(selected)&&session?.permission_mode!=='readonly';
      const attach=document.createElement('span');attach.className='workflow-attach-action';attach.classList.toggle('is-disabled',!canAttach);
      const label=document.createElement('span');label.textContent=session?.permission_mode==='readonly'?'只读模式不可附加':'附加到当前对话';
      const plus=document.createElementNS('http://www.w3.org/2000/svg','svg');plus.setAttribute('viewBox','0 0 20 20');plus.setAttribute('aria-hidden','true');const path=document.createElementNS(plus.namespaceURI,'path');path.setAttribute('d','m7 11 5-5a2.1 2.1 0 0 1 3 3l-7 7a3.5 3.5 0 0 1-5-5l7-7a4.9 4.9 0 0 1 7 7l-6 6');plus.append(path);attach.append(plus,label);footer.append(attach);
      const direct=document.createElement('button');direct.className='workflow-direct';direct.type='button';direct.title='直接运行';direct.ariaLabel='直接运行 '+item.name;const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');const circle=document.createElementNS(icon.namespaceURI,'circle');circle.setAttribute('cx','12');circle.setAttribute('cy','12');circle.setAttribute('r','9');const triangle=document.createElementNS(icon.namespaceURI,'path');triangle.setAttribute('d','m10 8 6 4-6 4Z');icon.append(circle,triangle);direct.append(icon);direct.disabled=!item.can_start||!models.length;direct.onclick=event=>{event.stopPropagation();configureWorkflow(item,true);};
      row.tabIndex=canAttach?0:-1;row.setAttribute('aria-label','附加 '+item.name+' 到当前对话');
      row.onclick=()=>{if(canAttach)configureWorkflow(item,false);};
      row.onkeydown=event=>{if(event.target===row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();if(canAttach)configureWorkflow(item,false);}};
      row.classList.toggle('can-attach',canAttach);row.append(info,direct,footer);host.append(row);
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
refreshWorkflowRuns();
setInterval(()=>{if(!document.hidden)refreshWorkflowRuns();},500);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshWorkflowRuns();});

/** Return home to an empty conversation while preserving existing histories and active tasks. */
document.addEventListener('flowcanvas-home',async()=>{
  document.dispatchEvent(new CustomEvent('flowcanvas-session',{detail:{id:null}}));
  closePicker();
  try{
    if(session&&!session.items.length&&!(session.queue||[]).length){
      $('message').focus();return;
    }
    await create();
  }catch(exc){error(exc.message);}
});

/** Page actions reuse the normal conversation execution and permission endpoints. */
window.FlowCanvasAI={
  open:async id=>{await list();await choose(id);},
  async startCase({text,current=true,project,requirements,design=null}){
    const model=$('model').value,effort=$('effort').value;
    const caseAction=['run-testcase','delete-testcase'].includes(design?.kind);
    const taskTitle=design?.kind==='functional-coverage'?'分析功能覆盖率':design?.kind==='run-testcase'?'运行测试用例':design?.kind==='delete-testcase'?'删除测试用例':design?.kind==='line-coverage'?'分析行覆盖率':design?'解释设计':'新增测试用例';
    if(!model)throw Error('请先选择可用模型');
    let owner=session;
    if(current){
      if(!owner)throw Error('请先打开一个对话');
      if(active.has(owner.status))throw Error('当前对话正在执行，请使用独立任务对话');
      if((!design||caseAction)&&owner.permission_mode!=='full')throw Error('请先将当前对话切换为完全访问，或使用独立任务对话');
      if(owner.attachment&&(design||owner.attachment.workflow_id!=='generate-testcase'))throw Error('当前对话已附加其他工作流，请使用独立任务对话');
    }else{
      owner=await request('/sessions',{});
      await request('/sessions/'+owner.id+'/settings',{title:design?taskTitle+' · '+design.module:'AI 新增测试用例'});
      await request('/sessions/'+owner.id+'/permissions',{mode:design&&!caseAction?'readonly':'full'});
    }
    if(!design)await request('/sessions/'+owner.id+'/attachment',{workflow_id:'generate-testcase',task_only:true,rtl_file:project.rtl?.top_file?project.path+'/'+project.rtl.top_file:'',dut:project.rtl?.top||'RTL'});
    const result=await request('/sessions/'+owner.id+'/messages',{text,model,effort,speed:speedMode,task_card:{title:taskTitle,workflow:design?'':'新增测试用例',project:project.name,requirements:design?design.instance+(requirements?'\n'+requirements:''):requirements||''}});
    await list();await choose(owner.id);
    return result;
  }
};
