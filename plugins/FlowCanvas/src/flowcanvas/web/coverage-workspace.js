/** Build-source coverage embedded in the design tab, sharing its selected module and conversation. */
window.FlowCanvasCoverageCode={
  /** Own one module's metric/file selection and cancel stale source or coverage responses. */
  create(host,project,node){
    const el=(tag,cls,text)=>{const element=document.createElement(tag);if(cls)element.className=cls;if(text!==undefined)element.textContent=text;return element;};
    const names={line:'行覆盖率',toggle:'翻转覆盖率',branch:'分支覆盖率',expr:'表达式覆盖率',cond:'条件覆盖率',fsm:'状态机覆盖率',assert:'断言'};
    let metric=localStorage.getItem('flowcanvas.design.coverage.'+project.id)||'line';if(!names[metric])metric='line';
    let selection=null,revision='',generation=0,loading=false,disposed=false,started=false;
    const tools=el('div','coverage-code-tools'),metrics=el('select','case-detail-select coverage-metric-select'),files=el('select','case-detail-select coverage-file-select'),legend=el('div','coverage-code-legend'),locationLabel=el('div','coverage-code-location'),body=el('div','case-detail-body coverage-code-body');
    metrics.ariaLabel='覆盖率指标';files.ariaLabel='构建源码文件';for(const [key,name] of Object.entries(names)){const option=el('option','',name);option.value=key;metrics.append(option);}metrics.value=metric;
    for(const [kind,label] of [['covered','已覆盖'],['partial','部分覆盖'],['missed','未覆盖']])legend.append(el('span',kind,label));tools.append(metrics,files,legend);host.append(tools,locationLabel,body);files.hidden=true;
    /** Send only visible coverage selection, retaining the current design-instance binding. */
    function publish(){if(host.hidden||disposed)return;document.dispatchEvent(new CustomEvent('flowcanvas-case-view',{detail:{case_id:node.id,view:{tab:'coverage',module_instance:node.instance,coverage_metric:metric,...(selection?.data?.file?{coverage_file:selection.data.file}:{})}}}));}
    /** Display measured line colors on the fingerprint-checked build snapshot. */
    function render(value){
      selection=value;metric=value.metric;metrics.value=metric;files.replaceChildren();
      for(const entry of value.files){const option=el('option','',entry.path);option.value=entry.path;option.selected=entry.path===value.data.file;files.append(option);}files.hidden=value.files.length<2;
      const source=value.data,coverage=value.files.find(entry=>entry.path===source.file)||{},hits=new Set(coverage.covered_lines||[]),misses=new Set(coverage.uncovered_lines||[]),partial=new Set(coverage.partial_lines||[]),pre=el('pre','case-detail-source coverage-code-source');pre.tabIndex=0;pre.ariaLabel='构建源码及覆盖率标记';locationLabel.textContent=source.file+' · 构建源码';
      source.content.split('\n').forEach((text,index)=>{const number=index+1,row=el('div',number>=source.line&&number<=source.end_line?'is-bound':'');row.dataset.line=number;const state=partial.has(number)?'partial':misses.has(number)?'missed':hits.has(number)?'covered':'';if(state){row.classList.add(state);row.title=({covered:'已覆盖',partial:'部分覆盖',missed:'未覆盖'})[state];}row.append(el('span','',String(number)),el('code','',text||' '));pre.append(row);});const section=window.FlowCanvasDetailSection(host,'构建源码',node.instance+':'+metric);section.append(pre);body.replaceChildren(section);
      section.open=true;window.FlowCanvasScrollToSourceLine(body,pre.children[source.line-1],pre.children[source.end_line-1]);publish();
    }
    /** Read build sources through the existing snapshot/fingerprint guard; never substitute current RTL. */
    async function source(row,file,token){const query=new URLSearchParams({instance:node.instance,metric,file:file||''}),response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage/source?'+query),data=await response.json();if(disposed||token!==generation)return;if(!response.ok)throw Error(data.detail||'无法读取构建源码');render({instance:node.instance,metric,files:row.files||[],data});}
    /** Refresh only when published coverage changes, preserving the code's scroll position otherwise. */
    async function refresh(){
      if(disposed||loading)return;loading=true;started=true;const token=++generation;
      try{const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage'),state=await response.json();if(disposed||token!==generation)return;if(!response.ok)throw Error(state.detail||'无法读取覆盖率');const next=JSON.stringify([state.build_id,state.report?.updated,state.runs,metric]);if(next===revision)return;
        const {coverageMetricRows}=await import('/rtl-viewer/overview-structure.js');if(disposed||token!==generation)return;const rows=coverageMetricRows(state.report,metric,node.instance.split('.')[0]),row=rows.find(entry=>entry.path===node.instance);revision=next;
        if(!row?.files?.length){selection=null;files.hidden=true;locationLabel.textContent='';body.replaceChildren(el('p','case-detail-empty','当前模块暂无'+names[metric]+'数据'));publish();return;}const preferred=selection?.data?.file,file=row.files.some(entry=>entry.path===preferred)?preferred:row.files[0].path;await source(row,file,token);
      }catch(error){if(!disposed&&token===generation){revision='';body.replaceChildren(el('p','targets-error',error.message));}}finally{loading=false;}
    }
    metrics.onchange=()=>{metric=metrics.value;revision='';selection=null;try{localStorage.setItem('flowcanvas.design.coverage.'+project.id,metric);}catch{}generation++;loading=false;refresh();publish();};
    files.onchange=()=>{const value=selection;if(!value)return;const token=++generation;source({files:value.files},files.value,token).catch(error=>{if(!disposed&&token===generation)body.replaceChildren(el('p','targets-error',error.message));});};
    return {activate(){if(!started)refresh();},refresh,select(value){++generation;loading=false;started=true;revision='';render(value);},context(){return {coverage_metric:metric,...(selection?.data?.file?{coverage_file:selection.data.file}:{})};},dispose(){disposed=true;generation++;}};
  }
};

/** Open build-source tabs while sharing one conversation per coverage metric. */
(()=>{
  const inspectors=new Map(),names={line:'行覆盖率',toggle:'翻转覆盖率',branch:'分支覆盖率',expr:'表达式覆盖率',cond:'条件覆盖率',fsm:'状态机覆盖率',assert:'断言'};
  let project=null,selection=0;
  const el=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  /** Re-read persisted selections through the build snapshot and fingerprint checks. */
  async function open(node,value=null,pin=false){
    if(!project||!names[node.coverage_metric]||!node.instance)return;
    const epoch=++selection;let inspector=inspectors.get(node.id);
    if(!inspector){
      const host=el('section','testcase-detail-pane coverage-source-detail');host.dataset.entityId=node.id;host.ariaLabel='覆盖率源码';
      const title=el('header','case-detail-title');title.append(el('strong','',node.title),el('code','',node.instance));
      const tabs=el('nav','case-detail-tabs');tabs.setAttribute('aria-label','覆盖率详情');const tab=el('button','','源码');tab.type='button';tab.setAttribute('aria-selected','true');tabs.append(tab);
      const location=el('div','coverage-code-location'),body=el('div','case-detail-body');host.append(title,tabs,location,body);
      inspector={host,location,body,generation:0};inspectors.set(node.id,inspector);window.FlowCanvasCaseWorkspace.mount(host);
    }
    const token=++inspector.generation;inspector.body.replaceChildren(el('p','case-detail-empty','正在读取源码…'));
    window.FlowCanvasCaseWorkspace.open(project,node,{tab:'source',module_instance:node.instance,coverage_metric:node.coverage_metric,coverage_file:node.coverage_file});if(pin)window.FlowCanvasCaseWorkspace.pin(project.id,node.id);
    try{
      if(!value){
        const query=new URLSearchParams({instance:node.instance,metric:node.coverage_metric,file:node.coverage_file||''});
        const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage/source?'+query),data=await response.json();
        if(!response.ok)throw Error(data.detail||'无法读取构建源码');value={data};
        // Coverage colors are refreshed from the current report rather than saved in browser state.
        const coverageResponse=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage');
        if(coverageResponse.ok){const state=await coverageResponse.json(),{coverageMetricRows}=await import('/rtl-viewer/overview-structure.js');value.files=coverageMetricRows(state.report,node.coverage_metric,node.instance.split('.')[0]).find(row=>row.path===node.instance)?.files||[];}
      }
      if(epoch!==selection||token!==inspector.generation||!inspector.host.isConnected)return;
      const source=value.data,record=value.files?.find(file=>file.path===source.file),hits=new Set(record?.covered_lines||[]),misses=new Set(record?.uncovered_lines||[]),partial=new Set(record?.partial_lines||[]);
      inspector.location.textContent=source.file+' · 构建源码';
      const pre=el('pre','case-detail-source coverage-code-source');pre.tabIndex=0;pre.ariaLabel='构建源码';
      source.content.split('\n').forEach((text,index)=>{const number=index+1,state=partial.has(number)?'partial':misses.has(number)?'missed':hits.has(number)?'covered':'';const row=el('div',state);row.dataset.line=number;row.append(el('span','',String(number)),el('code','',text||' '));pre.append(row);});
      const section=window.FlowCanvasDetailSection(inspector.host,'构建源码');section.append(pre);inspector.body.replaceChildren(section);
      let scrollTimer;inspector.body.onscroll=()=>{clearTimeout(scrollTimer);scrollTimer=setTimeout(()=>{if(inspector.host.hidden||!pre.isConnected)return;const y=inspector.body.getBoundingClientRect().top,row=[...pre.children].find(item=>item.getBoundingClientRect().bottom>y);if(row)document.dispatchEvent(new CustomEvent('flowcanvas-case-view',{detail:{case_id:node.id,view:{tab:'source',module_instance:node.instance,coverage_metric:node.coverage_metric,coverage_file:node.coverage_file,line:Number(row.dataset.line)}}}));},150);};
      section.open=true;window.FlowCanvasScrollToSourceLine(inspector.body,pre.children[source.line-1]);
    }catch(error){if(epoch===selection&&token===inspector.generation&&inspector.host.isConnected)inspector.body.replaceChildren(el('p','targets-error',error.message));}
  }
  window.addEventListener('message',event=>{
    const frame=document.querySelector('.target-line-pane iframe'),value=event.data;
    if(event.origin!==location.origin||event.source!==frame?.contentWindow||value?.type!=='flowcanvas-open-coverage-source'||!project||!names[value.metric]||typeof value.instance!=='string'||typeof value.data?.content!=='string')return;
    const file=value.data.file||'',node={id:'cov_'+encodeURIComponent(value.metric+':'+value.instance+':'+file),entity_type:'coverage',title:names[value.metric]+' · '+value.instance.split('.').at(-1),instance:value.instance,coverage_metric:value.metric,coverage_file:file};
    open(node,value,!!value.pin);
  });
  document.addEventListener('flowcanvas-case-restore',event=>{if(event.detail.project_id!==project?.id||! /^(cov_|coverage_)/.test(event.detail.case_id))return;const node=window.FlowCanvasCaseWorkspace.tab(project.id,event.detail.case_id);if(node)open(node);});
  document.addEventListener('flowcanvas-case-tab-closed',event=>{if(event.detail.project_id!==project?.id)return;const inspector=inspectors.get(event.detail.case_id);if(inspector){inspector.generation++;inspector.host.remove();inspectors.delete(event.detail.case_id);}});
  fetch('/api/chat/projects').then(response=>response.json()).then(state=>{project=state.current;if(project)window.FlowCanvasCaseWorkspace.ready(project,'coverage');}).catch(()=>{});
})();
