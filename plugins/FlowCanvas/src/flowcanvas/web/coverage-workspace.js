/** Build-source coverage embedded in the design tab, sharing its selected module and conversation. */
window.FlowCanvasCoverageCode={
  /** Own one module's metric/file selection and cancel stale source or coverage responses. */
  create(host,project,node){
    const el=(tag,cls,text)=>{const element=document.createElement(tag);if(cls)element.className=cls;if(text!==undefined)element.textContent=text;return element;};
    const names={line:'行覆盖率',toggle:'翻转覆盖率',branch:'分支覆盖率',expr:'表达式覆盖率',cond:'条件覆盖率',fsm:'状态机覆盖率',assert:'断言'};
    let metric=localStorage.getItem('flowcanvas.design.coverage.'+project.id)||'line';if(!names[metric])metric='line';
    let selection=null,revision='',generation=0,loading=false,disposed=false,started=false;
    const tools=el('div','coverage-code-tools'),metrics=el('select','case-detail-select'),files=el('select','case-detail-select'),legend=el('div','coverage-code-legend'),locationLabel=el('div','coverage-code-location'),body=el('div','case-detail-body coverage-code-body');
    metrics.ariaLabel='覆盖率指标';files.ariaLabel='构建源码文件';for(const [key,name] of Object.entries(names)){const option=el('option','',name);option.value=key;metrics.append(option);}metrics.value=metric;
    for(const [kind,label] of [['covered','已覆盖'],['partial','部分覆盖'],['missed','未覆盖']])legend.append(el('span',kind,label));tools.append(metrics,files,legend);host.append(tools,locationLabel,body);files.hidden=true;
    /** Send only visible coverage selection, retaining the current design-instance binding. */
    function publish(){if(host.hidden||disposed)return;document.dispatchEvent(new CustomEvent('flowcanvas-case-view',{detail:{case_id:node.id,view:{tab:'coverage',module_instance:node.instance,coverage_metric:metric,...(selection?.data?.file?{coverage_file:selection.data.file}:{})}}}));}
    /** Display measured line colors on the fingerprint-checked build snapshot. */
    function render(value){
      selection=value;metric=value.metric;metrics.value=metric;files.replaceChildren();
      for(const entry of value.files){const option=el('option','',entry.path);option.value=entry.path;option.selected=entry.path===value.data.file;files.append(option);}files.hidden=value.files.length<2;
      const source=value.data,coverage=value.files.find(entry=>entry.path===source.file)||{},hits=new Set(coverage.covered_lines||[]),misses=new Set(coverage.uncovered_lines||[]),partial=new Set(coverage.partial_lines||[]),pre=el('pre','case-detail-source coverage-code-source');pre.tabIndex=0;pre.ariaLabel='构建源码及覆盖率标记';locationLabel.textContent=source.file+' · 构建源码';
      source.content.split('\n').forEach((text,index)=>{const number=index+1,row=el('div',number>=source.line&&number<=source.end_line?'is-bound':'');row.dataset.line=number;const state=partial.has(number)?'partial':misses.has(number)?'missed':hits.has(number)?'covered':'';if(state){row.classList.add(state);row.title=({covered:'已覆盖',partial:'部分覆盖',missed:'未覆盖'})[state];}row.append(el('span','',String(number)),el('code','',text||' '));pre.append(row);});body.replaceChildren(pre);
      requestAnimationFrame(()=>{if(!disposed&&pre.isConnected){const row=pre.children[source.line-1];if(row)pre.scrollTop=Math.max(0,row.offsetTop-pre.offsetTop-40);}});publish();
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
