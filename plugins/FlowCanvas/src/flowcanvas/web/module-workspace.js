/** Open module source tabs with independent source positions and scoped conversations. */
(()=>{
  const inspectors=new Map();let project=null,generation=0,lastInstance='';
  fetch('/api/chat/projects').then(response=>response.json()).then(state=>{project=state.current;if(project&&document.getElementById('project-heading').textContent==='设计总览')open({id:'module_design'});});
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  /** Resolve the selected instance through the server before showing source or chat. */
  async function open(selection,pin=false,refresh=false){
    if(!project)return;const token=++generation;
    try{
      const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+(selection.instance?'/module?instance='+encodeURIComponent(selection.instance):'/modules/'+encodeURIComponent(selection.id))),result=await response.json();if(token!==generation)return;
      if(!response.ok)throw Error(result.detail||'无法读取模块源码');
      const node=result.module,source=result.source;lastInstance=node.instance;let inspector=inspectors.get(node.id);
      if(!inspector){const host=el('section','testcase-detail-pane');host.dataset.entityId=node.id;host.setAttribute('aria-label','模块源码');inspector={host,revision:'',tab:'overview'};inspectors.set(node.id,inspector);window.FlowCanvasCaseWorkspace.mount(host);}
      if(refresh&&inspector.host.hidden)return;
      const revision=JSON.stringify(result);if(refresh&&inspector.revision===revision){if(inspector.tab==='coverage')inspector.coverage?.refresh();return;}
      if(selection.coverage)inspector.tab='coverage';
      if(inspector.revision!==revision){
        inspector.revision=revision;const title=el('header','case-detail-title');title.append(el('strong','','设计总览'),el('code','',node.instance+' · '+source.file));
        const tabs=el('nav','case-detail-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','设计详情');
        const overview=el('div','case-detail-body');overview.dataset.tab='overview';
        const detail=node.overview||{};
        /** Use the same current-source viewer and citation format as test-case explanations. */
        const reference=(container,item)=>{if(!Number.isInteger(item?.line)||item.line<1)return;const file=item.file||source.file,end=item.end_line,label=file+':'+item.line+(end&&end!==item.line?'-'+end:'');const link=el('button','case-story-source',label);link.type='button';link.title='查看当前源码 · '+label;link.onclick=async()=>{try{const {openLocalSource}=await import('./markdown-renderer.js');await openLocalSource({path:file.startsWith('/')?file:project.path+'/'+file,line:item.line,end});}catch(error){link.title='无法打开源码：'+error.message;link.textContent=label+' · 打开失败';}};container.append(link);};
        /** Keep concrete assignments beside their explanation rather than in a nested card. */
        const implementation=(container,item)=>{if(!item.assignments?.length)return;const block=el('div','case-story-assignments');block.append(el('div','case-story-label','关键实现'));for(const assignment of item.assignments){const row=el('div','case-story-assignment');row.append(el('code','',assignment.expression),el('span','',assignment.meaning));reference(row,assignment);block.append(row);}container.append(block);};
        const section=label=>{const part=el('section','case-section');part.append(el('h4','case-section-title',label));overview.append(part);return part;};
        const purpose=section('模块职责');purpose.append(el('p',detail.purpose?'':'case-detail-empty',detail.purpose?.text||'模块说明待完善'));reference(purpose,detail.purpose);const actions=el('div','case-overview-actions');actions.append(window.FlowCanvasCaseTaskButton('module-overview','生成模块概览',{project,testcase:node,kind:'module-overview'}));purpose.querySelector('h4').append(actions);
        const structure=section('核心结构');
        if(detail.structure?.nodes?.length){
          const diagram=el('div','module-structure','正在布局…');structure.append(diagram);
          import('/rtl-viewer/overview-structure.js').then(module=>module.renderStructure(diagram,detail.structure)).catch(()=>{if(diagram.isConnected)diagram.textContent='结构图暂时无法显示，请刷新重试';});
          const explanations=el('ul','module-structure-summary');for(const item of detail.structure.nodes){const entry=el('li');entry.append(el('strong','',item.title+'：'),document.createTextNode(item.text||item.detail));explanations.append(entry);}structure.append(explanations);
        }else structure.append(el('p','case-detail-empty','结构说明待完善'));
        const children=section('子模块');
        if(node.children?.length){const list=el('ul','module-child-list');for(const child of node.children){const entry=el('li'),link=el('button','module-child-link',child.key+' · '+child.module);link.type='button';link.title='打开此子模块的概览';link.onclick=()=>open({instance:child.instance});entry.append(link,el('span','',detail.submodules?.[child.key]||'用途说明待完善'));list.append(entry);}children.append(list);}else children.append(el('p','case-detail-empty','当前解析配置下没有直接子模块'));
        for(const [key,label] of [['mechanisms','工作机制'],['rules','关键规则']]){const part=section(label);if(detail[key]?.length){const list=el('dl','module-explanation-list');for(const item of detail[key]){const heading=el('dt','',item.title),description=el('dd','',item.text);if(!item.assignments?.length){const citation=el('div','case-story-evidence');reference(citation,item);description.append(citation);}implementation(description,item);list.append(heading,description);}part.append(list);}else part.append(el('p','case-detail-empty','内容待完善'));}
        const interfaces=section('接口与参数');
        if(node.ports?.length){const table=el('table','module-port-table'),head=el('thead'),row=el('tr');row.append(el('th','','方向'),el('th','','端口'),el('th','','含义'));head.append(row);const rows=el('tbody');for(const port of node.ports){const row=el('tr');row.append(el('td','',({input:'输入',output:'输出',inout:'双向'})[port.direction]||port.direction),el('td','module-symbol',port.name),el('td','',detail.ports?.[port.name]||'—'));rows.append(row);}table.append(head,rows);interfaces.append(table);}
        if(detail.parameters?.length){const list=el('dl','module-explanation-list');for(const item of detail.parameters){const heading=el('dt','',item.title),description=el('dd','',item.text);if(!item.assignments?.length){const citation=el('div','case-story-evidence');reference(citation,item);description.append(citation);}implementation(description,item);list.append(heading,description);}interfaces.append(list);}
        const body=el('div','case-detail-body');body.dataset.tab='source';const pre=el('pre','case-detail-source');pre.tabIndex=0;pre.ariaLabel='模块源码';
        source.file_content.split('\n').forEach((text,index)=>{const row=el('div',index+1>=source.line&&index+1<=source.end_line?'is-bound':'');row.dataset.line=String(index+1);row.append(el('span','',String(index+1)),el('code','',text||' '));pre.append(row);});
        body.append(pre);const coverage=el('div','case-detail-body module-coverage-panel');coverage.dataset.tab='coverage';inspector.coverage?.dispose();inspector.coverage=window.FlowCanvasCoverageCode.create(coverage,project,node);inspector.host.replaceChildren(title,tabs,overview,body,coverage);
        const panels={overview,source:body,coverage},prefix='design-'+project.id+'-';
        for(const [key,label] of [['overview','概览'],['source','源码'],['coverage','覆盖率']]){
          const button=el('button','',label);button.type='button';button.id=prefix+key;button.setAttribute('role','tab');button.setAttribute('aria-controls',prefix+key+'-panel');
          panels[key].id=prefix+key+'-panel';panels[key].setAttribute('role','tabpanel');panels[key].setAttribute('aria-labelledby',button.id);
          button.onclick=()=>{inspector.tab=key;inspector.activate();document.dispatchEvent(new CustomEvent('flowcanvas-case-view',{detail:{case_id:node.id,view:{tab:key,module_instance:node.instance,...(key==='coverage'?inspector.coverage.context():{})}}}));};
          button.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const keys=['overview','source','coverage'],key=event.key==='Home'?'overview':event.key==='End'?'coverage':keys[(keys.indexOf(inspector.tab)+(event.key==='ArrowRight'?1:-1)+keys.length)%keys.length];const target=tabs.querySelector('#'+prefix+key);target.click();target.focus();}};tabs.append(button);
        }
        let sourcePositioned=false;
        inspector.activate=()=>{if(inspector.tab==='coverage')inspector.coverage.activate();if(inspector.tab==='source'&&!sourcePositioned){sourcePositioned=true;requestAnimationFrame(()=>{const row=pre.children[source.line-1];if(row)pre.scrollTop=Math.max(0,row.offsetTop-pre.offsetTop-60);});}for(const [key,panel] of Object.entries(panels)){panel.hidden=inspector.tab!==key;const button=tabs.querySelector('#'+prefix+key);button.setAttribute('aria-selected',String(!panel.hidden));button.tabIndex=panel.hidden?-1:0;}};inspector.activate();
        let timer;pre.onscroll=()=>{clearTimeout(timer);timer=setTimeout(()=>{if(inspector.host.hidden||body.hidden||inspector.revision!==revision)return;const top=pre.getBoundingClientRect().top,row=[...pre.children].find(row=>row.getBoundingClientRect().bottom>top);if(row)document.dispatchEvent(new CustomEvent('flowcanvas-case-view',{detail:{case_id:node.id,view:{tab:'source',module_instance:node.instance,line:Number(row.dataset.line)}}}));},150);};
      }
      if(selection.coverage){inspector.coverage.select(selection.coverage);inspector.activate();}
      window.FlowCanvasCaseWorkspace.open(project,node,{tab:inspector.tab,module_instance:node.instance,...(inspector.tab==='coverage'?inspector.coverage.context():{})});window.FlowCanvasCaseWorkspace.pin(project.id,node.id);
    }catch(error){if(refresh)return;console.error(error);document.getElementById('project-content').append(el('p','targets-error',error.message));}
  }
  setInterval(()=>{const inspector=inspectors.get('module_design');if(!document.hidden&&inspector&&!inspector.host.closest('[hidden]')&&lastInstance)open({instance:lastInstance},false,true);},2500);
  window.addEventListener('message',event=>{const frame=document.querySelector('.rtl-view-panel iframe');if(event.origin===location.origin&&event.source===frame?.contentWindow&&event.data?.type==='flowcanvas-open-module'&&typeof event.data.instance==='string')open({instance:event.data.instance},!!event.data.pin);});
  window.addEventListener('message',event=>{const frame=[...document.querySelectorAll('.target-line-pane iframe,.rtl-view-panel iframe')].find(frame=>frame.contentWindow===event.source);if(event.origin!==location.origin||!frame||event.data?.type!=='flowcanvas-open-coverage-source')return;const value=event.data;if(typeof value.instance==='string'&&value.data&&Array.isArray(value.files))open({instance:value.instance,coverage:value},true);});
  document.addEventListener('flowcanvas-case-restore',event=>{if(event.detail.project_id===project?.id&&event.detail.case_id.startsWith('module_'))open(lastInstance?{instance:lastInstance}:{id:event.detail.case_id});});
  document.addEventListener('flowcanvas-view',event=>{if(event.detail==='设计总览')open(lastInstance?{instance:lastInstance}:{id:'module_design'});});
  document.addEventListener('flowcanvas-case-tab-closed',event=>{const inspector=inspectors.get(event.detail.case_id);if(inspector){inspector.coverage?.dispose();inspector.host.remove();inspectors.delete(event.detail.case_id);}});
})();
