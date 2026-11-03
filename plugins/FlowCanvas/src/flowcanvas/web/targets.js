/* Verification goals are stable records; source scans only verify their bindings. */
(()=>{
  const host=document.getElementById('project-content');
  let project=null,data=null,panel=null,active=false,query='',busy=false,searchExpanded=true,selectedTarget=null;
  const opened=new Set();
  const labels={planned:'待实现',bound:'已绑定',changed:'待核对',missing:'绑定失效',conflict:'绑定冲突',unverified:'无法校验'};
  const kinds={group:['FG','功能组'],point:['FP','功能点'],testpoint:['TP','测试点']};
  function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;}
  function button(text,fn,cls=''){const n=el('button',cls,text);n.type='button';n.onclick=fn;return n;}
  function smallAction(label,pathData,callback){
    const b=button('',callback,'target-icon-action');b.title=label;b.setAttribute('aria-label',label);
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');
    const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',pathData);svg.append(path);b.append(svg);return b;
  }
  async function api(request){const url='/api/chat/projects/'+encodeURIComponent(project.id)+'/targets';const response=await fetch(url,request?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)}:{});const body=await response.json();if(!response.ok)throw Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));return body;}
  function fail(error){panel.querySelector('.targets-error').textContent=error.message;}
  async function refresh(){if(busy)return;busy=true;try{panel.querySelector('.targets-error').textContent='';const first=!data;data=await api();if(first)for(const n of data.targets)if(n.kind==='group')opened.add(n.id);render();}catch(e){fail(e);}finally{busy=false;}}
  function modal(title){const dialog=el('dialog','targets-dialog');const heading=el('header');heading.append(el('strong','',title),button('×',()=>dialog.close(),'targets-close'));const content=el('div','targets-dialog-content');dialog.append(heading,content);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});dialog.showModal();return {dialog,content};}
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

  function edit(node=null,kind='group',parent=''){
    if(!data)return;
    const {dialog,content}=modal(node?'编辑'+kinds[node.kind][1]:'新建'+kinds[kind][1]);
    const name=field(content,'名称',node?.name||'');const desc=field(content,'说明',node?.description||'',true);
    const condition=field(content,'验证条件',node?.condition||'',true);const expected=field(content,'预期行为',node?.expected||'',true);
    const error=el('p','targets-error');content.append(error);
    const save=button('保存',async()=>{save.disabled=true;try{const values={description:desc.value,condition:condition.value,expected:expected.value};await api(node?{operation:'update',target_id:node.id,expected_revision:data.revision,values:{...values,name:name.value}}:{operation:'create',kind,parent,name:name.value,values,expected_revision:data.revision});if(parent)opened.add(parent);dialog.close();await refresh();}catch(e){error.textContent=e.message;save.disabled=false;}} ,'targets-primary');content.append(save);name.focus();
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
  function render(){
    const list=panel.querySelector('.targets-tree'),summary=panel.querySelector('.targets-summary');list.replaceChildren();summary.replaceChildren();
    for(const [kind,[,label]]of Object.entries(kinds))summary.append(el('span','',data.targets.filter(n=>n.kind===kind).length+' '+label));
    const allExpanded=data.targets.length>0&&data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));
    const toggleAll=button(allExpanded?'全部折叠':'全部展开',()=>{const expanded=data.targets.every(node=>opened.has(node.id)||(query&&searchExpanded));if(expanded){opened.clear();searchExpanded=false;}else{for(const node of data.targets)opened.add(node.id);}render();},'targets-toggle-all');
    toggleAll.disabled=!data.targets.length;summary.append(toggleAll);
    const byId=new Map(data.targets.map(n=>[n.id,n]));const visible=new Set();
    for(const n of data.targets){if(!query||[n.name,n.description,n.id,...n.bindings.map(b=>b.file)].join(' ').toLowerCase().includes(query)){let current=n;while(current){visible.add(current.id);current=byId.get(current.parent);}}}
    function card(node,depth){
      const report=data.reports[node.id]||{status:'planned',bindings:[]};const children=data.targets.filter(n=>n.parent===node.id&&visible.has(n.id));
      const wrapper=el('article','target-card target-'+node.kind);const head=button('',()=>{
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
        const toggle=panel.querySelector('.targets-toggle-all');if(toggle)toggle.textContent=data.targets.every(item=>opened.has(item.id))?'全部折叠':'全部展开';
      },'target-card-head');head.setAttribute('aria-expanded',String(opened.has(node.id)||(!!query&&searchExpanded)));
      const chevron=el('span','target-chevron'+((opened.has(node.id)||(query&&searchExpanded))?' is-expanded':''));
      const arrow=document.createElementNS('http://www.w3.org/2000/svg','svg');arrow.setAttribute('viewBox','0 0 16 16');arrow.setAttribute('aria-hidden','true');
      const arrowPath=document.createElementNS(arrow.namespaceURI,'path');arrowPath.setAttribute('d','m5 3 6 5-6 5Z');arrow.append(arrowPath);chevron.append(arrow);
      head.append(chevron,el('span','target-kind',kinds[node.kind][0]));const title=el('span','target-title',node.name);head.append(title);
      if(report.status==='planned')head.append(el('span','target-state state-planned','待实现'));
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
      const controls=el('div','target-actions target-header-actions');controls.append(smallAction('编辑','m15 5 4 4M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15Z',()=>edit(node)));if(node.kind!=='testpoint')controls.append(smallAction('添加'+(node.kind==='group'?'功能点':'测试点'),'M12 5v14M5 12h14',()=>edit(null,node.kind==='group'?'point':'testpoint',node.id)));
      const links=el('div','target-title-sources');
      for(const binding of report.bindings){
        const label=binding.file.split('/').at(-1)+(binding.line?':'+binding.line+'–'+binding.end_line:'');
        const b=button(label,()=>source(binding),'target-source target-title-source');b.disabled=!binding.line;
        b.title=binding.file+(binding.message?' · '+binding.message:' · 查看源码');b.setAttribute('aria-label','查看源码 '+binding.file+(binding.line?' 第 '+binding.line+' 行':''));links.append(b);
      }
      header.append(head,links,controls);wrapper.append(header);
      if(opened.has(node.id)||(query&&searchExpanded)){
        const body=el('div','target-card-body');
        for(const [label,value]of [['',node.description],['条件',node.condition],['预期',node.expected]]){if(value){const p=el('p');if(label)p.append(el('strong','',label+' · '));p.append(document.createTextNode(value));body.append(p);}}

        const actions=el('div','target-actions');
        if(report.status==='changed')actions.append(button('核对后确认',()=>{const {dialog,content}=modal('确认源码与目标一致');content.append(el('p','','请先查看当前源码，核对描述、验证条件和预期行为。确认仅更新代码基线，不代表仿真通过。'));const error=el('p','targets-error');content.append(error,button('已核对，确认当前版本',async()=>{try{await api({operation:'accept',target_id:node.id,expected_revision:data.revision,values:Object.fromEntries(report.bindings.map(b=>[b.id,b.fingerprint]))});dialog.close();refresh();}catch(e){error.textContent=e.message;}},'targets-primary'));}));
        if(actions.children.length)body.append(actions);for(const child of children)body.append(card(child,depth+1));wrapper.append(body);
      }else if(children.length){head.title=children.length+' 个子目标';}
      return wrapper;
    }
    for(const group of data.targets.filter(n=>n.kind==='group'&&visible.has(n.id)))list.append(card(group,0));
    if(!list.children.length){const empty=el('div','targets-empty');empty.append(el('strong','',query?'没有匹配的验证目标':'从验证目标开始'),el('p','targets-muted','功能组 → 功能点 → 测试点。可以先规划，再绑定实现代码。'));if(!query)empty.append(button('创建功能组',()=>edit(), 'targets-primary'),button('导入已有定义',importer));list.append(empty);}
    const diagnostics=panel.querySelector('.targets-diagnostics');diagnostics.replaceChildren();for(const [file,error]of Object.entries(data.errors)){const d=el('details');d.append(el('summary','',file+' · 无法校验'),el('pre','',error));diagnostics.append(d);}
  }
  function show(view){active=view==='验证目标';if(panel){panel.hidden=!active;if(active)refresh();}}
  document.addEventListener('flowcanvas-view',e=>show(e.detail));
  // Watch published metadata only; never scan source files on a timer.
  let checking=false;
  setInterval(async()=>{if(!active||!data||busy||checking||document.hidden)return;checking=true;try{const response=await fetch('/api/chat/projects/'+encodeURIComponent(project.id)+'/targets/revision');if(response.ok&&(await response.json()).revision!==data.revision)await refresh();}catch{}finally{checking=false;}},3000);
  fetch('/api/chat/projects').then(r=>r.json()).then(state=>{
    project=state.current;if(!project)return;panel=el('section','targets-panel');panel.hidden=true;
    const toolbar=el('div','targets-toolbar');const search=el('input','targets-search');search.placeholder='筛选验证目标…  /';search.setAttribute('aria-label','搜索验证目标');search.oninput=()=>{query=search.value.trim().toLowerCase();searchExpanded=true;if(data)render();};
    search.onkeydown=event=>{if(event.key==='Escape'){event.preventDefault();search.value='';query='';if(data)render();}else if(event.key==='ArrowDown'){event.preventDefault();panel.querySelector('.target-card-head')?.focus();}};
    document.addEventListener('keydown',event=>{if(active&&event.key==='/'&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!event.target.closest('input,textarea,select,[contenteditable="true"],dialog')){event.preventDefault();search.focus();}});
    toolbar.append(search);
    panel.append(toolbar,el('div','targets-summary'),el('p','targets-error'),el('div','targets-diagnostics'),el('div','targets-tree'));host.append(panel);show(document.getElementById('project-heading').textContent);
  }).catch(()=>{});
})();
