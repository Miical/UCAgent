/* Verification goals are stable records; source scans only verify their bindings. */
function mountDefinitionPanel({view,endpoint,caseMode=false,bugMode=false}){
  const panelView=view;
  const host=document.getElementById('project-content');
  let project=null,data=null,panel=null,active=false,query='',busy=false,searchExpanded=true,selectedTarget=null;
  let lineCoverage=null,inlineEditing=false,caseDetail=null;
  let caseResults=new Map(),runCases=new Set(),pendingRun=null,runsLoaded=false,runsChecking=false;
  const opened=new Set();
  const localTextWrites=new Map(),textHighlights=new Map();
  const operationAnimationStart=new Map();
  const coverageModes=[['functional','功能覆盖率'],['line','行覆盖率'],['toggle','翻转覆盖率'],['branch','分支覆盖率'],['cond','条件覆盖率'],['fsm','状态机覆盖率'],['assert','断言']];
  let coverageTab='functional',functionalPane=null,linePane=null,lineFrame=null,tabBar=null;
  function selectCoverageTab(mode,instance){
    if(caseMode||bugMode||!tabBar)return;
    coverageTab=coverageModes.some(([key])=>key===mode)?mode:'functional';
    functionalPane.hidden=coverageTab!=='functional';linePane.hidden=coverageTab==='functional';
    for(const b of tabBar.querySelectorAll('button')){b.classList.toggle('active',b.dataset.mode===coverageTab);b.setAttribute('aria-selected',String(b.dataset.mode===coverageTab));b.tabIndex=b.dataset.mode===coverageTab?0:-1;}
    for(const card of panel.querySelectorAll('.coverage-overview-item'))card.setAttribute('aria-pressed',String(card.dataset.metric===coverageTab));
    try{localStorage.setItem('fc-target-coverage-tab:'+project.id,coverageTab);}catch{}
    if(coverageTab!=='functional'){
      if(!lineFrame.src||new URL(lineFrame.src).searchParams.get('metric')!==coverageTab){
        const view='/api/chat/projects/'+encodeURIComponent(project.id)+'/rtl-view';
        lineFrame.src='/rtl-viewer/?coverageOnly=1&metric='+encodeURIComponent(coverageTab)+'&view='+encodeURIComponent(view)+(instance?'&focus='+encodeURIComponent(instance):'');
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
  async function saveCard(request){
    if(bugMode)return api(request);
    let revision=request.expected_revision,id=request.target_id;
    if(!id){
      const allocated=await api({operation:'apply',expected_revision:revision,count:1});
      revision=allocated.revision;id=allocated.ids[0];
    }
    const started=await api({operation:'begin_modify',expected_revision:revision,ids:[id],action:'update'});
    const updated=await api({operation:'update',expected_revision:started.revision,cards:[{id,...request.values,...(!caseMode&&request.kind?{kind:request.kind,parent:request.parent||null}:{}),...(request.name?{name:request.name}:{})}]});
    return api({operation:'end_modify',expected_revision:updated.revision,ids:[id],...(!caseMode?{planned:true}:{})});
  }
  function fail(error){panel.querySelector('.targets-error').textContent=error.message;}
  /** Resume text highlights across list refreshes without replaying a completed animation. */
  function paintTextHighlights(wrapper,node){
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
  }
  async function animateRemovedCards(next){
    if(!data||!active||document.hidden||matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    const remaining=new Set(next.targets.map(node=>node.id));
    const removed=new Set(data.targets.filter(node=>!remaining.has(node.id)).map(node=>node.id));
    if(!removed.size)return;
    const cards=[...panel.querySelectorAll('.target-card[data-target-id],.verification-item[data-target-id]')].filter(card=>{
      if(!removed.has(card.dataset.targetId))return false;
      let ancestor=card.parentElement.closest('.target-card,.verification-item');
      while(ancestor){if(removed.has(ancestor.dataset.targetId))return false;ancestor=ancestor.parentElement.closest('.target-card,.verification-item');}
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
      if(!bugMode)results[0].value.targets=results[0].value.targets.filter(node=>node.lifecycle!=='draft'&&node.kind);
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
  function renderCoverageSummary(){
    const host=panel.querySelector('.target-coverage-overview');if(!host)return;
    const records=(data?.targets||[]).filter(n=>n.kind==='group').map(n=>data.coverage?.targets?.[n.id]);
    const bins=[...new Map(records.flatMap(r=>r?.observations||[]).map(r=>[r.instance+'::'+r.point,r])).values()];
    const instances=[...new Map((lineCoverage?.report?.instances||[]).map(r=>[r.path,r])).values()];
    const kinds=lineCoverage?.report?.code_metrics||{};
    const metrics=[
      {mode:'functional',label:'功能覆盖率',rows:bins,valid:records.length>0&&records.every(r=>r?.status==='available'),unit:'bin'},
      {mode:'line',label:'行覆盖率',rows:instances,valid:instances.length>0,unit:'行'},
      ...coverageModes.slice(2).map(([mode,label])=>({
        mode,label:mode==='assert'?'断言尝试覆盖率':label,rows:kinds[mode]?.dut?[kinds[mode].dut]:[],valid:!!kinds[mode]?.dut?.total,unit:'项'
      }))
    ];
    for(const metric of metrics){
      let card=host.querySelector('[data-metric="'+metric.mode+'"]');
      if(!card){
        card=button('',()=>selectCoverageTab(metric.mode),'coverage-overview-item');card.dataset.metric=metric.mode;
        const ring=el('span','coverage-overview-ring');
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 64 64');svg.setAttribute('aria-hidden','true');
        for(const cls of ['coverage-ring-track','coverage-ring-fill']){
          const circle=document.createElementNS(svg.namespaceURI,'circle');
          for(const [key,value] of Object.entries({cx:32,cy:32,r:27,pathLength:100,class:cls}))circle.setAttribute(key,value);
          svg.append(circle);
        }
        ring.append(svg,el('strong','coverage-metric-percent'));
        card.append(ring,el('span','coverage-metric-label',metric.label),el('span','coverage-metric-count'));
        host.append(card);
      }
      const covered=metric.rows.reduce((sum,row)=>sum+row.covered,0),total=metric.rows.reduce((sum,row)=>sum+row.total,0);
      const available=metric.valid&&total>0,percent=available?Math.min(100,Math.max(0,100*covered/total)):0;
      card.dataset.available=String(available);
      card.setAttribute('aria-pressed',String(metric.mode===coverageTab));
      card.querySelector('.coverage-ring-fill').style.strokeDashoffset=String(100-percent);
      card.querySelector('.coverage-metric-percent').textContent=available?percent.toFixed(1)+'%':'—';
      card.querySelector('.coverage-metric-count').textContent=available?covered+' / '+total+' '+metric.unit:'暂无数据';
      if(metric.mode==='assert')card.title=available?'已尝试的断言比例；累计失败 '+(kinds.assert?.dut?.items||[]).reduce((sum,item)=>sum+(item.failures||0),0)+' 次，成功与失败明细请点击查看':'暂无断言数据';
      card.setAttribute('aria-label',metric.label+'，'+(available?percent.toFixed(1)+'%，'+covered+'/'+total:'暂无有效数据')+'，点击查看明细');
    }
    let menu=host.querySelector('.coverage-overview-menu');
    if(!menu){
      menu=el('details','coverage-overview-menu');const trigger=el('summary','','⋯');trigger.title='覆盖率操作';trigger.setAttribute('aria-label','覆盖率操作');
      const reset=button('清空累计',async()=>{
        menu.open=false;
        if(!confirm('清空覆盖率累计并删除共享数据库？历史运行和日志会保留。'))return;
        try{const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage/reset',{method:'POST'});if(!response.ok)throw Error(response.status===409?'测试运行中，请结束后再清空累计':'清空失败');await refresh();if(lineFrame?.src)lineFrame.contentWindow.location.reload();}catch(e){fail(e);}
      },'coverage-menu-reset');
      menu.append(trigger,reset);const actions=el('div','coverage-overview-actions');actions.append(el('span','coverage-shared-runs'),menu);host.prepend(actions);
      menu.addEventListener('focusout',()=>setTimeout(()=>{if(!menu.contains(document.activeElement))menu.open=false;},0));
      menu.addEventListener('keydown',event=>{if(event.key==='Escape'){menu.open=false;trigger.focus();}});
    }
    const runs=lineCoverage?.runs?.length??data?.coverage?.runs??0;
    host.querySelector('.coverage-shared-runs').textContent='累计 '+runs+' 次';
    menu.querySelector('button').disabled=!runs&&!lineCoverage?.build_id;
  }
  function modal(title,floating=false){const dialog=el('dialog','targets-dialog');const heading=el('header');heading.append(el('strong','',title),button('×',()=>dialog.close(),'targets-close'));const content=el('div','targets-dialog-content');dialog.append(heading,content);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});if(floating)dialog.show();else dialog.showModal();return {dialog,content};}
  function field(parent,label,value='',multi=false){const wrapper=el('label','targets-field');wrapper.append(el('span','',label));const input=el(multi?'textarea':'input');input.value=value;if(multi)input.rows=3;wrapper.append(input);parent.append(wrapper);return input;}
  async function source(binding){
    const {dialog,content}=modal('源码 · '+binding.file);dialog.classList.add('targets-source-dialog');content.textContent='正在读取并校验…';
    try{
      const response=await api({operation:bugMode?'source':'inspect',binding_id:binding.id});const result=bugMode?response:response.source;
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
    if(!data||(!caseMode&&!bugMode))return;
    const {dialog,content}=modal(node?'编辑'+kinds[node.kind][1]:'新建'+kinds[kind][1]);
    const caseTitle=caseMode?field(content,'标题',node?.title||''):null;if(caseTitle){caseTitle.required=true;caseTitle.placeholder='例如：连续随机读访问';}
    const name=field(content,caseMode?'代码名称（类名 / 函数名）':'名称',node?.name||'');const desc=field(content,'说明',node?.description||'',true);
    const runnerCommand=caseMode?field(content,'运行命令',node?.runner?.command||''):null;
    const runnerDescription=caseMode?field(content,'运行说明',node?.runner?.description||''):null;
    const condition=bugMode?field(content,'复现步骤',node?.condition||'',true):null;const expected=bugMode?field(content,'预期行为',node?.expected||'',true):null;
    let actual,evidence,bugStatus,severity;
    if(bugMode){
      actual=field(content,'实际表现',node?.actual||'',true);evidence=field(content,'证据',node?.evidence||'',true);
      const choice=(label,options,value)=>{const wrap=el('label','targets-field');wrap.append(el('span','',label));const select=el('select');for(const [key,title]of Object.entries(options)){const opt=el('option','',title);opt.value=key;select.append(opt);}select.value=value;wrap.append(select);content.append(wrap);return select;};
      bugStatus=choice('状态',node?.example?{unverified:'示例 · 未验证'}:bugStatuses,node?.status||'unverified');severity=choice('严重程度',severities,node?.severity||'medium');
    }
    const error=el('p','targets-error');content.append(error);
    const save=button('保存',async()=>{save.disabled=true;try{const values={description:desc.value};if(caseTitle){if(!caseTitle.value.trim())throw Error('请填写测试用例标题');values.title=caseTitle.value.trim();if(!runnerCommand.value.trim()||!runnerDescription.value.trim())throw Error('请填写此用例的运行命令和说明');values.runner={command:runnerCommand.value.trim(),description:runnerDescription.value.trim()};}if(condition)Object.assign(values,{condition:condition.value,expected:expected.value});if(bugMode)Object.assign(values,{actual:actual.value,evidence:evidence.value,status:bugStatus.value,severity:severity.value});await saveCard(node?{operation:'update',target_id:node.id,expected_revision:data.revision,values:{...values,name:name.value}}:{operation:'create',kind,parent,name:name.value,values,expected_revision:data.revision});if(node){for(const [key,value] of Object.entries({...values,name:name.value}))localTextWrites.set(node.id+':'+key,value);}if(parent)opened.add(parent);dialog.close();await refresh();}catch(e){error.textContent=e.message;save.disabled=false;}} ,'targets-primary');content.append(save);(caseTitle||name).focus();
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
      editor.maxLength=200;const header=label.closest('.target-header,.case-detail-title')||label.parentElement,rect=label.getBoundingClientRect(),parent=header.getBoundingClientRect();
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
      try{await saveCard({operation:'update',target_id:node.id,expected_revision:revision,values:{[key]:value}});localTextWrites.set(node.id+':'+key,value);close();await refresh();}
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
    const open=preview=>{const context=kind==='new-testcase'?null:{kind,instance:node?.name||(['import-goals','plan-goals','import-testcases'].includes(kind)?project.name:'功能组'),module:node?.name||(['import-goals','plan-goals','import-testcases'].includes(kind)?project.name:'功能组'),target:node?{id:node.id,kind:node.kind,name:node.name}:null};if(node&&!caseMode&&!bugMode){if(node.kind!=='group')context.testcase={...node,title:node.name,entity_type:'target'};if(kind==='functional-coverage')context.coverage={target_id:node.id};}newCaseTask(action,preview,context);const card=document.querySelector('.ai-action-dialog');if(card)card.dataset.goalTask=key;};
    let timer;action.onmouseenter=()=>{timer=setTimeout(()=>{if(action.matches(':hover'))open(true);},220);};action.onmouseleave=()=>clearTimeout(timer);
    action.onclick=()=>{clearTimeout(timer);const card=document.querySelector('.ai-action-dialog');if(card?.dataset.goalTask===key)card.pinTask();else{card?.close();open(false);}};
    return action;
  }
  function newCaseTask(anchor,preview=false,design=null){
    if(document.querySelector('.ai-action-dialog'))return;
    const previousFocus=document.activeElement;
    const explanationTask=design?.kind==='target-explanation',explanationTitle=design?.testcase?.kind==='point'?'生成功能点说明':'生成测试点说明',targetTask=design?.testcase?.entity_type==='target',moduleTask=design?.kind==='module-overview',overviewTask=design?.kind==='case-overview',waveTask=design?.kind==='case-waveform',scopedTask=targetTask||moduleTask||overviewTask||waveTask;
    const importCases=design?.kind==='import-testcases',planGoals=design?.kind==='plan-goals',importGoals=design?.kind==='import-goals',addGoal=design?.kind==='add-goal',deleteGoal=design?.kind==='delete-goal',goalTask=design?.kind==='functional-coverage',runTask=design?.kind==='run-testcase',deleteTask=design?.kind==='delete-testcase';
    const {dialog,content}=modal(explanationTask?explanationTitle:moduleTask?'生成模块概览':waveTask?'解读波形':overviewTask?'生成用例概览':importCases?'同步测试用例':planGoals?'规划验证目标':importGoals?'同步验证目标':addGoal?'新增验证目标':deleteGoal?'删除验证目标':goalTask?'分析功能覆盖率':runTask?'运行测试用例':deleteTask?'删除测试用例':design?'解释设计':'新增测试用例',true);dialog.classList.add('ai-action-dialog','ai-task-surface');dialog.classList.toggle('is-preview',preview);
    let leaveTimer,closeTimer;
    const enter=()=>{clearTimeout(leaveTimer);clearTimeout(closeTimer);dialog.classList.remove('is-leaving');};
    const pin=()=>{enter();dialog.classList.remove('is-preview');};dialog.pinTask=pin;
    const leave=()=>{clearTimeout(leaveTimer);leaveTimer=setTimeout(()=>{if(dialog.classList.contains('is-preview')){dialog.classList.add('is-leaving');closeTimer=setTimeout(()=>dialog.close(),140);}},160);};
    dialog.addEventListener('pointerdown',pin);dialog.addEventListener('click',pin);dialog.addEventListener('mouseenter',enter);dialog.addEventListener('mouseleave',leave);
    anchor.addEventListener('mouseleave',leave);anchor.addEventListener('mouseenter',enter);
    const outside=event=>{if(!dialog.contains(event.target)&&!anchor.contains(event.target))dialog.close();};
    document.addEventListener('pointerdown',outside);
    dialog.addEventListener('close',()=>{clearTimeout(leaveTimer);clearTimeout(closeTimer);anchor.removeEventListener('mouseleave',leave);anchor.removeEventListener('mouseenter',enter);document.removeEventListener('pointerdown',outside);});
    content.append(el('p','ai-task-objective',explanationTask?'阅读真实源码，解释验证目标、检查方式及证据边界。':moduleTask?'阅读当前模块源码，生成职责、结构图、工作机制和关键规则。':waveTask?'选取关键信号，标注关键时间点并逐点解释。':overviewTask?'阅读当前用例源码，生成验证目的、验证方法和技术流程。':importCases?'核对已有用例，补齐缺失项，保留未变化的内容。':planGoals?'执行 UCAgent 原始规划与覆盖模型阶段。':importGoals?'核对项目覆盖定义，补齐缺失目标，保留已有内容。':addGoal?'补充验证目标、说明与源码绑定。':deleteGoal?'删除选中目标及其子目标，保留无关代码。':goalTask?'分析覆盖目标与未命中 bin，给出补测建议。':runTask?'运行用例，持续监控异常并分析结果。':deleteTask?'删除指定用例及专属代码，保留共享组件。':design?'解释 '+design.module+' 的功能、接口及内部逻辑。':'生成可运行用例，补齐说明与源码绑定。'));
    if(design&&!scopedTask){const scope=el('div','ai-design-scope',design.instance);scope.title=design.instance;content.append(scope);}
    if(!design||planGoals||importGoals||importCases)dialog.querySelector('header strong').after(el('span','ai-workflow-binding',importCases?'◇ 同步测试用例':importGoals?'◇ 同步验证目标':planGoals?'◇ 规划验证目标':'◇ 新增测试用例'));
    const note=field(content,scopedTask?'补充要求（可选）':'想验证什么？（可选）','',true);note.placeholder=explanationTask?'补充希望解释的行为、触发条件或检查细节…':moduleTask?'补充希望重点解释的结构、接口或边界行为…':waveTask?'补充关注的信号、行为或异常…':overviewTask?'补充希望重点解释的场景或实现…':importCases?'补充用例路径、运行入口或同步范围…':planGoals?'补充验证范围、重点或约束…':importGoals?'补充扫描范围或需要关注的验证代码…':addGoal?'补充希望覆盖的功能或场景…':deleteGoal?'补充删除范围或需要保留的内容…':goalTask?'补充关注的功能、覆盖缺口或激励场景…':runTask?'补充关注的异常或运行要求…':deleteTask?'补充删除范围或需要保留的内容…':design?'补充想了解的设计细节…':'补充要求，例如读写边界场景…';note.setAttribute('aria-label','具体要求（可选）');note.oninput=()=>{note.style.height='36px';note.style.height=Math.min(100,Math.max(36,note.scrollHeight))+'px';};
    const choice=el('input');choice.type='checkbox';choice.setAttribute('role','switch');
    const newChat=el('label','ai-action-new-chat');choice.setAttribute('aria-label','新对话');newChat.append(choice,el('span','','新对话'));
    const error=el('p','targets-error');error.setAttribute('role','alert');const start=button('',async()=>{
      start.disabled=true;start.classList.add('is-sending');start.querySelector('span').textContent='发送中';error.textContent='';
      try{
        if(!window.FlowCanvasAI)throw Error('对话服务尚未就绪，请稍后重试');
        if(targetTask){const target=data.targets.find(item=>item.id===design.testcase.id);if(!target)throw Error('验证目标已删除，请刷新后重试');goalDetail(target);}
        const caseContext=design?.testcase?JSON.stringify(design.testcase):'';
        const text=explanationTask?'请仅为项目 '+project.path+' 中的验证目标 '+design.testcase.id+'（'+design.testcase.kind+'，'+design.testcase.name+'）生成易理解的中文说明。先 verification_targets(operation=inspect,ids=['+JSON.stringify(design.testcase.id)+']) 获取最新目标、源码绑定、revision、explanation_schema 和 explanation_guide，完整阅读规范；按需要读取实际源码、相关调用及 test_cases(inspect) 中的真实用例。功能点侧重 summary 功能说明、approach 验证思路及测试点分解；测试点侧重 summary 验证意图、conditions 触发条件、expected 预期结果。implementation 解释实际激励、采样和检查逻辑，附精简的真实 assignments(expression/meaning/file/line/end_line) 与 evidence，并明确 confidence。check_kind 区分只有覆盖采样、断言、比较或未知，不能把覆盖命中当作验证通过。related_cases 只填当前项目已存在且有实际依据的 case_id 和 reason，不根据同名或同文件强行关联。coverage_notes 解释覆盖语义和限制，不填写手写运行数字。保留所有既有内容，仅通过 begin_modify(ids)、update(cards=[{id,explanation}])、end_modify(ids) 更新当前目标说明，每步使用最新 expected_revision；已有修改先核对所有权，不能接管别人的操作。source_hashes 由平台生成，不要填写。没有实现或缺乏依据的内容留空或明确推断，未实现目标保持 planned 状态。不得修改源码、名称、父子关系、绑定、用例或运行命令，不运行测试，不直接编辑管理 JSON。用户要求：'+(note.value.trim()||'让读者理解目标在检查什么、怎样触发、怎样判断，并能追溯到源码。'):moduleTask?'请为项目 '+project.path+' 中的模块实例 '+design.testcase.instance+' 生成模块概览。先调用 module_overview(operation=inspect,instance='+JSON.stringify(design.testcase.instance)+')，先完整阅读返回的 guide（模块概览编写规范），再读取实际源码、JSON schema、expected_revision 和 source_revision，并以该规范约束内容与呈现，并按需阅读相关源码。严格依照 schema 填写模块职责、核心结构、子模块、工作机制、关键规则、接口与参数。inspect 返回的 children 是当前配置下的直接子模块，submodules 必须按每个 child.key 填写其在父模块中的具体用途，不遗漏、不虚构，不把孙模块混入。结构图的节点数量由实际实现决定，不固定套用四个单元；保留真实分支、并行、扇出和反馈。用 nodes/edges 描述功能单元和真实连接，每个节点的 text 只用一句简短说明概括职责或结构关系，附源码位置；图下紧凑展示，不展开 assignments。具体条件、时序和关键代码放在工作机制与关键规则中，不在结构说明中重复。不要只重复图中信号名，不写时间轴，不提供坐标或图片，页面会统一布局绘制。工作机制和规则参考测试用例格式：清晰说明、关键 assignments(expression/meaning/file/line/end_line) 和源码引用，expression 必须是引用范围内的真实代码，不用省略号伪造片段。源码事实用 source，推断用 inferred 并在文字中明确说明，避免重复和嵌套卡片。调用 module_overview(operation=save,instance=同一实例,overview=符合 schema 的数据,expected_revision=inspect返回值,source_revision=inspect返回值) 保存；发生冲突先重新 inspect，不覆盖其他修改。不修改 UI、RTL、运行命令或管理 JSON，不运行测试，不声称验证通过。用户要求：'+(note.value.trim()||'生成简洁准确、可直接对应源码的中文模块概览。'):waveTask?'请分析当前用例 '+caseContext+' 的指定运行 '+design.run_id+'、波形 '+design.waveform+'。使用 case_waveform 工具，先 inspect 获取真实信号目录、原生时间边界和保存格式，结合 test_cases(inspect) 的源码绑定理解角色；用 values 分页读取相关时间窗的实际四态信号变化，再为每个关键时间点分别选择能解释当时行为的最小信号组，避免每条都填同一份大信号列表；analysis.signals 是各点信号的去重并集，points[i].signals 仅填写该点所需信号。每个点必须有 title、explanation、confidence 和 signals，并用 start/end 指定能看清前后关键变化的紧凑时间窗口，满足 start <= time <= end 且 start < end，均使用原生时间刻度：解释当时观察到什么、参与信号如何配合、为什么关键，并区分直接观察与因果推断，不把日志周期号当作波形原生时间。最后调用 case_waveform(save,run_id='+design.run_id+',waveform='+design.waveform+',expected_revision=最新版本,analysis={signals,points}) 保存，time 使用原生刻度十进制字符串；页面会选中信号并标记所有点位，AI 解读单独显示。每个点都要解释，不制造未读取的信号、时间或通过结论。若文件缺失、仍在写入或无法读取，明确报告原因，不运行测试、不修改源码、不切换到其他运行、不直接编辑管理 JSON。用户要求：'+(note.value.trim()||'解释此用例的关键行为与值得关注的点位。'):overviewTask?'请为项目 '+project.path+' 中的指定用例生成概览：'+caseContext+'。先调用 test_cases(inspect) 获取最新规范、revision 和源码绑定，阅读实际实现、相关调用及检查逻辑。仅更新此用例的 overview 和 timeline：overview.purpose 写简洁验证目的，overview.method 写刺激、约束及检查策略；timeline 按真实技术阶段连续解释，包含必要的 why/how/actors，以及关键 assignments(expression/meaning/file/line) 和 evidence。避免重复概览、嵌套卡片和框架术语堆砌；源码事实标为 source，推断标为 inferred，不能把解释、退出码或覆盖率当作运行通过证明。通过 begin_modify、update(cards)、end_modify 保存，使用每次返回的最新 expected_revision；已有修改流程先核对所有权再恢复，不改写其他会话的工作。不修改源码、运行命令或其他用例，不执行测试。用户要求：'+(note.value.trim()||'生成简洁、准确、可追溯到源码的中文概览。'):importCases?'请启动附加的 import-testcases 工作流，同步项目 '+project.path+' 的已有测试用例：复用已有 ID，只补缺失项；未变化的标题、说明、概览、源码绑定和运行命令保持不动，只有实际源码或配置变化、有明确问题时才最小修复并记录原因。保留历史运行，不自动删除旧用例。复用已有有效的完整覆盖率冒烟凭证，只有缺失或失效时才运行一个短冒烟，不逐个运行其他用例。最终阶段只核对已有结果。用户要求：'+(note.value.trim()||'核对已有用例，只补缺失项，保留未变化的内容。'):planGoals?'请启动附加的 plan-goals 工作流，按各阶段原始要求执行。用户要求：'+(note.value.trim()||'按工作流要求执行。'):importGoals?'请启动附加的 import-targets 工作流，读取当前项目已有验证环境，按阶段同步已有 covergroup、coverpoint、cross 和普通 bins；先检查已有目标，沿用稳定 ID 和绑定，缺少时才新增。未变化且无问题的目标不得改名、重写说明或重新绑定；只有源码变化或确有问题才做最小修复，并在同步清单 repairs 中记录 id 和具体 reason。不得自动删除旧目标，不改无关目标；不把断言、scoreboard、sequence 或 Python 检查注册成验证目标，不导入 ignore_bins、illegal_bins，也不虚构隐式 bins。用户要求：'+(note.value.trim()||'参考已有验证文档生成简洁中文标题和描述；无文档时依据实际源码生成，并核对全部绑定。'):addGoal?'【AI任务：新增验证目标】项目：'+project.path+'；父目标：'+JSON.stringify(design.target)+'。先 verification_targets(inspect) 读取规范和已有目标。按层级通过 apply(count) 申请新 ID，再 begin_modify(ids,action=update)，使用 update(cards) 填写中文 name、description、parent 和有依据的 bindings，最后 end_modify；无实现的规划显式 planned=true。不要重复申请；中断用原 ID 再次 begin_modify 恢复。不修改 RTL，不运行仿真。用户要求：'+(note.value.trim()||'新增有意义的验证目标。'):deleteGoal?'【AI任务：删除验证目标】项目：'+project.path+'；指定目标：'+JSON.stringify(design.target)+'。先 verification_targets(inspect) 获取最新 revision，立即 begin_modify(ids,action=delete,cascade=true) 登记目标及子级，成功后再检查源码、共享引用和实际删除范围。清理专属实现，保留共享代码、RTL、其他用例和历史产物。用 end_modify(ids=登记返回的全部ID) 完成删除。不直接编辑管理文件，不运行仿真。用户要求：'+(note.value.trim()||'删除此目标及子目标，保留无关代码。'):goalTask?'【AI任务：分析功能覆盖率】请只读分析项目 '+project.path+' 的验证目标：'+design.instance+'。选中目标及子目标快照：'+JSON.stringify(design.coverage)+'。先调用 verification_targets(inspect) 读取最新定义和覆盖数据，按稳定 ID 定位目标，读取对应源码、coverpoint/bin 定义和相关测试。解释目标意图、采样条件、累计命中情况与缺口，明确区分功能组、功能点、测试点和 bin，不把命中等同于正确性通过，不把缺失或过期数据当作未命中。针对未命中项提出具体激励、边界条件和可复用的用例，引用真实源码位置。不要修改文件、运行仿真或启动工作流。用户要求：'+(note.value.trim()||'优先分析主要缺口，给出有依据的补测建议。'):runTask?'【AI任务：运行测试用例】项目：'+project.path+'；指定用例：'+caseContext+'。先用 test_cases(inspect) 核实 ID 与运行模板，再调用 test_case_runs(start, case_id) 一次，保存返回的 run.id。持续调用 test_case_runs(read, run_id, offset, wait_seconds=10)，用返回的 offset 增量读取，直到运行结束且日志全部读取完成。实时关注断言、UVM_ERROR/UVM_FATAL、异常退出和超时，发现异常及时汇报；不要因短暂无输出断言死锁。必须等待执行完毕后报告运行结果，分析退出码、关键日志、失败原因与覆盖率采集结果；退出码为零不等于验证通过。不要自行修改代码、重复启动或重试；如明确不可恢复可通过 stop 停止并说明原因。用户要求：'+(note.value.trim()||'监控运行，报告结果并给出分析。'):deleteTask?'请删除项目 '+project.path+' 中的指定用例：'+caseContext+'。先 test_cases(inspect)，再 begin_modify(ids,action=delete) 登记，核对并清理用例专属源码，保留共享组件、RTL 和历史运行记录，最后 end_modify(ids)。运行中的用例禁止删除。不运行仿真。用户要求：'+(note.value.trim()||'删除此测试用例。'):design?'请只读分析当前项目 '+project.path+' 中的设计，不修改文件、不运行仿真、不启动其他工作流。目标实例：'+design.instance+'；模块：'+design.module+'；源码定位：'+JSON.stringify(design.source)+ '。先读取真实源码及必要的子模块，解释模块职责、端口协议、关键数据通路、状态变化及与父子模块的关系，引用具体文件和行号。不能用模块名猜测功能；无法确定的内容明确说明。用户要求：'+(note.value.trim()||'给出简洁清晰的中文设计说明。'):'请启动附加的 generate-testcase 工作流，为项目 '+project.path+' 新增一个真实可运行用例。先 test_cases(inspect)，再 apply(count)、begin_modify，随后编写实现并 update 填写中文 title、description、入口 name、源码行范围绑定和按执行顺序排列的 timeline；每步包含 description、expected、confidence、evidence，不能把推断步骤标记为已运行，最后 end_modify。运行命令放在卡片 runner，显式开启真实仿真器或 fixture 的波形开关，指定 DUT 层级，并直接输出唯一的 {wave_dir}/waves.vcd，不只创建目录；新构建产物放 output/flowcanvas/build，运行产物放 {run_dir}；共享覆盖配置使用 test_case_runs(configure)。复用已有环境，不修改 RTL，不自动运行仿真。用户要求：'+(note.value.trim()||'选择尚未充分验证的场景。');
        const result=scopedTask?await window.FlowCanvasAI.runCaseTask({text,project,testcase:design.testcase,current:!choice.checked,requirements:note.value.trim(),taskTitle:targetTask?({['target-explanation']:explanationTitle,['functional-coverage']:'分析功能覆盖率',['add-goal']:'新增验证目标',['delete-goal']:'删除验证目标'})[design.kind]||'验证目标任务':moduleTask?'生成模块概览':waveTask?'解读波形':'生成用例概览',caseView:moduleTask?{tab:'overview',module_instance:design.testcase.instance}:waveTask?{tab:'wave',run_id:design.run_id,waveform:design.waveform}:null}):await window.FlowCanvasAI.startCase({text,current:!choice.checked,project,requirements:note.value.trim(),design});
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
    for(const badge of panel.querySelectorAll('[data-case-result]')){
      const row=caseResults.get(badge.dataset.caseResult),status=runCases.has(badge.dataset.caseResult)?'running':row?.result_current?row?.test_result?.status:null;
      const label=({passed:'通过',failed:'失败',running:'运行中'})[status]||'';
      badge.hidden=!status;badge.dataset.status=status||'';badge.textContent='';badge.setAttribute('role','img');badge.setAttribute('aria-label',label);
      badge.title=label+(row?.test_result?.reason?' · '+row.test_result.reason:'');
    }
    for(const button of panel.querySelectorAll('[data-case-run]')){
      const running=runCases.has(button.dataset.caseRun)||pendingRun===button.dataset.caseRun;
      const hasRunner=!!data?.targets?.find(node=>node.id===button.dataset.caseRun)?.runner?.command?.trim();
      button.disabled=!runsLoaded||occupied||!hasRunner||button.dataset.caseUnavailable==='true';
      button.classList.toggle('is-case-running',running);
      button.setAttribute('aria-busy',String(running));
      button.title=running?'运行中':!hasRunner?'此用例尚未配置运行命令':!runsLoaded?'正在获取运行状态':occupied?'请等待当前用例运行结束':button.dataset.runLabel;
      button.setAttribute('aria-label',button.title);
    }
  }
  async function refreshRuns(){
    if(!caseMode||!project||runsChecking)return;runsChecking=true;
    try{
      const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/test-runs');
      if(!response.ok)return;
      const rows=await response.json();caseResults=new Map();for(const row of rows){if(!caseResults.has(row.case_id))caseResults.set(row.case_id,row);}runCases=new Set(rows.filter(row=>['starting','running'].includes(row.status)).map(row=>row.case_id));runsLoaded=true;paintRunButtons();
    }catch{}finally{runsChecking=false;}
  }
  if(caseMode){
    setInterval(()=>{if(active&&!document.hidden)refreshRuns();},1000);
    document.addEventListener('flowcanvas-test-run',event=>{if(event.detail.project?.id!==project?.id)return;runCases.add(event.detail.run.case_id);if(active){selectedTarget=event.detail.run.case_id;const node=data?.targets.find(node=>node.id===selectedTarget);if(node){caseDetail?.select(node,data.reports[node.id]);window.FlowCanvasCaseWorkspace.open(project,node,caseDetail.context());}}paintRunButtons();refreshRuns();});
  }
  function coverageFor(node){
    const record=data.coverage?.targets?.[node.id];
    const rows=record?.observations||[];
    return {status:record?.status||'no_data',rows,covered:rows.reduce((n,r)=>n+r.covered,0),total:rows.reduce((n,r)=>n+r.total,0)};
  }
  const goalInspectors=new Map(),goalCollapsed=new Set();
  /** Keep one target inspector per tab; only changed target data replaces its detail content. */
  function goalDetail(node,activate=true){
    const card={...node,title:node.name,entity_type:'target'},report=data.reports[node.id]||{status:'planned',bindings:[]},ids=new Set([node.id]);let count;
    do{count=ids.size;for(const item of data.targets)if(ids.has(item.parent))ids.add(item.id);}while(ids.size!==count);
    const members=data.targets.filter(item=>ids.has(item.id)),signature=JSON.stringify([node,report,members,data.coverage?.revision]);let inspector=goalInspectors.get(node.id);
    if(!inspector){const host=el('section','testcase-detail-pane goal-detail-pane');host.dataset.entityId=node.id;host.ariaLabel='验证目标详情';window.FlowCanvasCaseWorkspace.mount(host);inspector={host,signature:'',tab:'overview',binding:null,generation:0};goalInspectors.set(node.id,inspector);}
    if(inspector.signature!==signature){
      inspector.signature=signature;const token=++inspector.generation,title=el('header','case-detail-title'),name=el('strong','',node.name),path=[];let ancestor=node;while(ancestor){path.unshift(ancestor.name);ancestor=data.targets.find(item=>item.id===ancestor.parent);}title.append(name,el('code','',path.join(' / ')));
      const tabs=el('nav','case-detail-tabs');tabs.setAttribute('role','tablist');tabs.ariaLabel='验证目标详情';const overview=el('div','case-detail-body'),sourceBody=el('div','case-detail-body goal-source-body');
      const section=label=>{const part=window.FlowCanvasDetailSection(inspector.host,label,node.id+':'+label);overview.append(part);return part;};
      const explanation=node.explanation||{},testpoint=node.kind==='testpoint';
      const textSection=(label,text,placeholder)=>{const part=section(label);part.append(el('p',text?'':'case-detail-empty',text||placeholder));return part;};
      const introduction=textSection(testpoint?'验证意图':'功能说明',explanation.summary||node.description,testpoint?'验证意图待补充':'功能说明待补充');
      const generate=el('div','case-overview-actions');generate.append(window.FlowCanvasCaseTaskButton('target-explanation',testpoint?'生成测试点说明':'生成功能点说明',{project,testcase:card,kind:'target-explanation'}));generate.className='detail-overview-toolbar';overview.prepend(generate);
      if(testpoint){textSection('触发条件',explanation.conditions,'触发条件与采样前提待补充');textSection('预期结果',explanation.expected,'预期行为与判定标准待补充');}else textSection('验证思路',explanation.approach,'正常、边界与异常场景的验证思路待补充');
      if(report.explanation_status==='stale')overview.prepend(el('p','goal-explanation-stale','说明引用的源码已变化，请重新生成并核对。'));
      const children=data.targets.filter(item=>item.parent===node.id&&item.lifecycle!=='draft');
      if(!testpoint){const part=section('测试点分解');if(children.length){const list=el('ul','module-child-list');for(const child of children){const item=el('li');item.append(button(child.name,()=>{selectedTarget=child.id;goalDetail(child);renderGoals();},'goal-detail-child'),el('p','goal-child-summary',child.explanation?.summary||child.description||'此测试点的验证意图待补充'));list.append(item);}part.append(list);}else part.append(el('p','case-detail-empty','当前功能点还没有拆分测试点'));}
      /** Cite concrete implementation with the same source viewer used by testcase explanations. */
      const reference=(container,item)=>{const label=item.file+':'+item.line+(item.end_line&&item.end_line!==item.line?'-'+item.end_line:'');const link=button(label,async()=>{try{const {openLocalSource}=await import('./markdown-renderer.js');await openLocalSource({path:project.path+'/'+item.file,line:item.line,end:item.end_line});}catch(error){link.title=error.message;}},'case-story-source');link.title='查看当前源码 · '+label;container.append(link);};
      const implementation=section('实现与检查');
      implementation.append(el('p','goal-check-kind',({unknown:'检查方式待确认',coverage_only:'当前仅有覆盖采样，尚未找到独立正确性检查。',assertion:'检查方式：断言',comparison:'检查方式：数据比较或记分板',mixed:'检查方式：断言与比较逻辑结合'})[explanation.check_kind||'unknown']));
      if(explanation.implementation?.length){const list=el('dl','module-explanation-list');for(const step of explanation.implementation){const heading=el('dt','',step.title),body=el('dd');body.append(el('p','',step.confidence==='inferred'&&!step.text.includes('推断')?'推断：'+step.text:step.text));if(step.assignments?.length){const assignments=el('div','case-story-assignments');assignments.append(el('div','case-story-label','关键实现'));for(const assignment of step.assignments){const row=el('div','case-story-assignment');row.append(el('code','',assignment.expression),el('span','',assignment.meaning));reference(row,assignment);assignments.append(row);}body.append(assignments);}for(const evidence of step.evidence||[]){if(step.assignments?.some(item=>item.file===evidence.file&&item.line===evidence.line))continue;const citation=el('div','case-story-evidence');if(evidence.label)citation.append(el('span','',evidence.label+' · '));reference(citation,evidence);body.append(citation);}list.append(heading,body);}implementation.append(list);}else implementation.append(el('p','case-detail-empty','激励、采样条件和检查逻辑待结合源码补充'));
      const related=section('关联测试用例');
      if(explanation.related_cases?.length){const list=el('ul','module-child-list'),links=[];for(const relation of explanation.related_cases){const item=el('li'),link=button('正在读取用例…',()=>document.dispatchEvent(new CustomEvent('flowcanvas-case-restore',{detail:{project_id:project.id,case_id:relation.case_id}})),'goal-detail-child');link.disabled=true;item.append(link,el('p','goal-child-summary',relation.reason));list.append(item);links.push([relation,link]);}related.append(list);fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/testcases').then(async response=>{if(!response.ok)throw Error('读取失败');return response.json();}).then(result=>{if(token!==inspector.generation)return;for(const [relation,link] of links){const testcase=result.targets.find(item=>item.id===relation.case_id);link.textContent=testcase?(testcase.title||testcase.name):'关联用例已删除';link.disabled=!testcase;}}).catch(()=>{if(token===inspector.generation)for(const [,link] of links)link.textContent='关联用例暂时无法读取';});}else related.append(el('p','case-detail-empty','尚未建立有源码依据的测试用例关联'));
      const sources=section('源码依据');if(!report.bindings.length)sources.append(el('p','case-detail-empty','尚无源码绑定'));for(const binding of report.bindings){const link=button(binding.file+(binding.line?':'+binding.line+'–'+binding.end_line:''),()=>{inspector.tab='source';inspector.binding=binding.id;activateTab();},'case-story-source');link.disabled=!binding.line;const row=el('div','goal-source-reference');row.append(link,el('span','',labels[binding.status]||binding.status));sources.append(row);}
      const coverage=section('覆盖情况'),record=coverageFor(node);if(explanation.coverage_notes)coverage.append(el('p','goal-coverage-notes',explanation.coverage_notes));coverage.append(el('p','',record.status==='conflict'?'同一定义对应多个运行实例，需核对绑定':record.status==='stale'?'源码已变化，覆盖率数据待更新':record.total?record.covered+' / '+record.total+' 个 bin 达标（'+(100*record.covered/record.total).toFixed(1)+'%）':'当前目标暂无覆盖率数据'));
      const rows=[];for(const member of members){const c=coverageFor(member);if(c.status!=='available')continue;for(const observation of c.rows){if(observation.bins?.length){for(const bin of observation.bins)rows.push([member.name,observation.source_path||'—',bin.name,bin.hits,bin.goal,bin.hits>=bin.goal?'已达标':'未达标']);}else rows.push([member.name,observation.source_path||'—','原生汇总','—','—',observation.covered+' / '+observation.total]);}}
      if(rows.length){const scroll=el('div','goal-bin-table'),table=el('table','module-port-table'),head=el('thead'),heading=el('tr'),body=el('tbody');for(const label of ['目标','覆盖点','bin','命中','目标次数','状态'])heading.append(el('th','',label));head.append(heading);for(const values of rows){const row=el('tr');for(const value of values)row.append(el('td','',String(value)));body.append(row);}table.append(head,body);scroll.append(table);coverage.append(scroll);}
      inspector.host.replaceChildren(title,tabs,overview,sourceBody);
      const buttons={};let sourcesLoaded=false;const sourceSections=new Map();
      /** Expand all source bindings as document sections, without a source-selection dropdown. */
      function activateTab(){
        overview.hidden=inspector.tab!=='overview';sourceBody.hidden=inspector.tab!=='source';for(const [key,b] of Object.entries(buttons)){b.setAttribute('aria-selected',String(key===inspector.tab));b.tabIndex=key===inspector.tab?0:-1;}
        if(inspector.tab==='source'){
          if(!report.bindings.some(item=>item.id===inspector.binding))inspector.binding=report.bindings[0]?.id||null;
          if(!sourcesLoaded){
            sourcesLoaded=true;sourceBody.replaceChildren();
            if(!report.bindings.length)sourceBody.append(el('p','case-detail-empty','尚无源码绑定'));
            for(const binding of report.bindings){
              const part=window.FlowCanvasDetailSection(inspector.host,({definition:'目标定义',stimulus:'激励与约束',check:'检查逻辑',coverage:'覆盖采样',configuration:'环境与配置',rtl:'RTL 实现'})[binding.role]||'源码',node.id+':source:'+binding.id),pre=el('pre','case-detail-source');
              part.querySelector('summary').append(el('code','',binding.file+(binding.line?':'+binding.line:'')));pre.textContent='正在读取…';pre.tabIndex=0;pre.ariaLabel='验证目标源码';part.append(pre);sourceBody.append(part);sourceSections.set(binding.id,part);
              api({operation:'inspect',binding_id:binding.id}).then(result=>{
                if(token!==inspector.generation)return;const source=result.source;pre.replaceChildren();
                source.file_content.split('\n').forEach((text,index)=>{const row=el('div',index+1>=source.line&&index+1<=source.end_line?'is-bound':'');row.dataset.line=index+1;row.append(el('span','',String(index+1)),el('code','',text||' '));pre.append(row);});
              }).catch(error=>{if(token===inspector.generation)pre.textContent=error.message;});
            }
          }
          const selected=sourceSections.get(inspector.binding);if(selected)selected.open=true;
        }
        document.dispatchEvent(new CustomEvent('flowcanvas-case-view',{detail:{case_id:node.id,view:{tab:inspector.tab,...(inspector.tab==='source'&&inspector.binding?{binding_id:inspector.binding}:{})}}}));
      }
      for(const [key,label] of [['overview','详情'],['source','源码']]){const b=button(label,()=>{inspector.tab=key;activateTab();});b.setAttribute('role','tab');b.id=node.id+'-'+key;const detailPanel=key==='overview'?overview:sourceBody;detailPanel.id=b.id+'-panel';detailPanel.setAttribute('role','tabpanel');detailPanel.setAttribute('aria-labelledby',b.id);b.setAttribute('aria-controls',detailPanel.id);b.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();buttons[event.key==='Home'?'overview':event.key==='End'?'source':inspector.tab==='overview'?'source':'overview'].click();buttons[inspector.tab].focus();}};tabs.append(b);buttons[key]=b;}inspector.activate=activateTab;activateTab();
    }
    if(activate){let parent=node.parent;while(parent){goalCollapsed.delete(parent);parent=data.targets.find(item=>item.id===parent)?.parent;}window.FlowCanvasCaseWorkspace.open(project,card,{tab:inspector.tab,...(inspector.tab==='source'&&inspector.binding?{binding_id:inspector.binding}:{})});}
    return inspector;
  }
  /** Mirror the design hierarchy with compact title rows; details remain in editor tabs. */
  function renderGoals(){
    const list=panel.querySelector('.targets-tree'),summary=panel.querySelector('.targets-summary');list.replaceChildren();summary.replaceChildren(el('span','',data.targets.filter(node=>node.lifecycle!=='draft').length+' 个验证目标'));list.classList.add('verification-title-list');list.setAttribute('role','tree');list.ariaLabel='验证目标层级';
    const visible=new Set();for(const node of data.targets){if(node.lifecycle==='draft')continue;if(!query||[node.name,node.description,...node.bindings.map(b=>b.file)].join(' ').toLowerCase().includes(query)){let parent=node;while(parent){visible.add(parent.id);parent=data.targets.find(item=>item.id===parent.parent);}}}
    if(query&&searchExpanded){for(const id of visible)goalCollapsed.delete(id);searchExpanded=false;}
    const branches=data.targets.filter(node=>node.lifecycle!=='draft'&&data.targets.some(child=>child.parent===node.id&&child.lifecycle!=='draft'));
    const allExpanded=branches.length>0&&branches.every(node=>!goalCollapsed.has(node.id));
    const toggleAll=button(allExpanded?'全部折叠':'全部展开',()=>{searchExpanded=false;for(const node of branches){if(allExpanded)goalCollapsed.add(node.id);else goalCollapsed.delete(node.id);}renderGoals();summary.querySelector('.targets-toggle-all')?.focus();},'targets-toggle-all');
    toggleAll.disabled=!branches.length;toggleAll.title=toggleAll.textContent;toggleAll.setAttribute('aria-label',toggleAll.textContent);summary.append(toggleAll);
    const ordered=[];const collect=parent=>{for(const node of data.targets.filter(item=>item.parent===parent&&visible.has(item.id))){ordered.push(node);if(!goalCollapsed.has(node.id))collect(node.id);}};collect(null);
    /** Render plain hierarchical list items without inheriting legacy card surfaces. */
    const appendItems=(parent,container,depth=0)=>{for(const node of data.targets.filter(item=>item.parent===parent&&visible.has(item.id))){
      const children=data.targets.filter(item=>item.parent===node.id&&visible.has(item.id)),expanded=!goalCollapsed.has(node.id),wrapper=el('div','verification-item verification-'+node.kind),header=el('div','target-header'),heading=el('div','goal-row-heading');wrapper.dataset.targetId=node.id;
      const pending=node.lifecycle&&node.lifecycle!=='ready';
      let inheritedDeletion=false;
      if(node.lifecycle==='deleting'){let ancestor=data.targets.find(item=>item.id===node.parent);while(ancestor){if(ancestor.lifecycle==='deleting'){inheritedDeletion=true;break;}ancestor=data.targets.find(item=>item.id===ancestor.parent);}}
      if(pending){wrapper.dataset.lifecycle=node.lifecycle;wrapper.classList.add('case-in-progress');header.setAttribute('aria-busy',String(node.operation_status==='running'));}
      if(pending&&node.operation_status==='running'&&!inheritedDeletion){
        if(!operationAnimationStart.has(node.id))operationAnimationStart.set(node.id,performance.now());
        wrapper.classList.add('case-operation-active');wrapper.style.setProperty('--operation-phase',(-((performance.now()-operationAnimationStart.get(node.id))%2600))+'ms');
      }else operationAnimationStart.delete(node.id);
      const focus=id=>list.querySelector('.target-card-head[data-target-id="'+id+'"]')?.focus();
      const open=()=>{if(node.kind==='group'){if(children.length)toggle();return;}selectedTarget=node.id;goalDetail(node);for(const item of list.querySelectorAll('.target-card-head')){const selected=item.dataset.targetId===node.id;item.setAttribute('aria-selected',String(selected));item.closest('.target-header').classList.toggle('is-selected',selected);}};
      const head=button('',event=>{if(node.kind==='group'&&event.detail>1)return;open();},'target-card-head');head.dataset.targetId=node.id;head.setAttribute('role','treeitem');head.setAttribute('aria-level',String(depth+1));const selected=window.FlowCanvasCaseWorkspace.context()?.testcase.id===node.id;head.setAttribute('aria-selected',String(selected));header.classList.toggle('is-selected',selected);if(children.length)head.setAttribute('aria-expanded',String(expanded));head.title=node.kind==='group'?'点击展开或收起功能组':'单击预览，双击固定标签';
      const toggle=()=>{if(expanded)goalCollapsed.add(node.id);else goalCollapsed.delete(node.id);renderGoals();focus(node.id);};
      const chevron=el('span','target-chevron'+(expanded&&children.length?' is-expanded':''));
      if(children.length){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 16 16');svg.setAttribute('aria-hidden','true');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','m6 4 4 4-4 4');svg.append(path);chevron.append(svg);chevron.title=(expanded?'折叠':'展开')+'子目标';chevron.onclick=event=>{event.stopPropagation();if(event.detail<2)toggle();};chevron.ondblclick=event=>event.stopPropagation();}
      const title=el('span','target-title',node.name);title.dataset.textField='name';head.append(chevron,title);if(pending&&!inheritedDeletion)head.append(el('span','case-operation-label',({creating:'创建',updating:'修改',deleting:'删除'})[node.lifecycle]+(node.operation_status==='running'?'中':'已暂停')));head.ondblclick=event=>{if(node.kind==='group'){event.preventDefault();return;}window.FlowCanvasCaseWorkspace.pin(project.id,node.id);};
      head.onkeydown=event=>{const index=ordered.indexOf(node);if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?ordered[0]:event.key==='End'?ordered.at(-1):ordered[Math.max(0,Math.min(ordered.length-1,index+(event.key==='ArrowDown'?1:-1)))];if(next)focus(next.id);}else if(event.key==='ArrowRight'){event.preventDefault();if(children.length&&!expanded)toggle();else if(children.length)focus(children[0].id);}else if(event.key==='ArrowLeft'){event.preventDefault();if(children.length&&expanded)toggle();else if(node.parent)focus(node.parent);}else if(event.key==='F2'&&node.lifecycle==='ready'){event.preventDefault();editCaseText(node,'name',title);}};
      header.onclick=event=>{if(!event.target.closest('button'))head.click();};heading.append(head);header.append(heading);
      if(!node.lifecycle||node.lifecycle==='ready'){const controls=el('div','target-actions target-header-actions');if(node.kind!=='testpoint')controls.append(goalAction(node,'add-goal','AI 新增'+(node.kind==='group'?'功能点':'测试点'),'M10 5v14M3 12h14'));controls.append(goalAction(node,'delete-goal','AI 删除验证目标','M4 7h12M6 7v13h9V7M8 7V4h5M9 10v7m3-7v7'));header.append(controls);}
      if(['group','point','testpoint'].includes(node.kind)){
        const coverage=coverageFor(node),valid=coverage.status==='available'&&coverage.total>0,score=el('div','target-coverage-score'+(valid?' has-data':'')),meter=el('span','goal-coverage-meter');
        const percent=valid?Math.min(100,100*coverage.covered/coverage.total):0;
        score.append(el('span','goal-coverage-percent',valid?percent.toFixed(1)+'%':coverage.status==='stale'?'待更新':coverage.status==='conflict'?'冲突':'—'));
        score.title=valid?coverage.covered+' / '+coverage.total+' 个 bin 达标；覆盖命中不代表正确性检查通过':coverage.status==='stale'?'源码已变化，请更新覆盖率':coverage.status==='conflict'?'覆盖率实例映射冲突':'尚无实际覆盖率数据';
        meter.style.setProperty('--coverage',percent+'%');if(valid){meter.setAttribute('role','progressbar');meter.setAttribute('aria-label',(kinds[node.kind]?.[1]||'验证目标')+'覆盖率');meter.setAttribute('aria-valuemin','0');meter.setAttribute('aria-valuemax','100');meter.setAttribute('aria-valuenow',String(percent));}else meter.setAttribute('aria-hidden','true');score.append(meter);header.append(score);
      }
      wrapper.append(header);paintTextHighlights(wrapper,node);container.append(wrapper);
      if(children.length&&expanded){const body=el('div','verification-children');body.setAttribute('role','group');appendItems(node.id,body,depth+1);wrapper.append(body);}
    }};appendItems(null,list);
    list.classList.toggle('verification-title-list',ordered.length>0);
    if(!ordered.length){
      list.removeAttribute('role');list.removeAttribute('aria-label');
      const empty=el('div','targets-empty');
      empty.append(el('strong','',query?'没有匹配的'+view:'从'+view+'开始'),el('p','targets-muted',query?'试试其他关键词。':'规划整个项目的验证目标，或导入已有验证代码。单独新增请点击右上角的 ＋。'));
      if(!query){const actions=el('div','targets-empty-ai-actions');for(const [kind,label,path] of [['plan-goals','规划验证目标','M4 4h12v16H4zM7 8h6M7 12h6M7 16h4'],['import-goals','同步验证目标','M10 3v12m-4-4 4 4 4-4M3 17v4h14v-4']]){const action=goalAction(null,kind,label,path);action.classList.add('targets-empty-ai-button');action.append(el('span','',label));actions.append(action);}empty.append(actions);}
      list.append(empty);
    }
    for(const [id,inspector] of goalInspectors){const node=data.targets.find(item=>item.id===id);if(node)goalDetail(node,false);else{inspector.generation++;inspector.host.remove();goalInspectors.delete(id);}}
    const current=window.FlowCanvasCaseWorkspace.context();if(current?.testcase.entity_type==='target'&&!data.targets.some(node=>node.id===current.testcase.id))document.dispatchEvent(new CustomEvent('flowcanvas-case-ordinary'));
    window.FlowCanvasCaseWorkspace.reconcile(project,data.targets.map(node=>({...node,title:node.name,entity_type:'target'})),'target');
    const diagnostics=panel.querySelector('.targets-diagnostics');diagnostics.replaceChildren();for(const [file,error] of Object.entries(data.errors)){const detail=el('details');detail.append(el('summary','',file+' · 无法校验'),el('pre','',error));diagnostics.append(detail);}
  }
  const bugInspectors=new Map();
  /** Present a minimal issue record in its own editor tab and keep demo provenance visible. */
  function bugDetail(node,activate=true){
    let inspector=bugInspectors.get(node.id);const signature=JSON.stringify([node,data.reports[node.id]]);
    if(!inspector){const host=el('section','testcase-detail-pane bug-detail-pane');host.dataset.entityId=node.id;host.ariaLabel='Bug 详情';window.FlowCanvasCaseWorkspace.mount(host);inspector={host,signature:''};bugInspectors.set(node.id,inspector);}
    if(inspector.signature!==signature){
      inspector.signature=signature;const title=el('header','case-detail-title');title.append(el('strong','',node.name));
      const body=el('div','case-detail-body'),section=window.FlowCanvasDetailSection(inspector.host,'描述');let description=node.description||'';
      if(node.example&&!description.includes('演示'))description='演示记录，未实际复现，不代表真实缺陷。'+(description?'\n\n'+description:'');
      section.append(el('p',description?'':'case-detail-empty',description||'描述待补充'));body.append(section);
      inspector.host.replaceChildren(title,body);
    }
    if(activate)window.FlowCanvasCaseWorkspace.open(project,{...node,title:node.name,entity_type:'bug'},{tab:'overview'});
  }
  /** Match the other empty pages with a compact outlined icon-and-label action. */
  function bugEmptyActions(){
    const actions=el('div','targets-empty-ai-actions'),action=smallAction('创建 Bug','M12 5v14M5 12h14',()=>edit());
    action.classList.add('targets-empty-ai-button');action.append(el('span','','创建 Bug'));actions.append(action);return actions;
  }
  /** Reuse testcase-style rows instead of expanding bug cards into the navigation column. */
  function renderBugs(){
    const list=panel.querySelector('.targets-tree'),summary=panel.querySelector('.targets-summary');list.replaceChildren();list.classList.add('bug-title-list');list.setAttribute('role','listbox');list.ariaLabel='Bug 列表';const examples=data.targets.filter(node=>node.example).length;
    summary.replaceChildren(el('span','',(data.targets.length-examples)+' 条真实记录'),el('span','bug-demo-count',examples+' 条演示'));
    const rows=data.targets.filter(node=>[node.name,node.description,node.condition,node.expected,node.actual,bugStatuses[node.status],severities[node.severity],node.example?'演示':''].join(' ').toLowerCase().includes(query));
    rows.forEach((node,index)=>{const wrapper=el('div','bug-list-item'),head=button('',()=>{selectedTarget=node.id;bugDetail(node);for(const row of list.querySelectorAll('.bug-list-row'))row.setAttribute('aria-selected',String(row.dataset.bugId===node.id));},'bug-list-row');head.dataset.bugId=node.id;head.setAttribute('role','option');head.setAttribute('aria-selected',String(window.FlowCanvasCaseWorkspace.context()?.testcase.id===node.id));head.title=node.name+' · 单击预览，双击固定';head.append(el('span','bug-list-name',node.name));if(node.example)head.append(el('span','bug-demo-label','演示'));head.ondblclick=()=>window.FlowCanvasCaseWorkspace.pin(project.id,node.id);head.onkeydown=event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();list.querySelectorAll('.bug-list-row')[Math.max(0,Math.min(rows.length-1,index+(event.key==='ArrowDown'?1:-1)))]?.focus();}};
      const editButton=smallAction('编辑 Bug','M12 4l4 4M4 16l4-1L17 6a2 2 0 0 0-3-3L5 12Z',()=>edit(node));editButton.classList.add('bug-row-edit');wrapper.append(head,editButton);list.append(wrapper);
    });
    if(!rows.length){const empty=el('div','targets-empty');empty.append(el('strong','',query?'没有匹配的 Bug':'从 Bug 管理开始'),el('p','targets-muted',query?'试试其他关键词。':'记录问题、复现条件和相关证据。'));if(!query)empty.append(bugEmptyActions());list.append(empty);}
    for(const [id,inspector] of bugInspectors){const node=data.targets.find(item=>item.id===id);if(node)bugDetail(node,false);else{inspector.host.remove();bugInspectors.delete(id);}}
    const current=window.FlowCanvasCaseWorkspace.context();if(current?.testcase.entity_type==='bug'&&!data.targets.some(node=>node.id===current.testcase.id))document.dispatchEvent(new CustomEvent('flowcanvas-case-ordinary'));
    window.FlowCanvasCaseWorkspace.reconcile(project,data.targets.map(node=>({...node,title:node.name,entity_type:'bug'})),'bug');panel.querySelector('.targets-diagnostics').replaceChildren();
  }
  function render(){
    if(inlineEditing)return;
    if(bugMode){renderBugs();return;}
    if(!caseMode&&!bugMode){renderCoverageSummary();renderGoals();return;}
    const list=panel.querySelector('.targets-tree'),summary=panel.querySelector('.targets-summary');list.replaceChildren();summary.replaceChildren();
    for(const [kind,[,label]]of Object.entries(kinds))summary.append(el('span','',data.targets.filter(n=>n.kind===kind).length+' '+label));

    const allExpanded=data.targets.length>0&&data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));
    const toggleAll=button(allExpanded?'全部折叠':'全部展开',()=>{const expanded=data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));if(expanded){opened.clear();searchExpanded=false;}else{for(const node of data.targets)opened.add(node.id);}render();},'targets-toggle-all');
    toggleAll.disabled=!data.targets.length;toggleAll.title=toggleAll.textContent;toggleAll.setAttribute('aria-label',toggleAll.textContent);if(!caseMode)summary.append(toggleAll);
    const byId=new Map(data.targets.map(n=>[n.id,n]));const visible=new Set();
    for(const n of data.targets){if(!query||[n.title,n.name,n.description,n.id,...n.bindings.map(b=>b.file)].join(' ').toLowerCase().includes(query)){let current=n;while(current){visible.add(current.id);current=byId.get(current.parent);}}}
    function card(node,depth){
      const operationLocked=!bugMode&&!!node.lifecycle&&node.lifecycle!=='ready';
      let inheritedDeletion=false;
      if(node.lifecycle==='deleting'){let ancestor=byId.get(node.parent);while(ancestor){if(ancestor.lifecycle==='deleting'){inheritedDeletion=true;break;}ancestor=byId.get(ancestor.parent);}}
      const report=data.reports[node.id]||{status:'planned',bindings:[]};const children=data.targets.filter(n=>n.lifecycle!=='draft'&&n.parent===node.id&&visible.has(n.id));
      const wrapper=el('article','target-card target-'+node.kind);wrapper.dataset.targetId=node.id;if(node.operation_status==='running'&&node.lifecycle&&node.lifecycle!=='ready'){if(!operationAnimationStart.has(node.id))operationAnimationStart.set(node.id,performance.now());wrapper.style.setProperty('--operation-phase',(-((performance.now()-operationAnimationStart.get(node.id))%2600))+'ms');}else operationAnimationStart.delete(node.id);if(!bugMode&&node.lifecycle&&node.lifecycle!=='ready'){wrapper.dataset.lifecycle=node.lifecycle;wrapper.classList.add('case-in-progress');wrapper.classList.toggle('case-operation-active',node.operation_status==='running'&&!inheritedDeletion);}const head=button('',()=>{
        if(inlineEditing)return;
        selectedTarget=node.id;
        if(caseMode){for(const h of panel.querySelectorAll('.target-header.is-selected'))h.classList.remove('is-selected');header.classList.add('is-selected');caseDetail?.select(node,data.reports[node.id]);window.FlowCanvasCaseWorkspace.open(project,node,caseDetail.context());return;}
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
      if(!caseMode&&!bugMode)head.append(el('span','target-kind',kinds[node.kind][0]));
      const title=el('span','target-title',caseMode?(node.title||node.name):node.name);title.dataset.textField=caseMode&&node.title?'title':'name';head.append(title);
      if(caseMode){const badge=el('span','case-result-badge');badge.dataset.caseResult=node.id;badge.hidden=true;head.prepend(badge);}
      if(caseMode){
        head.title=node.kind==='group'?'点击展开或收起功能组':'单击预览，双击固定标签';
        head.ondblclick=event=>{event.preventDefault();event.stopPropagation();window.FlowCanvasCaseWorkspace.pin(project.id,node.id);};
      }else if(bugMode||!node.lifecycle||node.lifecycle==='ready'){
        title.onclick=event=>{event.stopPropagation();if(event.detail<=1)head.click();};
        title.ondblclick=event=>{event.preventDefault();event.stopPropagation();if(node.lifecycle!=='deleting')editCaseText(node,'name',title);};
      }
      if(!caseMode&&!bugMode){
        const c=coverageFor(node);
        const text=c.status==='conflict'?'多实例冲突':c.status==='stale'?'数据已过期':c.total?((c.covered/c.total*100).toFixed(1)+'% · '+c.covered+'/'+c.total):'—';
        const badge=el('span','target-coverage-score'+(c.total&&c.status!=='stale'?' has-data':''),text);
        badge.title=c.status==='conflict'?'同一定义对应多个运行实例，请拆分后重新导入':c.status==='stale'?'源码与采集时不同，请重新构建并开始新累计':'该目标唯一实例的累计 bin 命中，不代表正确性检查通过';
        if(c.status==='available'&&c.total){
          badge.title=c.covered+' / '+c.total+' 个 bin 达标 · '+badge.title;
          badge.replaceChildren(el('span','goal-coverage-percent',(100*c.covered/c.total).toFixed(1)+'%'),el('span','goal-coverage-count',c.covered+'/'+c.total));
          const meter=el('span','goal-coverage-meter');meter.setAttribute('aria-hidden','true');meter.style.setProperty('--coverage',Math.min(100,100*c.covered/c.total)+'%');badge.append(meter);
        }
        head.append(badge);
      }
      if(!bugMode&&node.lifecycle&&node.lifecycle!=='ready'&&!inheritedDeletion){const label=(node.lifecycle==='creating'?'创建':node.lifecycle==='updating'?'修改':'删除')+(node.operation_status==='running'?'中':'已暂停');head.append(el('span','case-operation-label',label));}
      if(bugMode){}else if(report.status==='planned'&&(!node.lifecycle||node.lifecycle==='ready'))head.append(el('span','target-state state-planned','待实现'));
      else if(report.status!=='bound'&&!operationLocked){
        const warning=el('span','target-warning state-'+report.status);
        const message=[labels[report.status],...report.bindings.filter(b=>b.status!=='bound').map(b=>b.message|| (b.status==='changed'?'源码已变化，请核对定义':''))].filter(Boolean);
        warning.title=[...new Set(message)].join(' · ');warning.setAttribute('role','img');warning.setAttribute('aria-label',warning.title);
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
        const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d','M10.3 4.3 2.7 17.5A2 2 0 0 0 4.4 20h15.2a2 2 0 0 0 1.7-2.5L13.7 4.3a2 2 0 0 0-3.4 0ZM12 9v4m0 3h.01');svg.append(path);warning.append(svg);head.append(warning);
      }
      const header=el('div','target-header'+(selectedTarget===node.id?' is-selected':''));
      if(caseMode||bugMode)head.title=caseMode?[node.title,node.name].filter(Boolean).join(' · '):node.name;
      head.onkeydown=event=>{
        if((bugMode||!node.lifecycle||node.lifecycle==='ready')&&event.key==='F2'){event.preventDefault();if(node.lifecycle!=='deleting')editCaseText(node,caseMode?'title':'name',title);return;}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){
          event.preventDefault();const rows=[...panel.querySelectorAll('.target-card-head')],index=rows.indexOf(head);
          rows[Math.max(0,Math.min(rows.length-1,index+(event.key==='ArrowDown'?1:-1)))]?.focus();
        }else if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
          event.preventDefault();if((event.key==='ArrowRight')!==(head.getAttribute('aria-expanded')==='true'))head.click();
        }
      };
      header.onclick=event=>{if(!event.target.closest('button'))head.click();};
      const controls=el('div','target-actions target-header-actions');if(!caseMode&&!bugMode&&(!node.lifecycle||node.lifecycle==='ready')){if(node.kind!=='testpoint')controls.append(goalAction(node,'add-goal','AI 新增'+(node.kind==='group'?'功能点':'测试点'),'M10 5v14M3 12h14'));controls.append(goalAction(node,'delete-goal','AI 删除验证目标','M4 7h12M6 7v13h9V7M8 7V4h5M9 10v7m3-7v7'));}
      if(caseMode&&(!node.lifecycle||node.lifecycle==='ready')){
        const run=smallAction('运行测试用例','m8 5 11 7-11 7Z',async()=>{if(pendingRun!==null||runCases.size)return;pendingRun=node.id;paintRunButtons();try{
          const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/test-runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case_id:node.id})});
          const result=await response.json();if(!response.ok)throw Error(result.detail||'启动失败');
          document.dispatchEvent(new CustomEvent('flowcanvas-test-run',{detail:{project,run:result}}));
        }catch(e){await refreshRuns();if(!runCases.size)fail(e);}finally{pendingRun=null;paintRunButtons();}});
        run.dataset.caseRun=node.id;run.dataset.caseUnavailable=String(!!node.lifecycle&&node.lifecycle!=='ready');run.dataset.runLabel='运行测试用例';controls.append(run);
        for(const [kind,label,path] of [['delete-testcase','AI 删除测试用例','M4 7h12M6 7v13h9V7M8 7V4h5M9 10v7m3-7v7']]){
          const context={kind,instance:node.name,module:node.title||node.name,testcase:{id:node.id,title:node.title,name:node.name,bindings:node.bindings}};
          const action=smallAction(label,path,()=>{});action.classList.add('case-ai-action');
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
      if(!caseMode&&!bugMode){
        const attach=smallAction('附加验证目标到对话','m7 11 5-5a2.1 2.1 0 0 1 3 3l-7 7a3.5 3.5 0 0 1-5-5l7-7',()=>{
          document.dispatchEvent(new CustomEvent('flowcanvas-attach-target',{detail:{
            project:{id:project.id,path:project.path},target:{id:node.id,name:node.name,kind:node.kind,
              parent:node.parent,description:node.description,bindings:report.bindings,lifecycle:node.lifecycle,coverage:coverageFor(node)}
          }}));
        });
        attach.classList.add('goal-attach-action');controls.prepend(attach);
      }
      const links=el('div','target-title-sources');
      for(const binding of (caseMode||bugMode?report.bindings:[])){
        const label=binding.file.split('/').at(-1)+(binding.line?':'+binding.line+'–'+binding.end_line:'');
        const b=button(label,()=>{if(!operationLocked)source(binding);},'target-source target-title-source');b.disabled=operationLocked||!binding.line;
        b.title=binding.file+(binding.message?' · '+binding.message:' · 查看源码');b.setAttribute('aria-label','查看源码 '+binding.file+(binding.line?' 第 '+binding.line+' 行':''));links.append(b);
      }
      if(!caseMode&&!bugMode){
        const c=coverageFor(node);
        for(const binding of report.bindings){
          const observation=c.status==='available'?c.rows.find(row=>row.binding_id===binding.id):null;
          const path=observation?.source_path||[binding.scope,binding.symbol].filter(Boolean).join('.')||binding.file;
          const link=button(path,()=>{if(!operationLocked)source(binding);},'target-source target-title-source');
          link.disabled=operationLocked||!binding.line;link.title=path;link.setAttribute('aria-label','查看 '+path+' 的源码');links.append(link);
        }
        const score=head.querySelector('.target-coverage-score'),heading=el('div','goal-row-heading');
        heading.append(head,links);header.append(heading,controls,score);
      }else header.append(head,links,controls);
      wrapper.append(header);queueMicrotask(paintRunButtons);
      if(!caseMode&&(opened.has(node.id)||(query&&searchExpanded))){
        const body=el('div','target-card-body');
        if(!bugMode){
          const description=el('p','case-editable-description'+(node.description?'':' is-empty'),node.description||(caseMode?'双击添加说明':''));description.dataset.textField='description';description.title='双击修改说明';description.tabIndex=0;
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
          const value=el('p','bug-editable',node.description||'');value.dataset.textField='description';value.tabIndex=0;
          value.ondblclick=event=>{event.stopPropagation();editCaseText(node,'description',value);};
          value.onkeydown=event=>{if(event.key==='Enter'||event.key==='F2'){event.preventDefault();editCaseText(node,'description',value);}};
          body.append(value);
        }

        if(!caseMode&&!bugMode){
          const c=coverageFor(node);
          if(report.bindings.length){
            const detail=el('div','target-coverage-detail');
            if(node.kind==='testpoint'&&c.status==='available'){
              for(const row of c.rows)for(const bin of row.bins){
                const item=el('div','target-coverage-bin');
                const hit=bin.hits>=bin.goal;const label=bin.goal===1?(hit?'已命中':'未命中'):bin.hits+' / '+bin.goal+' 次'+(hit?' · 已达标':' · 未达标');
                item.append(el('span','',bin.name),el('span',hit?'is-hit':'',label));
                detail.append(item);
              }
            }
            if(detail.childNodes.length)body.append(detail);
          }
        }
        const actions=el('div','target-actions');
        if(actions.children.length)body.append(actions);for(const child of children)body.append(card(child,depth+1));wrapper.append(body);
      }else if(children.length&&(caseMode||bugMode)){head.title=children.length+' 个子目标';}
      paintTextHighlights(wrapper,node);
      return wrapper;
    }
    for(const group of data.targets.filter(n=>n.lifecycle!=='draft'&&n.kind===rootKind&&visible.has(n.id)))list.append(card(group,0));
    if(!list.children.length){
      const empty=el('div','targets-empty');
      empty.append(el('strong','',query?'没有匹配的'+view:'从'+view+'开始'),el('p','targets-muted',query?'试试其他关键词。':bugMode?'记录问题、复现条件和相关证据。':caseMode?'新增测试用例，或导入项目已有用例。单独新增请点击右上角的 ＋。':'规划整个项目的验证目标，或导入已有验证代码。单独新增请点击右上角的 ＋。'));
      if(!query&&bugMode)empty.append(bugEmptyActions());
      if(!query&&!bugMode){
        const actions=el('div','targets-empty-ai-actions');
        for(const [kind,label,path] of [[caseMode?'new-testcase':'plan-goals',caseMode?'新增测试用例':'规划验证目标','M4 4h12v16H4zM7 8h6M7 12h6M7 16h4'],[caseMode?'import-testcases':'import-goals',caseMode?'同步测试用例':'同步验证目标','M10 3v12m-4-4 4 4 4-4M3 17v4h14v-4']]){
          const action=goalAction(null,kind,label,path);
          action.classList.add('targets-empty-ai-button');
          action.append(el('span','',label));
          actions.append(action);
        }
        empty.append(actions);
      }
      list.append(empty);
    }
    if(!caseMode&&!bugMode){
      let detail=functionalPane.querySelector('.functional-metric-details');
      const wasOpen=detail?.open||false;
      if(detail)detail.remove();
      const rows=[];
      for(const node of data.targets.filter(node=>visible.has(node.id)&&node.lifecycle!=='draft')){
        const record=coverageFor(node);if(record.status!=='available')continue;
        for(const observation of record.rows){
          const bins=observation.bins||[];
          if(bins.length){
            for(const bin of bins)rows.push([node.name,observation.source_path||node.name,bin.name,String(bin.hits),String(bin.goal),bin.hits>=bin.goal?'已达标':'未达标']);
          }else rows.push([node.name,observation.source_path||node.name,'仅原生汇总','—','—',observation.covered+' / '+observation.total+'（未提供 bin 明细）']);
        }
      }
      if(rows.length){
        detail=el('details','functional-metric-details');detail.open=wasOpen;
        detail.append(el('summary','','覆盖点明细（'+rows.length+'）'));
        const scroll=el('div','functional-metric-scroll'),table=el('table'),head=el('thead'),titles=el('tr'),body=el('tbody');
        for(const name of ['验证目标','覆盖点','bin','命中次数','目标次数','状态'])titles.append(el('th','',name));
        head.append(titles);
        for(const cells of rows){const row=el('tr');for(const text of cells)row.append(el('td','',text));body.append(row);}
        table.append(head,body);scroll.append(table);detail.append(scroll);functionalPane.append(detail);
      }
    }
    if(caseMode&&caseDetail){
      window.FlowCanvasCaseWorkspace.reconcile(project,data.targets);
      const selected=data.targets.find(node=>node.id===selectedTarget&&node.lifecycle!=='draft');
      if(selectedTarget&&!selected)document.dispatchEvent(new CustomEvent('flowcanvas-case-ordinary'));
      selectedTarget=selected?.id||null;
      caseDetail.select(selected,selected?data.reports[selected.id]:null);
      for(const header of panel.querySelectorAll('.target-header'))header.classList.toggle('is-selected',header.closest('[data-target-id]').dataset.targetId===selectedTarget);
    }
    const diagnostics=panel.querySelector('.targets-diagnostics');diagnostics.replaceChildren();for(const [file,error]of Object.entries(data.errors)){const d=el('details');d.append(el('summary','',file+' · 无法校验'),el('pre','',error));diagnostics.append(d);}
  }
  function show(view){
    active=view===panelView;
    if(panel){
      if(caseMode||bugMode)host.classList.toggle('has-case-workbench',view==='测试用例'||view==='Bug 管理');
      panel.hidden=!active;if(active){refresh();refreshRuns();}
    }
  }
  document.addEventListener('flowcanvas-view',e=>show(e.detail));
  // Watch published metadata only; never scan source files on a timer.
  let checking=false;
  setInterval(async()=>{if(!active||!data||busy||checking||document.hidden)return;checking=true;try{const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/'+endpoint+'/revision');if(response.ok){const next=await response.json();if(next.revision!==data.revision||(!caseMode&&!bugMode&&next.coverage_revision!==data.coverage?.revision))await refresh();}}catch{}finally{checking=false;}},!bugMode?1000:3000);
  fetch('/api/chat/projects').then(r=>r.json()).then(state=>{
    project=state.current;if(!project)return;panel=el('section','targets-panel'+(caseMode?' testcase-panel':bugMode?' bug-panel':''));panel.hidden=true;
    const toolbar=el('div','targets-toolbar');const search=el('input','targets-search');search.placeholder='筛选'+view+'…  /';search.setAttribute('aria-label','搜索'+view);search.oninput=()=>{query=search.value.trim().toLowerCase();searchExpanded=true;if(data)render();};
    search.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();search.value='';query='';if(data)render();}else if(event.key==='ArrowDown'){event.preventDefault();panel.querySelector('.bug-list-row,.verification-goal-row,.target-card-head')?.focus();}};
    document.addEventListener('keydown',event=>{if(active&&event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.target.closest('input,textarea,select,[contenteditable="true"],dialog')){event.preventDefault();search.focus();}});
    toolbar.append(search);
    if(bugMode)toolbar.append(smallAction('新增 Bug','M12 5v14M5 12h14',()=>edit()));
    if(!caseMode&&!bugMode)toolbar.append(goalAction(null,'add-goal','AI 新增验证目标','M10 5v14M3 12h14'));
    if(caseMode){const add=button('',()=>newCaseTask(add),'ai-case-add');add.title='AI 新增测试用例';add.setAttribute('aria-label','AI 新增测试用例');add.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="case-ai-spark" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#4285ff"/><stop offset=".55" stop-color="#9164ec"/><stop offset="1" stop-color="#df77ba"/></linearGradient></defs><path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7M8 14h8m-4-4v8"/><path class="ai-colored-spark" d="M18 2l1.2 3.8L23 7l-3.8 1.2L18 12l-1.2-3.8L13 7l3.8-1.2Z" fill="url(#case-ai-spark)" stroke="url(#case-ai-spark)"/></svg>';let previewTimer;add.onmouseenter=()=>{clearTimeout(previewTimer);previewTimer=setTimeout(()=>{if(add.matches(':hover'))newCaseTask(add,true);},220);};add.onmouseleave=()=>clearTimeout(previewTimer);add.onclick=()=>{clearTimeout(previewTimer);const card=document.querySelector('.ai-action-dialog');if(card)card.pinTask();else newCaseTask(add);};toolbar.append(add);}

    const content=[toolbar,el('div','targets-summary'),el('p','targets-error'),el('div','targets-diagnostics'),el('div','targets-tree')];
    if(!caseMode&&!bugMode){
      panel.classList.add('coverage-tabs-panel');
      tabBar=el('nav','target-coverage-tabs');tabBar.setAttribute('role','tablist');tabBar.setAttribute('aria-label','覆盖率类型');
      for(const [mode,label] of coverageModes){
        const b=button(label,()=>selectCoverageTab(mode));b.dataset.mode=mode;b.setAttribute('role','tab');tabBar.append(b);
      }
      functionalPane=el('div','target-goal-pane');functionalPane.append(...content);
      linePane=el('div','target-line-pane');lineFrame=el('iframe');lineFrame.title='行覆盖率明细';linePane.append(lineFrame);
      functionalPane.setAttribute('role','tabpanel');linePane.setAttribute('role','tabpanel');
      tabBar.addEventListener('keydown',event=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();const tabs=[...tabBar.querySelectorAll('button')],index=tabs.indexOf(document.activeElement);
        const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
        tabs[next].focus();selectCoverageTab(tabs[next].dataset.mode);
      });
      panel.append(el('div','target-coverage-overview'),tabBar,functionalPane,linePane);
      document.addEventListener('flowcanvas-case-restore',async event=>{if(event.detail.project_id!==project.id||! /^(vt|fg|fp|tp)_/.test(event.detail.case_id))return;if(!data)await refresh();const node=data?.targets.find(item=>item.id===event.detail.case_id);if(node){if(node.kind==='group'){goalCollapsed.delete(node.id);renderGoals();return;}selectedTarget=node.id;goalDetail(node);renderGoals();}});
      document.addEventListener('flowcanvas-case-tab-closed',event=>{const inspector=goalInspectors.get(event.detail.case_id);if(inspector){inspector.generation++;inspector.host.remove();goalInspectors.delete(event.detail.case_id);}});
      renderCoverageSummary();
      try{coverageTab=localStorage.getItem('fc-target-coverage-tab:'+project.id)||'functional';}catch{}
      selectCoverageTab(coverageTab);
    }else if(caseMode){
      panel.classList.add('testcase-workbench');
      const listPane=el('div','testcase-list-pane');listPane.append(...content);
      panel.append(listPane);
      const inspectors=new Map();let activeInspector=null;
      caseDetail={
        select(node,bindings){
          const context=window.FlowCanvasCaseWorkspace.context();
          const visible=context?.project.id===project.id&&context?.testcase.id===node?.id;
          for(const inspector of inspectors.values())inspector.host.hidden=!visible||inspector.id!==node?.id;
          if(!node){activeInspector=null;return;}
          let inspector=inspectors.get(node.id);
          if(!inspector){
            const detail=el('section','testcase-detail-pane');detail.dataset.entityId=node.id;detail.setAttribute('aria-label','测试用例详情');
            window.FlowCanvasCaseWorkspace.mount(detail);
            inspector={id:node.id,host:detail,api:window.FlowCanvasCaseDetails(detail,()=>project)};inspectors.set(node.id,inspector);
          }
          inspector.host.hidden=!visible;activeInspector=inspector;inspector.api.select(node,bindings);
        },
        context(){return activeInspector?.api.context()||{};}
      };
      document.addEventListener('flowcanvas-case-tab-closed',event=>{
        if(event.detail.project_id!==project.id)return;
        const inspector=inspectors.get(event.detail.case_id);if(!inspector)return;
        inspector.api.dispose();inspectors.delete(event.detail.case_id);if(activeInspector===inspector)activeInspector=null;
      });
      document.addEventListener('flowcanvas-case-restore',async event=>{
        if(event.detail.project_id!==project.id||!event.detail.case_id.startsWith('tc_'))return;
        if(!data)await refresh();
        const node=data?.targets.find(item=>item.id===event.detail.case_id);if(!node)return;
        selectedTarget=node.id;caseDetail.select(node,data.reports[node.id]);
        window.FlowCanvasCaseWorkspace.open(project,node,caseDetail.context());
        render();
      });

      document.addEventListener('flowcanvas-case-close',()=>{selectedTarget=null;if(data)render();});
    }else if(bugMode){
      panel.classList.add('bug-workbench');panel.append(...content);
      document.addEventListener('flowcanvas-case-restore',async event=>{if(event.detail.project_id!==project.id||!event.detail.case_id.startsWith('bug_'))return;if(!data)await refresh();const node=data?.targets.find(item=>item.id===event.detail.case_id);if(node){selectedTarget=node.id;bugDetail(node);renderBugs();}});
      document.addEventListener('flowcanvas-case-tab-closed',event=>{const inspector=bugInspectors.get(event.detail.case_id);if(inspector){inspector.host.remove();bugInspectors.delete(event.detail.case_id);}});
    }else panel.append(...content);
      document.addEventListener('flowcanvas-case-task',event=>{
        const d=event.detail;if(bugMode||d.project.id!==project.id||(d.testcase.entity_type==='target')!==(!caseMode))return;
        const key=d.kind+':'+d.testcase.id+':'+(d.run_id||''),card=document.querySelector('.ai-action-dialog');
        if(card){if(card.dataset.caseTask===key){if(!d.preview)card.pinTask();else card.dispatchEvent(new Event('mouseenter'));return;}if(!card.classList.contains('is-preview'))return;card.close();}
        newCaseTask(d.anchor,!!d.preview,{kind:d.kind,testcase:d.testcase,run_id:d.run_id,waveform:d.waveform,instance:d.testcase.title||d.testcase.name});
        const opened=document.querySelector('.ai-action-dialog');if(opened)opened.dataset.caseTask=key;
      });
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
    window.FlowCanvasCaseWorkspace.ready(project,caseMode?'case':bugMode?'bug':'target');
  }).catch(()=>{});
}
mountDefinitionPanel({view:'验证目标',endpoint:'targets'});
mountDefinitionPanel({view:'测试用例',endpoint:'testcases',caseMode:true});

mountDefinitionPanel({view:'Bug 管理',endpoint:'bugs',bugMode:true});
