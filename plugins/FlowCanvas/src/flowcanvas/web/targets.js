/* Verification goals are stable records; source scans only verify their bindings. */
function mountDefinitionPanel({view,endpoint,caseMode=false,bugMode=false}){
  const panelView=view;
  const host=document.getElementById('project-content');
  let project=null,data=null,panel=null,active=false,query='',busy=false,searchExpanded=true,selectedTarget=null;
  let lineCoverage=null,inlineEditing=false;
  let runCases=new Set(),pendingRun=null,runsLoaded=false,runsChecking=false;
  const opened=new Set();
  const localTextWrites=new Map(),textHighlights=new Map();
  const operationAnimationStart=new Map();
  let coverageTab='functional',functionalPane=null,linePane=null,lineFrame=null,tabBar=null;
  function selectCoverageTab(mode,instance){
    if(caseMode||bugMode||!tabBar)return;
    coverageTab=mode==='line'?'line':'functional';
    functionalPane.hidden=coverageTab!=='functional';linePane.hidden=coverageTab!=='line';
    for(const b of tabBar.querySelectorAll('button')){b.classList.toggle('active',b.dataset.mode===coverageTab);b.setAttribute('aria-selected',String(b.dataset.mode===coverageTab));}
    try{localStorage.setItem('fc-target-coverage-tab:'+project.id,coverageTab);}catch{}
    if(coverageTab==='line'){
      if(!lineFrame.src){
        const view='/api/chat/projects/'+encodeURIComponent(project.id)+'/rtl-view';
        lineFrame.src='/rtl-viewer/?coverageOnly=1&view='+encodeURIComponent(view)+(instance?'&focus='+encodeURIComponent(instance):'');
      }else if(instance)lineFrame.contentWindow.postMessage({type:'flowcanvas-coverage-focus',instance},location.origin);
    }
  }

  const labels={planned:'待实现',bound:'已绑定',changed:'待核对',missing:'绑定失效',conflict:'绑定冲突',unverified:'无法校验'};
  const kinds=bugMode?{bug:['BUG','Bug']}:caseMode?{case:['TC','测试用例']}:{group:['FG','功能组'],point:['FP','功能点'],testpoint:['TP','测试点']};
  const rootKind=bugMode?'bug':caseMode?'case':'group';
  const bugStatuses={unverified:'未验证',confirmed:'已确认',fixing:'修复中',resolved:'已修复'};
  const severities={low:'低',medium:'中',high:'高',critical:'严重'};
  function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
  function button(text,fn,cls=''){const n=el('button',cls,text);n.type='button';n.onclick=fn;return n;}
  function smallAction(label,pathData,callback){
    const b=button('',callback,'target-icon-action');b.title=label;b.setAttribute('aria-label',label);
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',pathData);svg.append(path);b.append(svg);return b;
  }
  async function api(request){const url='/api/chat/projects/'+encodeURIComponent(project.id)+'/'+endpoint;const response=await fetch(url,request?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}:{});const body=await response.json();if(!response.ok)throw Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));return body;}
  function fail(error){panel.querySelector('.targets-error').textContent=error.message;}
  async function animateRemovedCards(next){
    if(!data||!active||document.hidden||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    const remaining=new Set(next.targets.map(node=>node.id));
    const removed=new Set(data.targets.filter(node=>!remaining.has(node.id)).map(node=>node.id));
    if(!removed.size)return;
    const cards=[...panel.querySelectorAll('.target-card[data-target-id]')].filter(card=>{
      if(!removed.has(card.dataset.targetId))return false;
      let ancestor=card.parentElement.closest('.target-card');
      while(ancestor){if(removed.has(ancestor.dataset.targetId))return false;ancestor=ancestor.parentElement.closest('.target-card');}
      return card.getBoundingClientRect().height>0;
    });
    await Promise.all(cards.map(async card=>{
      const rect=card.getBoundingClientRect(),style=getComputedStyle(card);
      card.inert=true;card.classList.add('is-removing');
      const animation=card.animate([
        {height:rect.height+'px',opacity:1,transform:'translateY(0)',marginTop:style.marginTop,marginBottom:style.marginBottom},
        {height:rect.height+'px',opacity:0,transform:'translateY(-4px)',marginTop:style.marginTop,marginBottom:style.marginBottom,offset:.4},
        {height:'0px',opacity:0,transform:'translateY(-4px)',marginTop:'0px',marginBottom:'0px'}
      ],{duration:380,easing:'cubic-bezier(.22,.7,.25,1)',fill:'forwards'});
      try{await animation.finished;}catch{}
    }));
  }
  async function refresh(){
    if(busy||inlineEditing)return;busy=true;
    try{
      panel.querySelector('.targets-error').textContent='';const first=!data;
      const requests=[api()];
      if(!caseMode&&!bugMode)requests.push(fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage').then(async r=>{if(!r.ok)throw Error('行覆盖率读取失败');return r.json();}));
      const results=await Promise.allSettled(requests);
      if(results[0].status==='rejected')throw results[0].reason;
      if(inlineEditing)return;
      await animateRemovedCards(results[0].value);
      if(data){
        const previous=new Map(data.targets.map(node=>[node.id,node]));
        for(const node of results[0].value.targets){const old=previous.get(node.id);if(!old)continue;
          for(const key of ['name','title','description','condition','expected','actual','evidence']){
            if((old[key]||'')===(node[key]||''))continue;
            const id=node.id+':'+key;
            if(localTextWrites.get(id)===(node[key]||'')){localTextWrites.delete(id);continue;}
            textHighlights.set(id,performance.now());
          }
        }
      }
      data=results[0].value;
      if(results.length>1)lineCoverage=results[1].status==='fulfilled'?results[1].value:null;
      if(first)for(const n of data.targets)if(n.kind==='group')opened.add(n.id);
      render();paintRunButtons();if(caseMode)refreshRuns();
    }catch(e){fail(e);}finally{busy=false;}
  }
  function renderCoverageRings(){
    const host=panel.querySelector('.target-coverage-overview');if(!host)return;
    const records=(data?.targets||[]).filter(n=>n.kind==='group').map(n=>data.coverage?.targets?.[n.id]);
    const bins=[...new Map(records.flatMap(r=>r?.observations||[]).map(r=>[r.instance+'::'+r.point,r])).values()];
    const instances=[...new Map((lineCoverage?.report?.instances||[]).map(r=>[r.path,r])).values()];
    const metrics=[
      {mode:'functional',label:'功能覆盖率',rows:bins,valid:records.length>0&&records.every(r=>r?.status==='available'),unit:'bin'},
      {mode:'line',label:'行覆盖率',rows:instances,valid:instances.length>0,unit:'覆盖点'}
    ];
    for(const metric of metrics){
      let card=host.querySelector('[data-metric="'+metric.mode+'"]');
      if(!card){
        card=button('',()=>selectCoverageTab(metric.mode),'coverage-overview-item');card.dataset.metric=metric.mode;
        const ring=el('span','coverage-overview-ring'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.setAttribute('viewBox','0 0 64 64');svg.setAttribute('aria-hidden','true');
        for(const cls of ['coverage-ring-track','coverage-ring-fill']){
          const circle=document.createElementNS(svg.namespaceURI,'circle');
          for(const [key,value]of Object.entries({cx:32,cy:32,r:27,pathLength:100,class:cls}))circle.setAttribute(key,value);
          svg.append(circle);
        }
        ring.append(svg,el('strong','coverage-ring-value'));
        const text=el('span','coverage-overview-text');text.append(el('span','coverage-overview-label',metric.label),el('span','coverage-overview-count'),el('span','coverage-overview-extra'));
        card.append(ring,text);host.append(card);
      }
      const covered=metric.rows.reduce((sum,row)=>sum+row.covered,0),total=metric.rows.reduce((sum,row)=>sum+row.total,0);
      const available=metric.valid&&total>0,percent=available?Math.min(100,Math.max(0,100*covered/total)):0;
      card.querySelector('.coverage-ring-fill').style.strokeDashoffset=String(100-percent);
      card.querySelector('.coverage-ring-value').textContent=available?percent.toFixed(1)+'%':'—';
      card.querySelector('.coverage-overview-count').textContent=available?covered+' / '+total+' '+metric.unit:'暂无有效数据';
      const points=(data?.targets||[]).filter(n=>n.kind==='testpoint').map(coverageFor);
      const reached=points.filter(c=>c.status==='available'&&c.total>0&&c.covered===c.total).length;
      card.querySelector('.coverage-overview-extra').textContent=metric.mode==='functional'?reached+'/'+points.length+' 测试点达标':'';
      card.querySelector('.coverage-overview-extra').hidden=metric.mode!=='functional';
      card.setAttribute('aria-label',metric.label+'，'+(available?percent.toFixed(1)+'%，'+covered+'/'+total:'暂无有效数据')+'，点击查看明细');
    }
    let menu=host.querySelector('.coverage-overview-menu');
    if(!menu){
      menu=el('details','coverage-overview-menu');const trigger=el('summary','','⋯');trigger.title='覆盖率操作';trigger.setAttribute('aria-label','覆盖率操作');
      const reset=button('清空累计',async()=>{
        menu.open=false;
        if(!confirm('清空行覆盖率和功能覆盖率的本轮累计？历史运行和日志会保留。'))return;
        try{const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage/reset',{method:'POST'});if(!response.ok)throw Error('清空失败');await refresh();if(lineFrame?.src)lineFrame.contentWindow.location.reload();}catch(e){fail(e);}
      },'coverage-menu-reset');
      menu.append(trigger,reset);const actions=el('div','coverage-overview-actions');actions.append(el('span','coverage-shared-runs'),menu);host.prepend(actions);
      menu.addEventListener('focusout',()=>setTimeout(()=>{if(!menu.contains(document.activeElement))menu.open=false;},0));
      menu.addEventListener('keydown',event=>{if(event.key==='Escape'){menu.open=false;trigger.focus();}});
    }
    const runs=lineCoverage?.runs?.length??data?.coverage?.runs??0;
    host.querySelector('.coverage-shared-runs').textContent='累计 '+runs+' 次';
    menu.querySelector('button').disabled=!runs;
  }
  function modal(title,floating=false){const dialog=el('dialog','targets-dialog');const heading=el('header');heading.append(el('strong','',title),button('×',()=>dialog.close(),'targets-close'));const content=el('div','targets-dialog-content');dialog.append(heading,content);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});if(floating)dialog.show();else dialog.showModal();return {dialog,content};}
  function field(parent,label,value='',multi=false){const wrapper=el('label','targets-field');wrapper.append(el('span','',label));const input=el(multi?'textarea':'input');input.value=value;if(multi)input.rows=3;wrapper.append(input);parent.append(wrapper);return input;}
  async function source(binding){
    const {dialog,content}=modal('源码 · '+binding.file);dialog.classList.add('targets-source-dialog');content.textContent='正在读取并校验…';
    try{
      const result=await api({operation:'source',binding_id:binding.id});
      content.replaceChildren(el('p','targets-muted',result.file+' · 选中 L'+result.line+'–'+result.end_line));
      const pre=el('pre','targets-source');pre.tabIndex=0;pre.setAttribute('aria-label','完整源码，选中第 '+result.line+' 至 '+result.end_line+' 行');
      const full=result.file_content!==undefined;const start=full?1:result.line;
      (full?result.file_content:result.content).split('\n').forEach((line,i)=>{
        const number=start+i,selected=number>=result.line&&number<=result.end_line;
        const row=el('div',selected?'target-code-selected':'');
        if(number===result.line)row.dataset.selectionStart='true';
        row.append(el('span','targets-line',String(number)),el('code','',line||' '));pre.append(row);
      });
      content.append(pre);
      requestAnimationFrame(()=>{const row=pre.querySelector('[data-selection-start]');if(row)pre.scrollTop=Math.max(0,row.offsetTop-pre.offsetTop-pre.clientHeight*.2);});
    }catch(e){content.textContent=e.message;}
  }

  function edit(node=null,kind=rootKind,parent=''){
    if(!data)return;
    const {dialog,content}=modal(node?'编辑'+kinds[node.kind][1]:'新建'+kinds[kind][1]);
    const caseTitle=caseMode?field(content,'标题',node?.title||''):null;if(caseTitle){caseTitle.required=true;caseTitle.placeholder='例如：连续随机读访问';}
    const name=field(content,caseMode?'代码名称（类名 / 函数名）':'名称',node?.name||'');const desc=field(content,'说明',node?.description||'',true);
    const condition=bugMode?field(content,'复现步骤',node?.condition||'',true):null;const expected=bugMode?field(content,'预期行为',node?.expected||'',true):null;
    let actual,evidence,bugStatus,severity;
    if(bugMode){
      actual=field(content,'实际表现',node?.actual||'',true);evidence=field(content,'证据',node?.evidence||'',true);
      const choice=(label,options,value)=>{const wrap=el('label','targets-field');wrap.append(el('span','',label));const select=el('select');for(const [key,title]of Object.entries(options)){const opt=el('option','',title);opt.value=key;select.append(opt);}select.value=value;wrap.append(select);content.append(wrap);return select;};
      bugStatus=choice('状态',node?.example?{unverified:'示例 · 未验证'}:bugStatuses,node?.status||'unverified');severity=choice('严重程度',severities,node?.severity||'medium');
    }
    const error=el('p','targets-error');content.append(error);
    const save=button('保存',async()=>{save.disabled=true;try{const values={description:desc.value};if(caseTitle){if(!caseTitle.value.trim())throw Error('请填写测试用例标题');values.title=caseTitle.value.trim();}if(condition)Object.assign(values,{condition:condition.value,expected:expected.value});if(bugMode)Object.assign(values,{actual:actual.value,evidence:evidence.value,status:bugStatus.value,severity:severity.value});await api(node?{operation:'update',target_id:node.id,expected_revision:data.revision,values:{...values,name:name.value}}:{operation:'create',kind,parent,name:name.value,values,expected_revision:data.revision});if(node){for(const [key,value] of Object.entries({...values,name:name.value}))localTextWrites.set(node.id+':'+key,value);}if(parent)opened.add(parent);dialog.close();await refresh();}catch(e){error.textContent=e.message;save.disabled=false;}} ,'targets-primary');content.append(save);(caseTitle||name).focus();
  }
  /** Edit one display field without changing the runnable symbol or source binding. */
  function editCaseText(node,key,label){
    if(!bugMode&&node.lifecycle&&node.lifecycle!=='ready')return;
    if(inlineEditing)return;inlineEditing=true;
    const single=key==='title'||key==='name';
    const revision=data.revision,original=node[key]||'',editor=el(single?'input':'textarea','case-inline-input');
    editor.value=original;editor.setAttribute('aria-label',single?'标题':'说明');
    editor.title=single?'Enter 保存，Esc 取消':'Ctrl / ⌘ + Enter 保存，Esc 取消';
    const host=el('div','case-inline-editor'),message=el('span','case-inline-error');message.setAttribute('role','alert');host.append(editor,message);
    if(single){
      editor.maxLength=200;const header=label.closest('.target-header'),rect=label.getBoundingClientRect(),parent=header.getBoundingClientRect();
      host.classList.add('is-title');host.style.left=(rect.left-parent.left)+'px';host.style.top=(rect.top-parent.top-3)+'px';host.style.width=Math.max(100,parent.right-rect.left-48)+'px';
      label.style.visibility='hidden';header.append(host);
    }else{label.replaceWith(host);editor.rows=3;}
    let saving=false,closed=false;
    const close=()=>{if(closed)return;closed=true;inlineEditing=false;if(single){label.style.visibility='';host.remove();}else host.replaceWith(label);};
    const save=async()=>{
      if(saving||closed)return;const value=editor.value.trim();
      if(value===original){close();return;}
      if(single&&!value){message.textContent='标题不能为空';return;}
      saving=true;editor.disabled=true;message.textContent='';
      try{await api({operation:'update',target_id:node.id,expected_revision:revision,values:{[key]:value}});localTextWrites.set(node.id+':'+key,value);close();await refresh();}
      catch(error){message.textContent=error.message+'（Esc 取消后可重新编辑）';}
      finally{saving=false;editor.disabled=false;}
    };
    host.onclick=event=>event.stopPropagation();host.ondblclick=event=>event.stopPropagation();
    editor.onkeydown=event=>{event.stopPropagation();if(event.key==='Escape'){event.preventDefault();if(!saving){close();refresh();}}else if(event.key==='Enter'&&!event.isComposing&&(single||event.ctrlKey||event.metaKey)){event.preventDefault();save();}};
    editor.onblur=save;editor.focus();editor.select();
  }
  function goalAction(node,kind,label,path){
    const action=smallAction(label,path,()=>{});action.classList.add('case-ai-action');
    const svg=action.querySelector('svg'),spark=document.createElementNS(svg.namespaceURI,'path');spark.setAttribute('d','M19 1l1.2 3.8L24 6l-3.8 1.2L19 11l-1.2-3.8L14 6l3.8-1.2Z');spark.setAttribute('class','case-ai-spark');svg.append(spark);
    const key=kind+':'+(node?.id||'root');
    const open=preview=>{const context={kind,instance:node?.name||'功能组',module:node?.name||'功能组',target:node?{id:node.id,kind:node.kind,name:node.name}:null};newCaseTask(action,preview,context);const card=document.querySelector('.ai-action-dialog');if(card)card.dataset.goalTask=key;};
    let timer;action.onmouseenter=()=>{timer=setTimeout(()=>{if(action.matches(':hover'))open(true);},220);};action.onmouseleave=()=>clearTimeout(timer);
    action.onclick=()=>{clearTimeout(timer);const card=document.querySelector('.ai-action-dialog');if(card?.dataset.goalTask===key)card.pinTask();else{card?.close();open(false);}};
    return action;
  }
  function newCaseTask(anchor,preview=false,design=null){
    if(document.querySelector('.ai-action-dialog'))return;
    const previousFocus=document.activeElement;
    const addGoal=design?.kind==='add-goal',deleteGoal=design?.kind==='delete-goal',goalTask=design?.kind==='functional-coverage',coverageTask=design?.kind==='line-coverage',runTask=design?.kind==='run-testcase',deleteTask=design?.kind==='delete-testcase';
    const {dialog,content}=modal(addGoal?'新增验证目标':deleteGoal?'删除验证目标':goalTask?'分析功能覆盖率':runTask?'运行测试用例':deleteTask?'删除测试用例':coverageTask?'分析行覆盖率':design?'解释设计':'新增测试用例',true);dialog.classList.add('ai-action-dialog','ai-task-surface');dialog.classList.toggle('is-preview',preview);
    let leaveTimer,closeTimer;
    const enter=()=>{clearTimeout(leaveTimer);clearTimeout(closeTimer);dialog.classList.remove('is-leaving');};
    const pin=()=>{enter();dialog.classList.remove('is-preview');};dialog.pinTask=pin;
    const leave=()=>{clearTimeout(leaveTimer);leaveTimer=setTimeout(()=>{if(dialog.classList.contains('is-preview')){dialog.classList.add('is-leaving');closeTimer=setTimeout(()=>dialog.close(),140);}},160);};
    dialog.addEventListener('click',pin);dialog.addEventListener('mouseenter',enter);dialog.addEventListener('mouseleave',leave);
    anchor.addEventListener('mouseleave',leave);anchor.addEventListener('mouseenter',enter);
    const outside=event=>{if(!dialog.contains(event.target)&&!anchor.contains(event.target))dialog.close();};
    document.addEventListener('pointerdown',outside);
    dialog.addEventListener('close',()=>{clearTimeout(leaveTimer);clearTimeout(closeTimer);anchor.removeEventListener('mouseleave',leave);anchor.removeEventListener('mouseenter',enter);document.removeEventListener('pointerdown',outside);});
    content.append(el('p','ai-task-objective',addGoal?'补充验证目标、说明与源码绑定。':deleteGoal?'删除选中目标及其子目标，保留无关代码。':goalTask?'分析覆盖目标与未命中 bin，给出补测建议。':runTask?'运行用例，持续监控异常并分析结果。':deleteTask?'删除指定用例及专属代码，保留共享组件。':coverageTask?'分析 '+design.module+' 的覆盖缺口与补测方向。':design?'解释 '+design.module+' 的功能、接口及内部逻辑。':'生成可运行用例，补齐说明与源码绑定。'));
    if(design){const scope=el('div','ai-design-scope',design.instance);scope.title=design.instance;content.append(scope);}
    if(!design)dialog.querySelector('header strong').after(el('span','ai-workflow-binding','◇ 新增测试用例'));
    const note=field(content,'想验证什么？（可选）','',true);note.placeholder=addGoal?'补充希望覆盖的功能或场景…':deleteGoal?'补充删除范围或需要保留的内容…':goalTask?'补充关注的功能、覆盖缺口或激励场景…':runTask?'补充关注的异常或运行要求…':deleteTask?'补充删除范围或需要保留的内容…':coverageTask?'补充关注的覆盖缺口或测试场景…':design?'补充想了解的设计细节…':'补充要求，例如读写边界场景…';note.setAttribute('aria-label','具体要求（可选）');note.oninput=()=>{note.style.height='36px';note.style.height=Math.min(100,Math.max(36,note.scrollHeight))+'px';};
    const choice=el('input');choice.type='checkbox';choice.setAttribute('role','switch');
    const newChat=el('div','ai-action-new-chat');choice.setAttribute('aria-label','新对话');newChat.append(choice,el('span','','新对话'));
    const error=el('p','targets-error');error.setAttribute('role','alert');const start=button('',async()=>{
      start.disabled=true;start.classList.add('is-sending');start.querySelector('span').textContent='发送中';error.textContent='';
      try{
        if(!window.FlowCanvasAI)throw Error('对话服务尚未就绪，请稍后重试');
        const caseContext=design?.testcase?JSON.stringify(design.testcase):'';
        const text=addGoal?'【AI任务：新增验证目标】项目：'+project.path+'；父目标：'+JSON.stringify(design.target)+'。先用 verification_targets(guide/read) 读取规范、定义和源码绑定。父目标为空时新增功能组，group 下新增功能点，point 下新增测试点；不重复已有目标。根据真实设计与验证代码写清名称和 description，编写代码前先调用 verification_targets(begin_create, kind, name, parent, expected_revision, values={description}) 注册占位，保存 ID，再 update/inspect/bind 同一 ID，最后 finish_create。不能另行 create/import 重复登记；中断时用 resume_operation 恢复原条目。必要时实现专属覆盖定义，保留共享采样逻辑，不修改 RTL，不运行仿真，不启动其他工作流。不能虚构覆盖命中。用户要求：'+(note.value.trim()||'分析当前缺口，新增有意义的验证目标并汇报修改。'):deleteGoal?'【AI任务：删除验证目标】项目：'+project.path+'；指定目标：'+JSON.stringify(design.target)+'。删除任务的第一步是登记卡片：仅调用 verification_targets(read) 获取最新 revision 并定位用户指定 ID，立即调用 verification_targets(begin_delete, target_id, expected_revision, values={cascade:true})，先让选中卡片及子目标进入删除中。登记成功后再读取 guide、检查全部子目标、源码绑定、共享引用和实际删除范围，然后清理目标专属覆盖定义和引用，不能删除共享代码、RTL、测试用例或历史运行产物；若无法安全拆分则保留相关代码并明确说明。用 verification_targets(delete, target_id, expected_revision, values={cascade:true}) 移除选中目标及其子目标，不直接修改 JSON，不运行仿真。报告删除范围和保留项。用户要求：'+(note.value.trim()||'删除此目标及其子目标，保留无关代码和历史产物。'):goalTask?'【AI任务：分析功能覆盖率】请只读分析项目 '+project.path+' 的验证目标：'+design.instance+'。选中目标及子目标快照：'+JSON.stringify(design.coverage)+'。先调用 verification_targets(guide/read) 读取最新定义和覆盖数据，按稳定 ID 定位目标，读取对应源码、coverpoint/bin 定义和相关测试。解释目标意图、采样条件、累计命中情况与缺口，明确区分功能组、功能点、测试点和 bin，不把命中等同于正确性通过，不把缺失或过期数据当作未命中。针对未命中项提出具体激励、边界条件和可复用的用例，引用真实源码位置。不要修改文件、运行仿真或启动工作流。用户要求：'+(note.value.trim()||'优先分析主要缺口，给出有依据的补测建议。'):runTask?'【AI任务：运行测试用例】项目：'+project.path+'；指定用例：'+caseContext+'。先用 test_cases(read) 核实 ID 与运行模板，再调用 test_case_runs(start, case_id) 一次，保存返回的 run.id。持续调用 test_case_runs(read, run_id, offset, wait_seconds=10)，用返回的 offset 增量读取，直到运行结束且日志全部读取完成。实时关注断言、UVM_ERROR/UVM_FATAL、异常退出和超时，发现异常及时汇报；不要因短暂无输出断言死锁。必须等待执行完毕后报告运行结果，分析退出码、关键日志、失败原因与覆盖率采集结果；退出码为零不等于验证通过。不要自行修改代码、重复启动或重试；如明确不可恢复可通过 stop 停止并说明原因。用户要求：'+(note.value.trim()||'监控运行，报告结果并给出分析。'):deleteTask?'【AI任务：删除测试用例】项目：'+project.path+'；仅删除指定用例：'+caseContext+'。删除任务的第一步是登记卡片：仅调用 test_cases(read) 获取最新 revision 并定位用户指定 ID，立即调用 test_cases(begin_delete, target_id, expected_revision)。若接口因用例正在运行而拒绝，停止删除并说明原因。登记成功后再读取 guide、检查源码绑定、共享引用和实际删除范围，仅删除该用例专属实现和注册/导入引用，保留共享基类、驱动、环境、RTL、其他测试及历史日志。不要删除包含其他定义的整个文件。完成源码清理后使用 test_cases(delete, target_id, expected_revision) 删除对应记录；报告实际修改和无法安全删除的内容，不要启动仿真。用户要求：'+(note.value.trim()||'删除此测试用例，保留共享代码和历史运行记录。'):coverageTask?'请只读分析项目 '+project.path+' 的行覆盖率，不修改文件、不运行仿真、不启动工作流。实例：'+design.instance+'；模块：'+design.module+'；源码位置：'+JSON.stringify(design.source)+'；当前累计覆盖率快照：'+JSON.stringify(design.coverage)+'。读取真实源码和项目 .flowcanvas/coverage 下的覆盖率产物，分别说明自身与包含子模块的覆盖率，定位未覆盖和部分覆盖的代码，分析可能缺失的激励、边界条件及建议补充的测试场景。引用具体文件和行号；不要把未覆盖等同于缺陷，没有数据不能声称已覆盖，快照与产物不一致时明确说明。用户要求：'+(note.value.trim()||'优先分析主要覆盖缺口，给出可执行的补测建议。'):design?'请只读分析当前项目 '+project.path+' 中的设计，不修改文件、不运行仿真、不启动其他工作流。目标实例：'+design.instance+'；模块：'+design.module+'；源码定位：'+JSON.stringify(design.source)+ '。先读取真实源码及必要的子模块，解释模块职责、端口协议、关键数据通路、状态变化及与父子模块的关系，引用具体文件和行号。不能用模块名猜测功能；无法确定的内容明确说明。用户要求：'+(note.value.trim()||'给出简洁清晰的中文设计说明。'):'【AI任务：新增测试用例】\n绑定工作流：generate-testcase。请先调用 run_workflow 启动已附加的工作流，再按阶段执行。\n请为当前项目新增一个可独立运行的测试用例。项目：'+project.path+'。用户补充：'+(note.value.trim()||'请检查已有测试和验证目标，选择尚未充分验证且不重复的场景。')+'。先调用 test_cases(operation=guide) 读取规范，检查现有用例、目标和运行模板；复用现有驱动，必要时补齐用例所需基础设施。编写代码前必须先通过 test_cases(create) 注册占位，保存返回的用例 ID；实现后只 update/bind 同一 ID，不要另行 import。绑定有效且说明齐全后调用 finish_create 转为正式用例。中断后用 resume_operation 恢复原条目。通过 test_cases 工具保存 title（概括测试场景的中文标题）、name（代码类名或函数名）、具体说明和源码绑定，不能只给方案。不要修改 RTL，不要自动启动仿真；结束时汇报新增文件与未验证事项。';
        const result=await window.FlowCanvasAI.startCase({text,current:!choice.checked,project,requirements:note.value.trim(),design});
        const sourceRect=dialog.getBoundingClientRect();
        const destination=[...document.querySelectorAll('.conversation .ai-task-sent')].at(-1);
        if(destination&&!matchMedia('(prefers-reduced-motion: reduce)').matches){
          destination.scrollIntoView({block:'nearest',behavior:'instant'});
          const targetRect=destination.getBoundingClientRect();
          if(targetRect.width&&targetRect.height){
            // Animate a visual copy only; the real sent message already exists in the conversation.
            const flying=destination.cloneNode(true);flying.setAttribute('aria-hidden','true');flying.inert=true;
            flying.classList.add('ai-task-flight');
            Object.assign(flying.style,{position:'fixed',left:targetRect.left+'px',top:targetRect.top+'px',width:targetRect.width+'px',height:targetRect.height+'px',margin:'0',zIndex:'10000',pointerEvents:'none',transformOrigin:'top left',overflow:'hidden'});
            document.body.append(flying);destination.style.visibility='hidden';dialog.close();
            const dx=sourceRect.left-targetRect.left,dy=sourceRect.top-targetRect.top;
            const animation=flying.animate([
              {transform:`translate(${dx}px,${dy}px) scale(${sourceRect.width/targetRect.width},${sourceRect.height/targetRect.height})`,opacity:.95},
              {transform:'translate(0,0) scale(1,1)',opacity:1}
            ],{duration:440,easing:'cubic-bezier(.22,.8,.24,1)',fill:'forwards'});
            animation.finished.catch(()=>{}).finally(()=>{destination.style.visibility='';flying.remove();});
          }else dialog.close();
        }else dialog.close();
      }catch(e){error.textContent=e.message;start.disabled=false;start.classList.remove('is-sending');start.querySelector('span').textContent='发送任务';}
    },'targets-primary');
    start.classList.add('ai-task-send');start.append(el('span','','发送任务'));
    const arrow=document.createElementNS('http://www.w3.org/2000/svg','svg');arrow.setAttribute('viewBox','0 0 16 16');arrow.setAttribute('aria-hidden','true');const path=document.createElementNS(arrow.namespaceURI,'path');path.setAttribute('d','M3 8h10M9 4l4 4-4 4');arrow.append(path);start.append(arrow);
    const footer=el('footer');footer.append(newChat,start);content.append(error,footer);
    const position=()=>{const rect=anchor.getBoundingClientRect();dialog.style.margin='0';dialog.style.left=Math.max(12,Math.min(rect.right+10,innerWidth-dialog.offsetWidth-12))+'px';dialog.style.top=Math.max(12,Math.min(rect.top-8,innerHeight-dialog.offsetHeight-12))+'px';};
    position();requestAnimationFrame(()=>dialog.classList.add('is-visible'));
    window.addEventListener('resize',position);dialog.addEventListener('close',()=>window.removeEventListener('resize',position));
    if(preview)previousFocus?.focus({preventScroll:true});else note.focus();
  }
  function paintRunButtons(){
    if(!caseMode||!panel)return;
    const occupied=pendingRun!==null||runCases.size>0;
    for(const button of panel.querySelectorAll('[data-case-run]')){
      const running=runCases.has(button.dataset.caseRun)||pendingRun===button.dataset.caseRun;
      button.disabled=!runsLoaded||occupied||!data?.runner||button.dataset.caseUnavailable==='true';
      button.classList.toggle('is-case-running',running);
      button.setAttribute('aria-busy',String(running));
      button.title=running?'运行中':button.dataset.runLabel;
      button.setAttribute('aria-label',button.title);
    }
  }
  async function refreshRuns(){
    if(!caseMode||!project||runsChecking)return;runsChecking=true;
    try{
      const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/test-runs');
      if(!response.ok)return;
      const rows=await response.json();runCases=new Set(rows.filter(row=>['starting','running'].includes(row.status)).map(row=>row.case_id));runsLoaded=true;paintRunButtons();
    }catch{}finally{runsChecking=false;}
  }
  if(caseMode){
    setInterval(()=>{if(active&&!document.hidden)refreshRuns();},1000);
    document.addEventListener('flowcanvas-test-run',event=>{if(event.detail.project?.id!==project?.id)return;runCases.add(event.detail.run.case_id);paintRunButtons();refreshRuns();});
  }
  function importer(){
    if(!data)return;
    const {dialog,content}=modal('导入源码定义');content.append(el('p','targets-muted','选择项目内的 SV/UVM 或 Python 源文件。先预览，再导入；导入会在对应定义前插入稳定 ID 注释。'));
    const paths=field(content,'相对路径，每行一个','verification/src/components/cache_bus_cov.sv',true);
    // A suggestion only when this sample file is already bound; generic projects start empty.
    if(!data.targets.some(n=>n.bindings.some(b=>b.file===paths.value)))paths.value='';
    paths.placeholder='verification/src/coverage.sv\ntests/test_cache.py';
    const preview=el('div','targets-preview'),error=el('p','targets-error');content.append(preview,error);
    let inspected=null;paths.oninput=()=>{inspected=null;run.disabled=true;};
    const inspect=button('预览定义',async()=>{inspect.disabled=true;error.textContent='';try{const files=paths.value.split('\n').map(s=>s.trim()).filter(Boolean);if(!files.length)throw Error('请输入源码路径');const rows=await api({operation:'inspect',files});preview.replaceChildren();for(const file of rows){preview.append(el('strong','',file.file));for(const n of file.nodes)preview.append(el('div','targets-preview-row',`${n.kind} · ${n.name} · L${n.line}–${n.end_line}${n.excluded?' · 排除项，不导入':''}`));}inspected=files;run.disabled=false;}catch(e){error.textContent=e.message;}finally{inspect.disabled=false;}});
    const run=button('插入标识并导入',async()=>{run.disabled=true;inspect.disabled=true;try{await api({operation:'import',files:inspected,expected_revision:data.revision});dialog.close();await refresh();}catch(e){error.textContent=e.message;inspect.disabled=false;}} ,'targets-primary');run.disabled=true;const actions=el('footer');actions.append(inspect,run);content.append(actions);
  }
  function coverageFor(node){
    const record=data.coverage?.targets?.[node.id];
    const rows=record?.observations||[];
    return {status:record?.status||'no_data',rows,covered:rows.reduce((n,r)=>n+r.covered,0),total:rows.reduce((n,r)=>n+r.total,0)};
  }
  function render(){
    if(inlineEditing)return;
    if(!caseMode&&!bugMode)renderCoverageRings();
    const list=panel.querySelector('.targets-tree'),summary=panel.querySelector('.targets-summary');list.replaceChildren();summary.replaceChildren();
    for(const [kind,[,label]]of Object.entries(kinds))summary.append(el('span','',data.targets.filter(n=>n.kind===kind).length+' '+label));
    if(bugMode)summary.append(el('span','',data.targets.filter(n=>!n.example&&n.status==='confirmed').length+' 已确认 · '+data.targets.filter(n=>n.example).length+' 示例'));
    const allExpanded=data.targets.length>0&&data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));
    const toggleAll=button(allExpanded?'全部折叠':'全部展开',()=>{const expanded=data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));if(expanded){opened.clear();searchExpanded=false;}else{for(const node of data.targets)opened.add(node.id);}render();},'targets-toggle-all');
    toggleAll.disabled=!data.targets.length;toggleAll.title=toggleAll.textContent;toggleAll.setAttribute('aria-label',toggleAll.textContent);summary.append(toggleAll);
    const byId=new Map(data.targets.map(n=>[n.id,n]));const visible=new Set();
    for(const n of data.targets){if(!query||[n.title,n.name,n.description,n.id,...n.bindings.map(b=>b.file)].join(' ').toLowerCase().includes(query)){let current=n;while(current){visible.add(current.id);current=byId.get(current.parent);}}}
    function card(node,depth){
      const operationLocked=!bugMode&&!!node.lifecycle&&node.lifecycle!=='ready';
      let inheritedDeletion=false;
      if(node.lifecycle==='deleting'){let ancestor=byId.get(node.parent);while(ancestor){if(ancestor.lifecycle==='deleting'){inheritedDeletion=true;break;}ancestor=byId.get(ancestor.parent);}}
      const report=data.reports[node.id]||{status:'planned',bindings:[]};const children=data.targets.filter(n=>n.parent===node.id&&visible.has(n.id));
      const wrapper=el('article','target-card target-'+node.kind);wrapper.dataset.targetId=node.id;if(node.operation_status==='running'&&node.lifecycle&&node.lifecycle!=='ready'){if(!operationAnimationStart.has(node.id))operationAnimationStart.set(node.id,performance.now());wrapper.style.setProperty('--operation-phase',(-((performance.now()-operationAnimationStart.get(node.id))%2600))+'ms');}else operationAnimationStart.delete(node.id);if(!bugMode&&node.lifecycle&&node.lifecycle!=='ready'){wrapper.dataset.lifecycle=node.lifecycle;wrapper.classList.add('case-in-progress');wrapper.classList.toggle('case-operation-active',node.operation_status==='running'&&!inheritedDeletion);}const head=button('',()=>{
        if(inlineEditing)return;
        selectedTarget=node.id;
        for(const h of panel.querySelectorAll('.target-header.is-selected'))h.classList.remove('is-selected');
        header.classList.add('is-selected');
        if(query&&searchExpanded){for(const item of data.targets)opened.add(item.id);searchExpanded=false;}
        if(opened.has(node.id))opened.delete(node.id);else opened.add(node.id);
        // Preserve the hovered header: only replace its expandable body.
        const fresh=card(node,depth),body=fresh.querySelector(':scope > .target-card-body');
        wrapper.querySelector(':scope > .target-card-body')?.remove();if(body)wrapper.append(body);
        head.setAttribute('aria-expanded',String(opened.has(node.id)));
        head.querySelector('.target-chevron').classList.toggle('is-expanded',opened.has(node.id));
        const toggle=panel.querySelector('.targets-toggle-all');if(toggle){toggle.textContent=data.targets.every(item=>opened.has(item.id))?'全部折叠':'全部展开';toggle.title=toggle.textContent;toggle.setAttribute('aria-label',toggle.textContent);}
      },'target-card-head');head.setAttribute('aria-expanded',String(opened.has(node.id)||(!!query&&searchExpanded)));
      const chevron=el('span','target-chevron'+((opened.has(node.id)||(query&&searchExpanded))?' is-expanded':''));
      const arrow=document.createElementNS('http://www.w3.org/2000/svg','svg');arrow.setAttribute('viewBox','0 0 16 16');arrow.setAttribute('aria-hidden','true');
      const arrowPath=document.createElementNS(arrow.namespaceURI,'path');arrowPath.setAttribute('d','m5 3 6 5-6 5Z');arrow.append(arrowPath);chevron.append(arrow);
      head.append(chevron);
      if(!caseMode)head.append(el('span','target-kind',kinds[node.kind][0]));
      const title=el('span','target-title',caseMode?(node.title||node.name):node.name);title.dataset.textField=caseMode&&node.title?'title':'name';head.append(title);
      if(!bugMode&&(!node.lifecycle||node.lifecycle==='ready')){
        let clickTimer;title.title='双击修改标题';
        title.onclick=event=>{event.stopPropagation();clearTimeout(clickTimer);clickTimer=setTimeout(()=>head.click(),450);};
        title.ondblclick=event=>{event.preventDefault();event.stopPropagation();clearTimeout(clickTimer);if(node.lifecycle!=='deleting')editCaseText(node,caseMode?'title':'name',title);};
      }
      if(!caseMode&&!bugMode){
        const c=coverageFor(node);
        const text=c.status==='conflict'?'多实例冲突':c.status==='stale'?'数据已过期':c.total?((c.covered/c.total*100).toFixed(1)+'% · '+c.covered+'/'+c.total):'无数据';
        const badge=el('span','target-coverage-score'+(c.total&&c.status!=='stale'?' has-data':''),text);
        badge.title=c.status==='conflict'?'同一定义对应多个运行实例，请拆分后重新导入':c.status==='stale'?'源码与采集时不同，请重新构建并开始新累计':'该目标唯一实例的累计 bin 命中，不代表正确性检查通过';
        if(c.status==='available'&&c.total){
          badge.replaceChildren(el('span','goal-coverage-percent',(100*c.covered/c.total).toFixed(1)+'%'),el('span','goal-coverage-count',c.covered+'/'+c.total));
          const meter=el('span','goal-coverage-meter');meter.setAttribute('aria-hidden','true');meter.style.setProperty('--coverage',Math.min(100,100*c.covered/c.total)+'%');badge.append(meter);
        }
        head.append(badge);
      }
      if(!bugMode&&node.lifecycle&&node.lifecycle!=='ready'&&!inheritedDeletion){const label=(node.lifecycle==='creating'?'创建':'删除')+(node.operation_status==='running'?'中':'已暂停');head.append(el('span','case-operation-label',label));}
      if(bugMode){head.append(el('span','target-state',node.example?'示例 · 未验证':bugStatuses[node.status]));}else if(report.status==='planned'&&(!node.lifecycle||node.lifecycle==='ready'))head.append(el('span','target-state state-planned','待实现'));
      else if(report.status!=='bound'&&!operationLocked){
        const warning=el('span','target-warning state-'+report.status);
        const message=[labels[report.status],...report.bindings.filter(b=>b.status!=='bound').map(b=>b.message|| (b.status==='changed'?'源码已变化，请核对定义':''))].filter(Boolean);
        warning.title=[...new Set(message)].join(' · ');warning.setAttribute('role','img');warning.setAttribute('aria-label',warning.title);
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
        const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M10.3 4.3 2.7 17.5A2 2 0 0 0 4.4 20h15.2a2 2 0 0 0 1.7-2.5L13.7 4.3a2 2 0 0 0-3.4 0ZM12 9v4m0 3h.01');svg.append(path);warning.append(svg);head.append(warning);
      }
      const header=el('div','target-header'+(selectedTarget===node.id?' is-selected':''));
      head.title=caseMode?[node.title,node.name].filter(Boolean).join(' · '):node.name;
      head.onkeydown=event=>{
        if(!bugMode&&(!node.lifecycle||node.lifecycle==='ready')&&event.key==='F2'){event.preventDefault();if(node.lifecycle!=='deleting')editCaseText(node,caseMode?'title':'name',title);return;}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          event.preventDefault();const rows=[...panel.querySelectorAll('.target-card-head')],index=rows.indexOf(head);
          rows[Math.max(0,Math.min(rows.length-1,index+(event.key==='ArrowDown'?1:-1)))]?.focus();
        }else if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
          event.preventDefault();if((event.key==='ArrowRight')!==(head.getAttribute('aria-expanded')==='true'))head.click();
        }
      };
      header.onclick=event=>{if(!event.target.closest('button'))head.click();};
      const controls=el('div','target-actions target-header-actions');if(bugMode)controls.append(smallAction('编辑','m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15Z',()=>edit(node)));if(!caseMode&&!bugMode&&(!node.lifecycle||node.lifecycle==='ready')){if(node.kind!=='testpoint')controls.append(goalAction(node,'add-goal','AI 新增'+(node.kind==='group'?'功能点':'测试点'),'M10 5v14M3 12h14'));controls.append(goalAction(node,'delete-goal','AI 删除验证目标','M4 7h12M6 7v13h9V7M8 7V4h5M9 10v7m3-7v7'));}
      if(!caseMode&&!bugMode&&(!node.lifecycle||node.lifecycle==='ready')){
        const ai=smallAction('AI 分析功能覆盖率','M4 17V9m5 8V5m5 12v-5M3 21h16',()=>{});ai.classList.add('case-ai-action');
        const svg=ai.querySelector('svg'),spark=document.createElementNS(svg.namespaceURI,'path');spark.setAttribute('d','M19 1l1.2 3.8L24 6l-3.8 1.2L19 11l-1.2-3.8L14 6l3.8-1.2Z');spark.setAttribute('class','case-ai-spark');svg.append(spark);
        const key='functional-coverage:'+node.id;
        const open=preview=>{
          const ids=new Set([node.id]);let size;do{size=ids.size;for(const n of data.targets)if(ids.has(n.parent))ids.add(n.id);}while(size!==ids.size);
          const ancestors=[];let parent=node;while(parent){ancestors.unshift(parent.name);parent=data.targets.find(n=>n.id===parent.parent);}
          const context={kind:'functional-coverage',instance:ancestors.join(' / '),module:node.name,coverage:{target_id:node.id,targets:data.targets.filter(n=>ids.has(n.id)).map(n=>({id:n.id,parent:n.parent,kind:n.kind,name:n.name,description:n.description,bindings:data.reports[n.id]?.bindings,coverage:coverageFor(n)}))}};
          newCaseTask(ai,preview,context);const card=document.querySelector('.ai-action-dialog');if(card)card.dataset.goalTask=key;
        };
        let timer;ai.onmouseenter=()=>{timer=setTimeout(()=>{if(ai.matches(':hover'))open(true);},220);};ai.onmouseleave=()=>clearTimeout(timer);
        ai.onclick=()=>{clearTimeout(timer);const card=document.querySelector('.ai-action-dialog');if(card?.dataset.goalTask===key)card.pinTask();else{card?.close();open(false);}};controls.prepend(ai);
      }
      if(caseMode&&(!node.lifecycle||node.lifecycle==='ready')){
        const run=smallAction('运行测试用例','m8 5 11 7-11 7Z',async()=>{if(pendingRun!==null||runCases.size)return;pendingRun=node.id;paintRunButtons();try{
          const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/test-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:node.id})});
          const result=await response.json();if(!response.ok)throw Error(result.detail||'启动失败');
          document.dispatchEvent(new CustomEvent('flowcanvas-test-run',{detail:{project,run:result}}));
        }catch(e){await refreshRuns();if(!runCases.size)fail(e);}finally{pendingRun=null;paintRunButtons();}});
        run.dataset.caseRun=node.id;run.dataset.caseUnavailable=String(!!node.lifecycle&&node.lifecycle!=='ready');run.dataset.runLabel='运行测试用例';controls.append(run);
        for(const [kind,label,path] of [['run-testcase','AI 运行测试用例','m6 5 11 7-11 7Z'],['delete-testcase','AI 删除测试用例','M4 7h12M6 7v13h9V7M8 7V4h5M9 10v7m3-7v7']]){
          const context={kind,instance:node.name,module:node.title||node.name,testcase:{id:node.id,title:node.title,name:node.name,bindings:node.bindings}};
          const action=smallAction(label,path,()=>{});action.classList.add('case-ai-action');if(kind==='run-testcase'){action.dataset.caseRun=node.id;action.dataset.caseUnavailable=String(!!node.lifecycle&&node.lifecycle!=='ready');action.dataset.runLabel=label;}
          const svg=action.querySelector('svg'),spark=document.createElementNS(svg.namespaceURI,'path');
          spark.setAttribute('d','M19 1l1.2 3.8L24 6l-3.8 1.2L19 11l-1.2-3.8L14 6l3.8-1.2Z');spark.setAttribute('class','case-ai-spark');svg.append(spark);
          const key=kind+':'+node.id;
          const open=preview=>{newCaseTask(action,preview,context);const card=document.querySelector('.ai-action-dialog');if(card)card.dataset.caseTask=key;};
          let timer;action.onmouseenter=()=>{timer=setTimeout(()=>{if(action.matches(':hover'))open(true);},220);};action.onmouseleave=()=>clearTimeout(timer);
          action.onclick=()=>{clearTimeout(timer);const card=document.querySelector('.ai-action-dialog');if(card?.dataset.caseTask===key)card.pinTask();else{card?.close();open(false);}};
          if(node.lifecycle&&node.lifecycle!=='ready')action.disabled=true;
          controls.append(action);
        }
      }
      const links=el('div','target-title-sources');
      for(const binding of (caseMode||bugMode?report.bindings:[])){
        const label=binding.file.split('/').at(-1)+(binding.line?':'+binding.line+'–'+binding.end_line:'');
        const b=button(label,()=>{if(!operationLocked)source(binding);},'target-source target-title-source');b.disabled=operationLocked||!binding.line;
        b.title=binding.file+(binding.message?' · '+binding.message:' · 查看源码');b.setAttribute('aria-label','查看源码 '+binding.file+(binding.line?' 第 '+binding.line+' 行':''));links.append(b);
      }
      if(!caseMode&&!bugMode){
        const score=head.querySelector('.target-coverage-score'),heading=el('div','goal-row-heading');
        heading.append(head,links);header.append(heading,score,controls);
      }else header.append(head,links,controls);
      wrapper.append(header);queueMicrotask(paintRunButtons);
      if(opened.has(node.id)||(query&&searchExpanded)){
        const body=el('div','target-card-body');
        if(!bugMode){
          const description=el('p','case-editable-description'+(node.description?'':' is-empty'),node.description||'双击添加说明');description.dataset.textField='description';description.title='双击修改说明';description.tabIndex=0;
          description.ondblclick=event=>{event.preventDefault();event.stopPropagation();editCaseText(node,'description',description);};
          description.onkeydown=event=>{if(event.key==='Enter'||event.key==='F2'){event.preventDefault();editCaseText(node,'description',description);}};
          if(!bugMode&&node.lifecycle&&node.lifecycle!=='ready'){
            description.ondblclick=null;description.onkeydown=null;description.removeAttribute('tabindex');description.removeAttribute('title');description.classList.add('is-locked');
            if(!node.description)description.textContent='';
          }
          body.append(description);if(caseMode)body.append(el('p','test-case-code',node.name));
          const mark=el('span','case-ai-mark');mark.setAttribute('aria-hidden','true');
          mark.innerHTML='<svg viewBox="0 0 20 20"><path d="M9 2.5 11 7l4.5 2-4.5 2L9 15.5 7 11 2.5 9 7 7Z"/><path class="case-ai-satellite" d="m15.5 12 1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1Z"/></svg>';
          body.append(mark);
        }else{
          for(const [label,value]of [['',node.description],...(bugMode?[['复现步骤',node.condition],['预期',node.expected],['实际',node.actual],['证据',node.evidence],['严重程度',severities[node.severity]],['关联用例',node.case_ids.join('、')]]:[])]){if(value){const p=el('p');p.dataset.textField=({'':'description','复现步骤':'condition','预期':'expected','实际':'actual','证据':'evidence'})[label]||'';if(label)p.append(el('strong','',label+' · '));p.append(document.createTextNode(value));body.append(p);}}
        }

        if(!caseMode&&!bugMode){
          const c=coverageFor(node);
          if(report.bindings.length){
            const detail=el('div','target-coverage-detail');
            for(const binding of report.bindings){
              const observation=c.status==='available'?c.rows.find(row=>row.binding_id===binding.id):null;
              const path=observation?.source_path||[binding.scope,binding.symbol].filter(Boolean).join('.')||binding.file;
              const context=binding.line?button(path,()=>{if(!operationLocked)source(binding);},'target-coverage-context goal-source-link'):el('div','target-coverage-context',path);
              if(binding.line){context.disabled=operationLocked;context.title=operationLocked?'AI 操作尚未完成':'查看源码';context.setAttribute('aria-label',operationLocked?path+'，AI 操作中不可打开':'查看 '+path+' 的源码');}
              detail.append(context);
            }
            if(node.kind==='testpoint'&&c.status==='available'){
              for(const row of c.rows)for(const bin of row.bins){
                const item=el('div','target-coverage-bin');
                const hit=bin.hits>=bin.goal;const label=bin.goal===1?(hit?'已命中':'未命中'):bin.hits+' / '+bin.goal+' 次'+(hit?' · 已达标':' · 未达标');
                item.append(el('span','',bin.name),el('span',hit?'is-hit':'',label));
                detail.append(item);
              }
            }
            body.append(detail);
          }
        }
        const actions=el('div','target-actions');
        if(bugMode&&report.status==='changed'&&!operationLocked)actions.append(button('核对后确认',()=>{const {dialog,content}=modal('确认源码与目标一致');content.append(el('p','','请先查看当前源码，核对说明与实际实现。确认仅更新代码基线，不代表仿真通过。'));const error=el('p','targets-error');content.append(error,button('已核对，确认当前版本',async()=>{try{await api({operation:'accept',target_id:node.id,expected_revision:data.revision,values:Object.fromEntries(report.bindings.map(b=>[b.id,b.fingerprint]))});dialog.close();refresh();}catch(e){error.textContent=e.message;}},'targets-primary'));}));
        if(actions.children.length)body.append(actions);for(const child of children)body.append(card(child,depth+1));wrapper.append(body);
      }else if(children.length){head.title=children.length+' 个子目标';}
      for(const label of wrapper.querySelectorAll('[data-text-field]')){
        if(label.closest('[data-target-id]')!==wrapper)continue;
        const id=node.id+':'+label.dataset.textField,started=textHighlights.get(id);
        if(started===undefined)continue;
        const elapsed=performance.now()-started;
        if(elapsed>=2200){textHighlights.delete(id);continue;}
        label.classList.add('ai-text-updated');label.style.setProperty('--text-update-phase',-elapsed+'ms');
        // Remove the animation even while this panel is hidden: display toggles
        // otherwise restart a completed CSS animation on the retained node.
        setTimeout(()=>{
          label.classList.remove('ai-text-updated');label.style.removeProperty('--text-update-phase');
          if(textHighlights.get(id)===started)textHighlights.delete(id);
        },Math.max(0,2200-elapsed));
      }
      return wrapper;
    }
    for(const group of data.targets.filter(n=>n.kind===rootKind&&visible.has(n.id)))list.append(card(group,0));
    if(!list.children.length){const empty=el('div','targets-empty');empty.append(el('strong','',query?'没有匹配的'+view:'从'+view+'开始'),el('p','targets-muted',bugMode?'由 AI 整理缺陷、复现步骤和证据。尚无确认的 Bug。':caseMode?'让 AI 检查项目并导入可独立运行的测试用例。':'功能组 → 功能点 → 测试点。可以先规划，再绑定实现代码。'));if(!query)empty.append(button(bugMode?'创建 Bug':caseMode?'创建测试用例':'创建功能组',()=>edit(), 'targets-primary'),...((caseMode||bugMode)?[]:[button('导入已有定义',importer)]));list.append(empty);}
    const diagnostics=panel.querySelector('.targets-diagnostics');diagnostics.replaceChildren();for(const [file,error]of Object.entries(data.errors)){const d=el('details');d.append(el('summary','',file+' · 无法校验'),el('pre','',error));diagnostics.append(d);}
  }
  function show(view){active=view===panelView;if(panel){panel.hidden=!active;if(active){refresh();refreshRuns();}}}
  document.addEventListener('flowcanvas-view',e=>show(e.detail));
  // Watch published metadata only; never scan source files on a timer.
  let checking=false;
  setInterval(async()=>{if(!active||!data||busy||checking||document.hidden)return;checking=true;try{const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/'+endpoint+'/revision');if(response.ok){const next=await response.json();if(next.revision!==data.revision||(!caseMode&&!bugMode&&next.coverage_revision!==data.coverage?.revision))await refresh();}}catch{}finally{checking=false;}},!bugMode?1000:3000);
  fetch('/api/chat/projects').then(r=>r.json()).then(state=>{
    project=state.current;if(!project)return;panel=el('section','targets-panel'+(caseMode?' testcase-panel':''));panel.hidden=true;
    const toolbar=el('div','targets-toolbar');const search=el('input','targets-search');search.placeholder='筛选'+view+'…  /';search.setAttribute('aria-label','搜索'+view);search.oninput=()=>{query=search.value.trim().toLowerCase();searchExpanded=true;if(data)render();};
    search.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();search.value='';query='';if(data)render();}else if(event.key==='ArrowDown'){event.preventDefault();panel.querySelector('.target-card-head')?.focus();}};
    document.addEventListener('keydown',event=>{if(active&&event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.target.closest('input,textarea,select,[contenteditable="true"],dialog')){event.preventDefault();search.focus();}});
    toolbar.append(search);
    if(!caseMode&&!bugMode)toolbar.append(goalAction(null,'add-goal','AI 新增功能组','M10 5v14M3 12h14'));
    if(caseMode){const add=button('',()=>newCaseTask(add),'ai-case-add');add.title='AI 新增测试用例';add.setAttribute('aria-label','AI 新增测试用例');add.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="case-ai-spark" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4285ff"/><stop offset=".55" stop-color="#9164ec"/><stop offset="1" stop-color="#df77ba"/></linearGradient></defs><path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M8 14h8m-4-4v8"/><path class="ai-colored-spark" d="M18 2l1.2 3.8L23 7l-3.8 1.2L18 12l-1.2-3.8L13 7l3.8-1.2Z" fill="url(#case-ai-spark)" stroke="url(#case-ai-spark)"/></svg>';let previewTimer;add.onmouseenter=()=>{clearTimeout(previewTimer);previewTimer=setTimeout(()=>{if(add.matches(':hover'))newCaseTask(add,true);},220);};add.onmouseleave=()=>clearTimeout(previewTimer);add.onclick=()=>{clearTimeout(previewTimer);const card=document.querySelector('.ai-action-dialog');if(card)card.pinTask();else newCaseTask(add);};toolbar.append(add);}

    const content=[toolbar,el('div','targets-summary'),el('p','targets-error'),el('div','targets-diagnostics'),el('div','targets-tree')];
    if(!caseMode&&!bugMode){
      panel.classList.add('coverage-tabs-panel');
      tabBar=el('nav','target-coverage-tabs');tabBar.setAttribute('role','tablist');tabBar.setAttribute('aria-label','覆盖率类型');
      for(const [mode,label] of [['functional','功能覆盖率'],['line','行覆盖率']]){
        const b=button(label,()=>selectCoverageTab(mode));b.dataset.mode=mode;b.setAttribute('role','tab');tabBar.append(b);
      }
      functionalPane=el('div','target-goal-pane');functionalPane.append(...content);
      linePane=el('div','target-line-pane');lineFrame=el('iframe');lineFrame.title='行覆盖率明细';linePane.append(lineFrame);
      panel.append(el('div','target-coverage-overview'),tabBar,functionalPane,linePane);
      renderCoverageRings();
      try{coverageTab=localStorage.getItem('fc-target-coverage-tab:'+project.id)||'functional';}catch{}
      selectCoverageTab(coverageTab);
      window.addEventListener('message',event=>{
        if(event.origin!==location.origin||event.data?.type!=='flowcanvas-open-coverage')return;
        const viewer=document.querySelector('.rtl-view-panel iframe');
        if(event.source!==viewer?.contentWindow)return;
        if(!active)document.querySelector('[data-view="验证目标"]')?.click();
        selectCoverageTab(event.data.mode,event.data.instance);
      });
    }else panel.append(...content);
    let designPreviewTimer;
    if(caseMode)window.addEventListener('message',event=>{
      const frame=[...document.querySelectorAll('.rtl-view-panel iframe,.target-line-pane iframe')].find(frame=>frame.contentWindow===event.source);
      if(event.origin!==location.origin||event.source!==frame?.contentWindow||event.data?.type!=='flowcanvas-explain-design')return;
      const d=event.data;const taskKey=(d.kind||'design')+':'+d.instance;if(typeof d.instance!=='string'||typeof d.module!=='string')return;
      clearTimeout(designPreviewTimer);
      const card=document.querySelector('.ai-action-dialog');
      if(d.action==='leave'){if(card?.dataset.designInstance===taskKey)card.dispatchEvent(new Event('mouseleave'));return;}
      if(card){
        if(card.dataset.designInstance===taskKey){card.dispatchEvent(new Event('mouseenter'));if(d.action==='pin')card.pinTask();return;}
        if(!card.classList.contains('is-preview'))return;
        card.close();card.remove();
      }
      const r=frame.getBoundingClientRect(),point=d.rect||{};
      const anchor={getBoundingClientRect:()=>({left:r.left+(point.left||0),right:r.left+(point.right||0),top:r.top+(point.top||0),bottom:r.top+(point.bottom||0)}),addEventListener:()=>{},removeEventListener:()=>{},contains:()=>false};
      const open=()=>{newCaseTask(anchor,d.action!=='pin',d);const card=document.querySelector('.ai-action-dialog');if(card)card.dataset.designInstance=taskKey;};
      if(d.action==='pin')open();else designPreviewTimer=setTimeout(open,220);
    });
    host.append(panel);show(document.getElementById('project-heading').textContent);
  }).catch(()=>{});
}
mountDefinitionPanel({view:'验证目标',endpoint:'targets'});
mountDefinitionPanel({view:'测试用例',endpoint:'testcases',caseMode:true});

mountDefinitionPanel({view:'Bug 管理',endpoint:'bugs',bugMode:true});
