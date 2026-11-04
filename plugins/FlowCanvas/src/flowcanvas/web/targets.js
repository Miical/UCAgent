/* Verification goals are stable records; source scans only verify their bindings. */
function mountDefinitionPanel({view,endpoint,caseMode=false,bugMode=false}){
  const panelView=view;
  const host=document.getElementById('project-content');
  let project=null,data=null,panel=null,active=false,query='',busy=false,searchExpanded=true,selectedTarget=null;
  let lineCoverage=null;
  const opened=new Set();
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
  async function refresh(){
    if(busy)return;busy=true;
    try{
      panel.querySelector('.targets-error').textContent='';const first=!data;
      const requests=[api()];
      if(!caseMode&&!bugMode)requests.push(fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage').then(async r=>{if(!r.ok)throw Error('行覆盖率读取失败');return r.json();}));
      const results=await Promise.allSettled(requests);
      if(results[0].status==='rejected')throw results[0].reason;
      data=results[0].value;
      if(results.length>1)lineCoverage=results[1].status==='fulfilled'?results[1].value:null;
      if(first)for(const n of data.targets)if(n.kind==='group')opened.add(n.id);
      render();
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
    const name=field(content,'名称',node?.name||'');const desc=field(content,'说明',node?.description||'',true);
    const condition=(caseMode||bugMode)?field(content,bugMode?'复现步骤':'验证条件',node?.condition||'',true):null;const expected=(caseMode||bugMode)?field(content,'预期行为',node?.expected||'',true):null;
    let actual,evidence,bugStatus,severity;
    if(bugMode){
      actual=field(content,'实际表现',node?.actual||'',true);evidence=field(content,'证据',node?.evidence||'',true);
      const choice=(label,options,value)=>{const wrap=el('label','targets-field');wrap.append(el('span','',label));const select=el('select');for(const [key,title]of Object.entries(options)){const opt=el('option','',title);opt.value=key;select.append(opt);}select.value=value;wrap.append(select);content.append(wrap);return select;};
      bugStatus=choice('状态',node?.example?{unverified:'示例 · 未验证'}:bugStatuses,node?.status||'unverified');severity=choice('严重程度',severities,node?.severity||'medium');
    }
    const error=el('p','targets-error');content.append(error);
    const save=button('保存',async()=>{save.disabled=true;try{const values={description:desc.value};if(condition)Object.assign(values,{condition:condition.value,expected:expected.value});if(bugMode)Object.assign(values,{actual:actual.value,evidence:evidence.value,status:bugStatus.value,severity:severity.value});await api(node?{operation:'update',target_id:node.id,expected_revision:data.revision,values:{...values,name:name.value}}:{operation:'create',kind,parent,name:name.value,values,expected_revision:data.revision});if(parent)opened.add(parent);dialog.close();await refresh();}catch(e){error.textContent=e.message;save.disabled=false;}} ,'targets-primary');content.append(save);name.focus();
  }
  function newCaseTask(anchor,preview=false,design=null){
    if(document.querySelector('.ai-action-dialog'))return;
    const previousFocus=document.activeElement;
    const coverageTask=design?.kind==='line-coverage';
    const {dialog,content}=modal(coverageTask?'分析行覆盖率':design?'解释设计':'新增测试用例',true);dialog.classList.add('ai-action-dialog','ai-task-surface');dialog.classList.toggle('is-preview',preview);
    let leaveTimer,closeTimer;
    const enter=()=>{clearTimeout(leaveTimer);clearTimeout(closeTimer);dialog.classList.remove('is-leaving');};
    const pin=()=>{enter();dialog.classList.remove('is-preview');};dialog.pinTask=pin;
    const leave=()=>{clearTimeout(leaveTimer);leaveTimer=setTimeout(()=>{if(dialog.classList.contains('is-preview')){dialog.classList.add('is-leaving');closeTimer=setTimeout(()=>dialog.close(),140);}},160);};
    dialog.addEventListener('click',pin);dialog.addEventListener('mouseenter',enter);dialog.addEventListener('mouseleave',leave);
    anchor.addEventListener('mouseleave',leave);anchor.addEventListener('mouseenter',enter);
    const outside=event=>{if(!dialog.contains(event.target)&&!anchor.contains(event.target))dialog.close();};
    document.addEventListener('pointerdown',outside);
    dialog.addEventListener('close',()=>{clearTimeout(leaveTimer);clearTimeout(closeTimer);anchor.removeEventListener('mouseleave',leave);anchor.removeEventListener('mouseenter',enter);document.removeEventListener('pointerdown',outside);});
    content.append(el('p','ai-task-objective',coverageTask?'分析 '+design.module+' 的覆盖缺口与补测方向。':design?'解释 '+design.module+' 的功能、接口及内部逻辑。':'生成可运行用例，补齐说明与源码绑定。'));
    if(design){const scope=el('div','ai-design-scope',design.instance);scope.title=design.instance;content.append(scope);}
    if(!design)dialog.querySelector('header strong').after(el('span','ai-workflow-binding','◇ 新增测试用例'));
    const note=field(content,'想验证什么？（可选）','',true);note.placeholder=coverageTask?'补充关注的覆盖缺口或测试场景…':design?'补充想了解的设计细节…':'补充要求，例如读写边界场景…';note.setAttribute('aria-label','具体要求（可选）');note.oninput=()=>{note.style.height='36px';note.style.height=Math.min(100,Math.max(36,note.scrollHeight))+'px';};
    const choice=el('input');choice.type='checkbox';choice.setAttribute('role','switch');
    const newChat=el('div','ai-action-new-chat');choice.setAttribute('aria-label','新对话');newChat.append(choice,el('span','','新对话'));
    const error=el('p','targets-error');error.setAttribute('role','alert');const start=button('',async()=>{
      start.disabled=true;start.classList.add('is-sending');start.querySelector('span').textContent='发送中';error.textContent='';
      try{
        if(!window.FlowCanvasAI)throw Error('对话服务尚未就绪，请稍后重试');
        const text=coverageTask?'请只读分析项目 '+project.path+' 的行覆盖率，不修改文件、不运行仿真、不启动工作流。实例：'+design.instance+'；模块：'+design.module+'；源码位置：'+JSON.stringify(design.source)+'；当前累计覆盖率快照：'+JSON.stringify(design.coverage)+'。读取真实源码和项目 .flowcanvas/coverage 下的覆盖率产物，分别说明自身与包含子模块的覆盖率，定位未覆盖和部分覆盖的代码，分析可能缺失的激励、边界条件及建议补充的测试场景。引用具体文件和行号；不要把未覆盖等同于缺陷，没有数据不能声称已覆盖，快照与产物不一致时明确说明。用户要求：'+(note.value.trim()||'优先分析主要覆盖缺口，给出可执行的补测建议。'):design?'请只读分析当前项目 '+project.path+' 中的设计，不修改文件、不运行仿真、不启动其他工作流。目标实例：'+design.instance+'；模块：'+design.module+'；源码定位：'+JSON.stringify(design.source)+ '。先读取真实源码及必要的子模块，解释模块职责、端口协议、关键数据通路、状态变化及与父子模块的关系，引用具体文件和行号。不能用模块名猜测功能；无法确定的内容明确说明。用户要求：'+(note.value.trim()||'给出简洁清晰的中文设计说明。'):'【AI任务：新增测试用例】\n绑定工作流：generate-testcase。请先调用 run_workflow 启动已附加的工作流，再按阶段执行。\n请为当前项目新增一个可独立运行的测试用例。项目：'+project.path+'。用户补充：'+(note.value.trim()||'请检查已有测试和验证目标，选择尚未充分验证且不重复的场景。')+'。先调用 test_cases(operation=guide) 读取规范，检查现有用例、目标和运行模板；复用现有驱动，必要时补齐用例所需基础设施。实现后通过 test_cases 工具保存用例名称、具体说明和源码绑定，不能只给方案。不要修改 RTL，不要自动启动仿真；结束时汇报新增文件与未验证事项。';
        const result=await window.FlowCanvasAI.startCase({text,current:!choice.checked,project,requirements:note.value.trim(),design});
        if(!design){const task={id:result.id,before:(data?.targets||[]).map(n=>n.id)};localStorage.setItem('fc-case-task:'+project.id,JSON.stringify(task));}
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
        await updateCaseTask();
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
  let taskChecking=false;
  async function updateCaseTask(){
    if(!caseMode||!project||!panel||taskChecking)return;
    const bar=panel.querySelector('.ai-case-task');if(!bar)return;
    let task;try{task=JSON.parse(localStorage.getItem('fc-case-task:'+project.id)||'null');}catch{}
    if(!task){bar.hidden=true;return;}taskChecking=true;
    try{
      const response=await fetch('/api/chat/sessions/'+encodeURIComponent(task.id));
      if(response.status===404){localStorage.removeItem('fc-case-task:'+project.id);bar.hidden=true;return;}
      if(!response.ok)return;const session=await response.json();
      const running=['starting','running','stopping'].includes(session.status);
      const added=(data?.targets||[]).filter(n=>!task.before.includes(n.id));
      bar.hidden=false;bar.classList.toggle('is-running',running);
      const label=bar.querySelector('button');label.textContent=running?'正在生成测试用例 · 查看过程':added.length?'已新增 '+added.length+' 个用例 · 查看对话':'任务已停止 · 查看结果';label.onclick=()=>window.FlowCanvasAI?.open(task.id);
      if(!running&&!task.finished){task.finished=true;localStorage.setItem('fc-case-task:'+project.id,JSON.stringify(task));await refresh();
        for(const card of panel.querySelectorAll('[data-target-id]'))if(!task.before.includes(card.dataset.targetId))card.classList.add('ai-case-new');
      }
    }catch{}finally{taskChecking=false;}
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
    if(!caseMode&&!bugMode)renderCoverageRings();
    const list=panel.querySelector('.targets-tree'),summary=panel.querySelector('.targets-summary');list.replaceChildren();summary.replaceChildren();
    for(const [kind,[,label]]of Object.entries(kinds))summary.append(el('span','',data.targets.filter(n=>n.kind===kind).length+' '+label));
    if(bugMode)summary.append(el('span','',data.targets.filter(n=>!n.example&&n.status==='confirmed').length+' 已确认 · '+data.targets.filter(n=>n.example).length+' 示例'));
    const allExpanded=data.targets.length>0&&data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));
    const toggleAll=button(allExpanded?'全部折叠':'全部展开',()=>{const expanded=data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));if(expanded){opened.clear();searchExpanded=false;}else{for(const node of data.targets)opened.add(node.id);}render();},'targets-toggle-all');
    toggleAll.disabled=!data.targets.length;toggleAll.title=toggleAll.textContent;toggleAll.setAttribute('aria-label',toggleAll.textContent);summary.append(toggleAll);
    const byId=new Map(data.targets.map(n=>[n.id,n]));const visible=new Set();
    for(const n of data.targets){if(!query||[n.name,n.description,n.id,...n.bindings.map(b=>b.file)].join(' ').toLowerCase().includes(query)){let current=n;while(current){visible.add(current.id);current=byId.get(current.parent);}}}
    function card(node,depth){
      const report=data.reports[node.id]||{status:'planned',bindings:[]};const children=data.targets.filter(n=>n.parent===node.id&&visible.has(n.id));
      const wrapper=el('article','target-card target-'+node.kind);wrapper.dataset.targetId=node.id;const head=button('',()=>{
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
      head.append(chevron,el('span','target-kind',kinds[node.kind][0]));const title=el('span','target-title',node.name);head.append(title);
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
      if(bugMode){head.append(el('span','target-state',node.example?'示例 · 未验证':bugStatuses[node.status]));}else if(report.status==='planned')head.append(el('span','target-state state-planned','待实现'));
      else if(report.status!=='bound'){
        const warning=el('span','target-warning state-'+report.status);
        const message=[labels[report.status],...report.bindings.filter(b=>b.status!=='bound').map(b=>b.message|| (b.status==='changed'?'源码已变化，请核对定义':''))].filter(Boolean);
        warning.title=[...new Set(message)].join(' · ');warning.setAttribute('role','img');warning.setAttribute('aria-label',warning.title);
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
        const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M10.3 4.3 2.7 17.5A2 2 0 0 0 4.4 20h15.2a2 2 0 0 0 1.7-2.5L13.7 4.3a2 2 0 0 0-3.4 0ZM12 9v4m0 3h.01');svg.append(path);warning.append(svg);head.append(warning);
      }
      const header=el('div','target-header'+(selectedTarget===node.id?' is-selected':''));
      head.title=node.name;
      head.onkeydown=event=>{
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          event.preventDefault();const rows=[...panel.querySelectorAll('.target-card-head')],index=rows.indexOf(head);
          rows[Math.max(0,Math.min(rows.length-1,index+(event.key==='ArrowDown'?1:-1)))]?.focus();
        }else if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
          event.preventDefault();if((event.key==='ArrowRight')!==(head.getAttribute('aria-expanded')==='true'))head.click();
        }
      };
      header.onclick=event=>{if(!event.target.closest('button'))head.click();};
      const controls=el('div','target-actions target-header-actions');controls.append(smallAction('编辑','m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15Z',()=>edit(node)));if(!caseMode&&!bugMode&&node.kind!=='testpoint')controls.append(smallAction('添加'+(node.kind==='group'?'功能点':'测试点'),'M12 5v14M5 12h14',()=>edit(null,node.kind==='group'?'point':'testpoint',node.id)));
      if(caseMode){
        const run=smallAction('运行测试用例','m8 5 11 7-11 7Z',async()=>{run.disabled=true;try{
          const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/test-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:node.id})});
          const result=await response.json();if(!response.ok)throw Error(result.detail||'启动失败');
          document.dispatchEvent(new CustomEvent('flowcanvas-test-run',{detail:{project,run:result}}));
        }catch(e){fail(e);}finally{run.disabled=!data.runner;}});
        run.disabled=!data.runner;if(!data.runner)run.title='请让 AI 配置项目运行模板';controls.append(run);
      }
      const links=el('div','target-title-sources');
      for(const binding of (caseMode||bugMode?report.bindings:[])){
        const label=binding.file.split('/').at(-1)+(binding.line?':'+binding.line+'–'+binding.end_line:'');
        const b=button(label,()=>source(binding),'target-source target-title-source');b.disabled=!binding.line;
        b.title=binding.file+(binding.message?' · '+binding.message:' · 查看源码');b.setAttribute('aria-label','查看源码 '+binding.file+(binding.line?' 第 '+binding.line+' 行':''));links.append(b);
      }
      if(!caseMode&&!bugMode){
        const score=head.querySelector('.target-coverage-score'),heading=el('div','goal-row-heading');
        heading.append(head,links);header.append(heading,score,controls);
      }else header.append(head,links,controls);
      wrapper.append(header);
      if(opened.has(node.id)||(query&&searchExpanded)){
        const body=el('div','target-card-body');
        for(const [label,value]of [['',node.description],...((caseMode||bugMode)?[[bugMode?'复现步骤':'条件',node.condition],['预期',node.expected]]:[]),...(bugMode?[['实际',node.actual],['证据',node.evidence],['严重程度',severities[node.severity]],['关联用例',node.case_ids.join('、')]]:[])]){if(value){const p=el('p');if(label)p.append(el('strong','',label+' · '));p.append(document.createTextNode(value));body.append(p);}}

        if(!caseMode&&!bugMode){
          const c=coverageFor(node);
          if(report.bindings.length){
            const detail=el('div','target-coverage-detail');
            for(const binding of report.bindings){
              const observation=c.status==='available'?c.rows.find(row=>row.binding_id===binding.id):null;
              const path=observation?.source_path||[binding.scope,binding.symbol].filter(Boolean).join('.')||binding.file;
              const context=binding.line?button(path,()=>source(binding),'target-coverage-context goal-source-link'):el('div','target-coverage-context',path);
              if(binding.line){context.title='查看源码';context.setAttribute('aria-label','查看 '+path+' 的源码');}
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
        if(report.status==='changed')actions.append(button('核对后确认',()=>{const {dialog,content}=modal('确认源码与目标一致');content.append(el('p','','请先查看当前源码，核对描述、验证条件和预期行为。确认仅更新代码基线，不代表仿真通过。'));const error=el('p','targets-error');content.append(error,button('已核对，确认当前版本',async()=>{try{await api({operation:'accept',target_id:node.id,expected_revision:data.revision,values:Object.fromEntries(report.bindings.map(b=>[b.id,b.fingerprint]))});dialog.close();refresh();}catch(e){error.textContent=e.message;}},'targets-primary'));}));
        if(actions.children.length)body.append(actions);for(const child of children)body.append(card(child,depth+1));wrapper.append(body);
      }else if(children.length){head.title=children.length+' 个子目标';}
      return wrapper;
    }
    for(const group of data.targets.filter(n=>n.kind===rootKind&&visible.has(n.id)))list.append(card(group,0));
    if(!list.children.length){const empty=el('div','targets-empty');empty.append(el('strong','',query?'没有匹配的'+view:'从'+view+'开始'),el('p','targets-muted',bugMode?'由 AI 整理缺陷、复现步骤和证据。尚无确认的 Bug。':caseMode?'让 AI 检查项目并导入可独立运行的测试用例。':'功能组 → 功能点 → 测试点。可以先规划，再绑定实现代码。'));if(!query)empty.append(button(bugMode?'创建 Bug':caseMode?'创建测试用例':'创建功能组',()=>edit(), 'targets-primary'),...((caseMode||bugMode)?[]:[button('导入已有定义',importer)]));list.append(empty);}
    const diagnostics=panel.querySelector('.targets-diagnostics');diagnostics.replaceChildren();for(const [file,error]of Object.entries(data.errors)){const d=el('details');d.append(el('summary','',file+' · 无法校验'),el('pre','',error));diagnostics.append(d);}
  }
  function show(view){active=view===panelView;if(panel){panel.hidden=!active;if(active)refresh();}}
  document.addEventListener('flowcanvas-view',e=>show(e.detail));
  // Watch published metadata only; never scan source files on a timer.
  let checking=false;
  setInterval(async()=>{if(!active||!data||busy||checking||document.hidden)return;checking=true;try{const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/'+endpoint+'/revision');if(response.ok){const next=await response.json();if(next.revision!==data.revision||(!caseMode&&!bugMode&&next.coverage_revision!==data.coverage?.revision))await refresh();}}catch{}finally{checking=false;}},3000);
  fetch('/api/chat/projects').then(r=>r.json()).then(state=>{
    project=state.current;if(!project)return;panel=el('section','targets-panel');panel.hidden=true;
    const toolbar=el('div','targets-toolbar');const search=el('input','targets-search');search.placeholder='筛选'+view+'…  /';search.setAttribute('aria-label','搜索'+view);search.oninput=()=>{query=search.value.trim().toLowerCase();searchExpanded=true;if(data)render();};
    search.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();search.value='';query='';if(data)render();}else if(event.key==='ArrowDown'){event.preventDefault();panel.querySelector('.target-card-head')?.focus();}};
    document.addEventListener('keydown',event=>{if(active&&event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.target.closest('input,textarea,select,[contenteditable="true"],dialog')){event.preventDefault();search.focus();}});
    toolbar.append(search);
    if(caseMode){const add=button('',()=>newCaseTask(add),'ai-case-add');add.title='AI 新增测试用例';add.setAttribute('aria-label','AI 新增测试用例');add.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="case-ai-spark" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4285ff"/><stop offset=".55" stop-color="#9164ec"/><stop offset="1" stop-color="#df77ba"/></linearGradient></defs><path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M8 14h8m-4-4v8"/><path class="ai-colored-spark" d="M18 2l1.2 3.8L23 7l-3.8 1.2L18 12l-1.2-3.8L13 7l3.8-1.2Z" fill="url(#case-ai-spark)" stroke="url(#case-ai-spark)"/></svg>';let previewTimer;add.onmouseenter=()=>{clearTimeout(previewTimer);previewTimer=setTimeout(()=>{if(add.matches(':hover'))newCaseTask(add,true);},220);};add.onmouseleave=()=>clearTimeout(previewTimer);add.onclick=()=>{clearTimeout(previewTimer);const card=document.querySelector('.ai-action-dialog');if(card)card.pinTask();else newCaseTask(add);};toolbar.append(add);}

    const content=[toolbar,el('div','targets-summary'),el('p','targets-error'),el('div','targets-diagnostics'),el('div','targets-tree')];
    if(caseMode){const bar=el('div','ai-case-task');bar.hidden=true;bar.append(button('',()=>{}),button('×',()=>{localStorage.removeItem('fc-case-task:'+project.id);bar.hidden=true;},'ai-task-dismiss'));bar.lastChild.title='隐藏任务提示';content.splice(1,0,bar);setInterval(()=>{if(active&&!document.hidden)updateCaseTask();},2000);}
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
