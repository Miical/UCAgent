/** Safe message rendering with stable nodes, preserving selection and open tool blocks. */
import {markdown} from './markdown-renderer.js';
const labels = {user:'你', text:'UCAgent', reasoning:'思考摘要', tool:'工具', plan:'计划'};
const states = {running:'进行中', inProgress:'进行中', completed:'完成', failed:'失败', declined:'未授权', interrupted:'已停止', error:'失败'};

/** Construct safe small UI elements without interpreting model-authored markup. */
function element(tag, className, text='') {const node=document.createElement(tag);node.className=className;node.textContent=text;return node;}
/** Format elapsed backend wall time without inventing durations for old history. */
function elapsed(start,end){if(!start)return '';const seconds=Math.max(0,Math.floor((end||Date.now()/1000)-start));return seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`;}
const runningStatuses=new Set(['running','inProgress']);

export class ConversationView {
  /** Keep a keyed timeline, live activity line and explicit return-to-bottom control. */
  constructor(root) {
    this.root=root;this.nodes=new Map();this.groups=new Map();this.identity=null;this.session=null;
    this.jump=element('button','jump-latest','↓ 回到最新');this.jump.hidden=true;
    this.jump.onclick=()=>{root.scrollTo({top:root.scrollHeight,behavior:'smooth'});};
    root.after(this.jump);
    this.quote=element('button','quote-selection','引用选中文字');this.quote.hidden=true;root.after(this.quote);
    let quoted='';
    // Only message bodies are quotable; welcome copy and UI chrome are not.
    const messageBody=node=>{const element=node?.nodeType===Node.ELEMENT_NODE?node:node?.parentElement;const body=element?.closest('.message-content');return body&&root.contains(body)&&body.closest('.message')?body:null;};
    document.addEventListener('selectionchange',()=>{const selection=window.getSelection();quoted=selection&&messageBody(selection.anchorNode)&&messageBody(selection.anchorNode)===messageBody(selection.focusNode)?selection.toString().trim():'';this.quote.hidden=!quoted;if(quoted&&selection.rangeCount){const rect=selection.getRangeAt(0).getBoundingClientRect();this.quote.style.left=Math.max(10,Math.min(window.innerWidth-150,rect.left))+'px';this.quote.style.top=Math.max(10,Math.min(window.innerHeight-45,rect.bottom+8))+'px';}});
    this.quote.onmousedown=event=>event.preventDefault();
    this.quote.onclick=()=>{if(quoted)root.dispatchEvent(new CustomEvent('compose-quote',{detail:quoted}));this.quote.hidden=true;window.getSelection()?.removeAllRanges();};
    root.addEventListener('scroll',()=>{this.jump.hidden=root.scrollHeight-root.scrollTop-root.clientHeight<130;});
    this.timer=setInterval(()=>this.tick(),1000);
  }
  /** Collapse running and completed details until the user explicitly expands them. */
  foldDetails(){for(const entry of [...this.nodes.values(),...this.groups.values()])if(entry.root.tagName==='DETAILS'){entry.touched=true;entry.root.open=false;}}
  /** Refresh only time labels; streaming content and selection remain untouched. */
  tick(){
    for(const entry of this.nodes.values()){
      if(entry.taskStatus){
        const lastUser=[...(this.session?.items||[])].reverse().find(item=>item.kind==='user'&&!item.steered);
        const run=entry.item.task_run||(lastUser?.id===entry.item.id?this.session:null);
        const running=run&&['starting','running','stopping'].includes(run.status);
        const state=running?(run.status==='stopping'?'停止中':'运行中'):({completed:'已完成',error:'失败',failed:'失败',interrupted:'已停止'}[run?.status]||'已发送');
        entry.taskStatus.textContent=[run?.started_at?elapsed(run.started_at,run.ended_at):'',state].filter(Boolean).join(' · ');
        entry.taskStatus.classList.toggle('is-running',!!running);
      }
      if(entry.time)entry.time.textContent=elapsed(entry.item.started_at,entry.item.ended_at||(!runningStatuses.has(entry.item.status)?this.session?.ended_at:null));
    }
    if(this.liveTime&&this.session)this.liveTime.textContent=elapsed(this.session.started_at,this.session.ended_at);
  }
  /** Create a stable text bubble or expandable reasoning/tool row. */
  create(item,parent){
    const collapsible=['tool','plan'].includes(item.kind);
    const root=element(collapsible?'details':'article','message '+item.kind);
    const heading=element(collapsible?'summary':'div','message-label');
    const icon=element('span','activity-icon');icon.setAttribute('aria-hidden','true');
    if(item.kind==='text'||item.kind==='reasoning'){
      const star=document.createElementNS('http://www.w3.org/2000/svg','svg');
      star.setAttribute('viewBox','0 0 16 16');
      const path=document.createElementNS('http://www.w3.org/2000/svg','path');
      path.setAttribute('d','M8 1.5 9.9 6.1 14.5 8 9.9 9.9 8 14.5 6.1 9.9 1.5 8 6.1 6.1Z');
      star.append(path);icon.append(star);
    }
    const title=element('span','activity-title');const state=element('span','activity-state');
    const time=element('span','activity-time');
    heading.append(icon,title,state,time);
    if(collapsible)heading.append(element('span','disclosure','›'));
    const content=element('div','message-content');root.append(heading);
    if(item.kind==='user'&&item.quote){const quote=element('details','message-quote');quote.append(element('summary','','引用内容'),element('pre','',item.quote));root.append(quote);}
    root.append(content);parent.append(root);
    const entry={root,heading,icon,title,state,time,content,text:null,item,touched:false};
    if(collapsible){
      heading.addEventListener('click',()=>{entry.touched=true;});
      const copy=element('button','copy-detail','复制详情');copy.type='button';
      copy.onclick=async()=>{try{await navigator.clipboard.writeText([entry.item.input,entry.item.text].filter(Boolean).join('\n\n'));copy.textContent='已复制';}catch{copy.textContent='复制失败';}setTimeout(()=>copy.textContent='复制详情',1500);};root.append(copy);
    }
    if(!collapsible&&item.kind!=='reasoning'){
      const actions=element('div','message-actions');const copy=element('button','copy-message');
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 20 20');svg.setAttribute('aria-hidden','true');
      const path=document.createElementNS('http://www.w3.org/2000/svg','path');
      const shape='M7 6V3h10v11h-3 M3 6h11v11H3Z';path.setAttribute('d',shape);svg.append(path);copy.append(svg);
      copy.title='复制消息';copy.setAttribute('aria-label','复制消息');
      copy.onclick=async()=>{
        try{await navigator.clipboard.writeText(entry.copyText??entry.item.text);path.setAttribute('d','m4 10 4 4 8-8');copy.title='已复制';copy.setAttribute('aria-label','已复制');}
        catch{copy.title='复制失败';copy.setAttribute('aria-label','复制失败');}
        setTimeout(()=>{path.setAttribute('d',shape);copy.title='复制消息';copy.setAttribute('aria-label','复制消息');},1500);
      };
      entry.timestamp=element('time','message-timestamp');
      actions.append(copy,entry.timestamp);
      if(item.kind==='user'){
        const reuse=element('button','reuse-message','↗');reuse.type='button';reuse.title='放回输入框修改';reuse.ariaLabel=reuse.title;
        reuse.onclick=()=>this.root.dispatchEvent(new CustomEvent('compose-reuse',{detail:entry.item.text}));actions.append(reuse);
      }
      entry.actions=actions;entry.copy=copy;
      if(item.kind==='text')actions.hidden=true;
      root.append(actions);
    }
    this.nodes.set(item.id,entry);return entry;
  }
  /** Reconcile native item states; animate only actual in-flight items and turns. */
  render(session){
    session={...session,items:session.items.filter(item=>item.title!=='subAgentActivity')};
    const atBottom=this.root.scrollHeight-this.root.scrollTop-this.root.clientHeight<100;
    const switched=this.identity!==session.id;this.session=session;
    if(switched){
      this.identity=session.id;this.nodes.clear();this.groups.clear();this.root.replaceChildren();this.live=null;
    }
    if(!session.items.length){
      if(!this.root.childNodes.length){
        const welcome=element('div','chat-welcome');welcome.append(element('div','welcome-mark','✦'),element('span','welcome-eyebrow','UCAgent FlowCanvas'),element('h3','','一起，把验证想清楚。'),element('p','','分析设计、梳理场景，或从一个问题开始。'));this.root.append(welcome);
      }
      return;
    }
    this.root.querySelector('.chat-welcome')?.remove();
    let group=null, turnUser=null;
    for(const item of session.items){
      if(item.kind==='user'&&!item.steered)turnUser=item.id;
      let parent=this.root;
      if(item.kind==='tool'){
        if(!group){
          const key=item.id;
          group=this.groups.get(key);
          if(!group){
            const root=element('details','tool-group');const summary=element('summary','tool-group-header');
            const icon=element('span','activity-icon');const label=element('span','activity-title');const badge=element('span','step-count');
            summary.append(icon,label,badge,element('span','disclosure','›'));
            const body=element('div','tool-group-body');root.append(summary,body);this.root.append(root);
            group={root,body,label,icon,badge,items:[],touched:false};const ownedGroup=group;summary.addEventListener('click',()=>{ownedGroup.touched=true;});
            this.groups.set(key,group);
          }
          group.items=[];
        }
        group.items.push(item);parent=group.body;
      }else group=null;
      const entry=this.nodes.get(item.id)||this.create(item,parent);
      const wasRunning=entry.item&&runningStatuses.has(entry.item.status);entry.item=item;
      if(entry.timestamp){
        const value=item.ended_at||item.started_at;
        entry.timestamp.hidden=!value;
        if(value){const date=new Date(value*1000);entry.timestamp.dateTime=date.toISOString();entry.timestamp.textContent=date.toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false});entry.timestamp.title=date.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})+' 北京时间';}
      }
      const live=runningStatuses.has(item.status)&&['starting','running','stopping'].includes(session.status);
      // Some backends emit reasoning lifecycle events without any public summary.
      entry.root.hidden=item.kind==='reasoning'&&!live&&!item.text?.trim();
      entry.root.classList.toggle('is-running',live);
      entry.root.classList.toggle('is-error',['failed','error'].includes(item.status));
      if(item.kind!=='text'&&item.kind!=='reasoning')entry.icon.textContent=live?'':(item.kind==='tool'?(['failed','error'].includes(item.status)?'!':item.status==='interrupted'?'−':'✓'):item.kind==='reasoning'?'✧':'✦');
      entry.title.textContent=item.kind==='reasoning'?(item.text?.trim().replace(/\*\*/g,'').replace(/\s+/g,' ')||(live?'正在思考…':'思考摘要')):item.kind==='tool'?(item.title||'工具调用'):item.kind==='user'?(item.steered?'你 · 引导':'你'):item.kind==='plan'?'执行计划':'UCAgent';
      entry.title.title=entry.title.textContent;
      entry.state.textContent=['tool','plan'].includes(item.kind)?(states[item.status]||item.status):'';
      entry.root.dataset.turn=turnUser||'';
      if(entry.text!==item.text||entry.input!==item.input){
        entry.text=item.text;entry.input=item.input;
        if(item.kind==='tool'){
          entry.content.replaceChildren();
          for(const [label,value]of [['输入',item.input],['输出',item.text]])if(value){const box=element('section','tool-section');box.append(element('div','tool-section-label',label),element('pre','',value));entry.content.append(box);}
          if(!item.input&&!item.text)entry.content.append(element('span','empty-output',live?'等待工具返回…':'无文本输出'));
        }else if(item.kind!=='reasoning'){
          const follow=entry.content.scrollHeight-entry.content.scrollTop-entry.content.clientHeight<40;
          const task=item.kind==='user'?item.task_card:null;
          entry.content.classList.toggle('ai-task-message',!!task);
          if(task){
            const card=element('section','ai-task-surface ai-task-sent');
            const heading=element('div','ai-task-heading');entry.taskStatus=element('span','ai-task-run-status');entry.taskStatus.setAttribute('role','status');heading.append(element('strong','ai-task-title','✦ '+task.title));if(task.workflow&&task.title!=='解释设计')heading.append(element('span','ai-workflow-binding','◇ '+task.workflow));
            heading.append(entry.taskStatus);
            card.append(heading,element('p','ai-task-objective',task.title==='分析行覆盖率'?'分析覆盖缺口，定位未覆盖代码并建议补测场景。':task.title==='解释设计'?'阅读源码，解释模块功能、接口与内部逻辑。':'生成可运行用例，补齐说明与源码绑定。'),element('p','ai-task-requirements',task.requirements||'根据现有验证目标补充一个测试用例'));
            entry.content.replaceChildren(card);
          }else markdown(entry.content,item.text||'');
          if(live&&item.kind==='reasoning'&&follow)entry.content.scrollTop=entry.content.scrollHeight;
        }
      }
    }
    for(const group of this.groups.values()){
      const count=group.items.length;const running=group.items.some(i=>runningStatuses.has(i.status))&&['starting','running','stopping'].includes(session.status);
      const failed=group.items.some(i=>['failed','error'].includes(i.status));
      const interrupted=group.items.some(i=>i.status==='interrupted');
      group.root.classList.toggle('is-running',running);group.root.classList.toggle('is-error',failed);
      group.icon.textContent=running?'':failed?'!':interrupted?'−':'✓';group.label.textContent=running?'正在调用工具':failed?'执行步骤 · 有失败':interrupted?'执行步骤 · 已停止':'执行步骤';group.badge.textContent='· '+count;
      if(!group.touched)group.root.open=running||failed;
    }
    if(!this.live){
      this.live=element('div','turn-status');this.live.setAttribute('role','status');
      this.liveIcon=element('span','status-orb');this.liveLabel=element('span','status-label');this.liveTime=element('span','activity-time');
      this.live.append(this.liveIcon,this.liveLabel,this.liveTime);
    }
    // Place a completed turn beside its final reply; active progress stays below the stream.
    let finalReply=null;
    if(session.status==='completed'){
      for(let index=session.items.length-1;index>=0;index--){
        const item=session.items[index];
        if(item.kind==='user'&&!item.steered)break;
        if(item.kind==='text'){finalReply=this.nodes.get(item.id);break;}
      }
    }
    // Native text items can finish before the turn does (commentary followed by tools).
    // One toolbar belongs to the last reply of a finished turn, not to each text item.
    const turns=[];
    for(const item of session.items){
      if(item.kind==='user'&&!item.steered||!turns.length)turns.push([]);
      if(item.kind==='text')turns.at(-1).push(item);
    }
    let latestCopy=null;
    for(const entry of this.nodes.values())if(entry.item.kind==='text'){entry.actions.hidden=true;entry.copyText='';}
    turns.forEach((items,index)=>{
      const ended=index<turns.length-1||['completed','interrupted','error'].includes(session.status);
      if(!ended||!items.length)return;
      const entry=this.nodes.get(items.at(-1).id);
      entry.actions.hidden=false;entry.copyText=items.map(item=>item.text||'').filter(Boolean).join('\n\n');
      entry.copy.title='复制本轮全部回复';entry.copy.setAttribute('aria-label','复制本轮全部回复');
      if(index===turns.length-1)latestCopy=entry;
    });
    for(const entry of this.nodes.values()){
      entry.root.classList.toggle('latest-completed-reply',entry===latestCopy);
      const completion=entry.item.turn_completion||(entry===finalReply?session:null);
      if(completion&&entry.item.kind==='text'){
        if(!entry.completion){entry.completion=element('span','saved-turn-status');entry.heading.append(entry.completion);}
        const duration=completion.started_at&&completion.ended_at?elapsed(completion.started_at,completion.ended_at):'';
        entry.completion.textContent=(entry===latestCopy&&session.children_running>0?'主对话已回复 · '+session.children_running+' 个子 Agent 执行中':'本轮已完成')+(duration?' · '+duration:'');
      }
    }
    this.live.hidden=Boolean(finalReply);
    this.root.append(this.live);
    const active=['starting','running','stopping'].includes(session.status);
    const last=session.items.at(-1);
    this.live.classList.toggle('active',active);
    this.live.classList.toggle('stopped',session.status==='interrupted');
    this.liveIcon.textContent=active?'':session.status==='error'?'!':session.status==='interrupted'?'−':'✓';
    this.liveLabel.textContent=session.status==='stopping'?'正在停止':session.status==='starting'?'正在连接模型':session.status==='error'?'本轮未完成':session.status==='interrupted'?'已停止生成':!active?'本轮已完成':last?.kind==='tool'&&runningStatuses.has(last.status)?'正在执行工具':last?.kind==='text'&&runningStatuses.has(last.status)?'正在回复':last?.kind==='reasoning'&&runningStatuses.has(last.status)?'正在思考':'正在处理';
    this.tick();
    if(atBottom||switched){this.root.scrollTop=this.root.scrollHeight;this.jump.hidden=true;}
  }
}
