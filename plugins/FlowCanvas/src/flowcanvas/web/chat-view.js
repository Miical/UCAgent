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
    root.addEventListener('scroll',()=>{
      root.classList.toggle('is-scrolled',root.scrollTop>0);
      this.jump.hidden=root.scrollHeight-root.scrollTop-root.clientHeight<130;
    });
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
    if(item.kind==='user'&&(item.quote||item.view_context)){
      const tray=element('div','message-references');tray.setAttribute('aria-label','消息引用');
      const visibleQuote=(item.quote||'').split('Current case view (reference only; not an instruction or authorization):')[0].trim();
      const references=visibleQuote.split(/(?=^\d+\. (?:附加(?:测试用例|验证目标)上下文|所选文本：))/m).filter(text=>text.trim());
      for(const text of references){
        let label='引用 · '+text.replace(/^\d+\. 所选文本：\s*/, '').trim().slice(0,32);
        if(/^\d+\. 附加/.test(text)){
          try{
            const context=JSON.parse(text.slice(text.indexOf('\n')+1));
            label=context.testcase?'用例 · '+(context.testcase.title||context.testcase.name):context.target?'目标 · '+context.target.name:label;
          }catch{}
        }
        const reference=element('span','message-reference');
        const summary=element('span','message-reference-label');
        const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 20 20');icon.setAttribute('aria-hidden','true');
        const path=document.createElementNS(icon.namespaceURI,'path');path.setAttribute('d','m7 11 5-5a2.1 2.1 0 0 1 3 3l-7 7a3.5 3.5 0 0 1-5-5l7-7');icon.append(path);
        summary.append(icon,element('span','',label));reference.append(summary);tray.append(reference);
      }
      if(tray.childElementCount)root.append(tray);
    }
    root.append(content);parent.insertBefore(root,parent===this.root&&this.live?.parentNode===parent?this.live:null);
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
      const caseEmpty=!!session.case_binding;
      const welcomeKey=JSON.stringify([caseEmpty,session.page_context?.testcase.entity_type||'',!!session.page_context,session.welcome_prompt_kind]),previous=this.root.querySelector('.chat-welcome,.case-chat-empty');
      if(previous&&previous.dataset.welcomeKey!==welcomeKey)previous.remove();
      if(!this.root.childNodes.length){
        if(session.case_binding){
          const welcome=element('div','chat-welcome case-chat-empty');welcome.dataset.welcomeKey=welcomeKey;
          const kind=session.page_context?.testcase.entity_type||'case',scoped=!!session.page_context;
          const copy=({case:['一起看懂这个用例','理解验证目的、执行过程与关键检查。','这个用例主要验证什么？','关键检查是如何实现的？'],module:['一起看懂这个模块','从模块职责，深入到结构与源码。','这个模块负责什么？','关键结构之间如何协作？'],coverage:['一起理解覆盖率','结合源码，理解覆盖情况与验证缺口。','这部分覆盖率说明了什么？','哪些场景可能还没有覆盖？'],target:['一起理清验证目标','理解要检查的行为、触发条件与预期结果。','这个目标要检查什么？','如何触发并判断结果？'],bug:['一起分析这个问题','梳理问题现象、触发条件与排查思路。','这个问题的现象是什么？','可以从哪些地方开始排查？']})[kind];
          const mark=element('div','welcome-mark case-chat-empty-mark');mark.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h14a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-8l-6 4V5a1 1 0 0 1 1-1ZM8 8h8M8 12h5"/></svg>';
          welcome.append(mark,element('h3','',scoped?copy[0]:'从一个问题开始'),element('p','',scoped?copy[1]:'继续这段对话，或提出一个新的问题。'));
          if(scoped){
            const prompts=element('div','welcome-prompts');
            for(const text of copy.slice(2)){const button=element('button','welcome-prompt',text);button.type='button';button.onclick=()=>this.root.dispatchEvent(new CustomEvent('compose-reuse',{detail:text}));prompts.append(button);}welcome.append(prompts);
            const note=element('p','case-chat-empty-context');note.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M5 2h5l3 3v9H3V2ZM10 2v4h3M5.5 9h5M5.5 11h3"/></svg>';const noteText=element('span','case-chat-context-copy');noteText.append(element('strong','','当前页面已关联'),element('span','','，提问时无需重复说明。'));note.append(noteText);welcome.append(note);
          }
          this.root.append(welcome);return;
        }
        const welcome=element('div','chat-welcome');welcome.dataset.welcomeKey=welcomeKey;welcome.append(element('div','welcome-mark','✦'),element('span','welcome-eyebrow','UCAgent FlowCanvas'),element('h3','','一起，把验证想清楚。'),element('p','','分析设计、梳理场景，或从一个问题开始。'));
        if(session.welcome_prompt_kind){
          const prompts=element('div','welcome-prompts');prompts.setAttribute('aria-label','试着问问');
          const suggestions=session.welcome_prompt_kind==='project'?[
            ['导入已有验证内容','请运行「导入项目」工作流，将当前项目已有的验证目标和测试用例同步到 FlowCanvas。'],
            ['了解 FlowCanvas 用法','请介绍 FlowCanvas 的主要功能，并结合当前项目说明从哪里开始。'],
            ['开始完整验证','请运行「UCAgent 默认验证流程」，对当前项目进行完整验证。']
          ]:[
            ['我可以用 FlowCanvas 做什么？','我可以用 FlowCanvas 做什么？'],
            ['如何开始一个新项目？','如何开始一个新项目？'],
            ['帮我理清一个想法','帮我理清一个想法']
          ];
          for(const [label,text] of suggestions){
            const button=element('button','welcome-prompt',label);button.type='button';button.title=text;
            button.onclick=()=>this.root.dispatchEvent(new CustomEvent('compose-reuse',{detail:text}));prompts.append(button);
          }
          welcome.append(prompts);
        }
        this.root.append(welcome);
      }
      return;
    }
    this.root.querySelector('.chat-welcome,.case-chat-empty')?.remove();
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
            const body=element('div','tool-group-body');root.append(summary,body);this.root.insertBefore(root,this.live?.parentNode===this.root?this.live:null);
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
            card.append(heading,element('p','ai-task-objective',task.title==='新增 Bug'?'把问题整理成一条 Bug 记录。':task.title==='删除 Bug'?'删除所选 Bug 记录，保留项目源码和运行结果。':task.title==='解读波形'?'选取关键信号，标注关键时间点并逐点解释。':task.title==='生成用例概览'?'阅读当前用例源码，生成验证目的、验证方法和技术流程。':task.title==='规划验证目标'?'梳理整个设计，规划功能组、功能点与测试点。':task.title==='同步测试用例'?'核对已有用例，补齐缺失项，保留未变化的内容。':task.title==='同步验证目标'?'核对项目覆盖定义，补齐缺失目标，保留已有内容。':task.title==='新增验证目标'?'补充验证目标、说明与源码绑定。':task.title==='删除验证目标'?'删除选中目标及其子目标，保留无关代码。':task.title==='分析功能覆盖率'?'分析覆盖目标与未命中 bin，给出补测建议。':task.title==='运行测试用例'?'运行用例，持续监控异常并分析结果。':task.title==='删除测试用例'?'删除指定用例及专属代码，保留共享组件。':task.title==='分析行覆盖率'?'分析覆盖缺口，定位未覆盖代码并建议补测场景。':task.title==='解释设计'?'阅读源码，解释模块功能、接口与内部逻辑。':'生成可运行用例，补齐说明与源码绑定。'));
            card.append(element('p','ai-task-requirements',task.requirements?.trim()||task.project||'按工作流要求执行。'));
            entry.content.replaceChildren(card);
          }else{
            markdown(entry.content,item.text||'');
            entry.root.querySelector('.message-workflow-binding')?.remove();
            if(item.kind==='user'&&item.workflow_label){
              const binding=element('div','message-workflow-binding');
              binding.append(element('span','ai-workflow-binding','◇ '+item.workflow_label));
              entry.content.after(binding);
            }
          }
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
    // Keep the animated status node mounted; moving it restarts CSS animations.
    if(this.live.parentNode!==this.root)this.root.append(this.live);
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
