/** One central case workspace reuses the existing chat surface and tracks its visible context. */
(()=>{
  const pane=document.querySelector('.chat-pane'),bottom=document.createElement('div');bottom.className='case-chat-bottom';
  const chatSurface=document.createElement('div');chatSurface.className='case-chat-surface';chatSurface.append(...pane.children);
  const analysisPane=document.createElement('aside');analysisPane.className='case-wave-sidebar';analysisPane.hidden=true;analysisPane.setAttribute('aria-label','波形 AI 解读');bottom.append(chatSurface,analysisPane);
  const waveAnalyses=new Map();let sidebarMode='analysis',sidebarWaveKey=null;
  const top=document.createElement('section');top.className='case-view-area';top.hidden=true;top.ariaLabel='详情查看区域';
  window.FlowCanvasDetailIndex(top);
  const divider=document.createElement('div');divider.className='case-horizontal-divider';divider.tabIndex=0;divider.hidden=true;
  divider.setAttribute('role','separator');divider.ariaLabel='调整详情与右侧栏宽度';divider.setAttribute('aria-orientation','vertical');
  const context=document.createElement('div');context.className='case-view-context';context.hidden=true;context.setAttribute('role','status');
  bottom.querySelector('.composer').prepend(context);pane.append(top,divider,bottom);
  const toggle=document.getElementById('detail-toggle').cloneNode(true);toggle.id='case-chat-toggle';toggle.classList.add('case-chat-toggle');
  bottom.id='case-dedicated-chat';toggle.setAttribute('aria-controls',bottom.id);
  let current=null,view={},ratio=.6,chatCollapsed=true;
  const opened=new Map();
  let savedProject=null,restoreKey=null;
  /** Persist identities and tab order per project; source and detail data are always reloaded. */
  function saveTabs(){
    if(!savedProject)return;
    try{localStorage.setItem('flowcanvas.workspaceTabs:'+savedProject,JSON.stringify({
      active:restoreKey||(current?current.project.id+':'+current.testcase.id:null),
      tabs:[...opened.values()].filter(item=>item.project.id===savedProject).map(item=>({id:item.testcase.id,title:item.testcase.title||item.testcase.name,entity_type:item.testcase.entity_type||'case',instance:item.testcase.instance,coverage_metric:item.testcase.coverage_metric,coverage_file:item.testcase.coverage_file,preview:item.preview,view:item.view,waveSidebar:item.waveSidebar}))
    }));}catch{}
  }
  function restoreTab(item){
    if(!item)return;
    const {project,testcase,view}=item;
    document.dispatchEvent(new CustomEvent('flowcanvas-case-restore',{
      detail:{project_id:project.id,case_id:testcase.id,module_instance:testcase.instance,view}
    }));
  }
  /** Hydrate once, then activate the saved page only after its detail handler is ready. */
  function ready(project,entity){
    if(savedProject!==project.id){
      savedProject=project.id;
      try{
        const saved=JSON.parse(localStorage.getItem('flowcanvas.workspaceTabs:'+project.id)||'null');
        for(const item of Array.isArray(saved?.tabs)?saved.tabs:[]){
          if(typeof item?.id!=='string'||!['case','module','target','bug','coverage'].includes(item.entity_type))continue;
          opened.set(project.id+':'+item.id,{project,testcase:{id:item.id,title:String(item.title||item.id),entity_type:item.entity_type,instance:item.instance,coverage_metric:item.coverage_metric,coverage_file:item.coverage_file},preview:!!item.preview,view:item.view,waveSidebar:item.waveSidebar});
        }
        restoreKey=typeof saved?.active==='string'&&opened.has(saved.active)?saved.active:null;
      }catch{}
      paintTabs();
    }
    const pending=opened.get(restoreKey);
    if(pending&&(pending.testcase.entity_type||'case')===entity){
      restoreTab(pending);
    }
  }
  const tabs=document.createElement('nav');tabs.className='case-workspace-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','工作区标签页');
  const tabActions=document.createElement('div');tabActions.className='workspace-tab-actions';tabActions.setAttribute('role','group');tabActions.setAttribute('aria-label','侧边栏控制');
  const sidebarNav=document.querySelector('.detail-page-tabs'),sidebarHeader=sidebarNav.closest('header');
  const dedicated=document.createElement('div');dedicated.className='workspace-dedicated-label';dedicated.setAttribute('role','status');
  const dedicatedChip=document.createElement('span');dedicatedChip.className='workspace-dedicated-chip';dedicatedChip.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="7" cy="6" r="2.5"/><path d="M2.5 16v-1.5a4.5 4.5 0 0 1 9 0V16M12 6h4v4"/><circle cx="16" cy="13" r="2"/></svg><span class="workspace-dedicated-title">专属对话</span><span class="workspace-dedicated-dot" aria-hidden="true">·</span>';
  const dedicatedName=document.createElement('span');dedicatedName.className='workspace-dedicated-name';dedicatedChip.append(dedicatedName);dedicated.append(dedicatedChip);
  const waveNav=document.createElement('nav');waveNav.className='workspace-wave-nav';waveNav.hidden=true;waveNav.setAttribute('role','tablist');waveNav.setAttribute('aria-label','波形侧边栏');
  for(const [mode,label] of [['analysis','AI 解读'],['chat','专属对话']]){const button=document.createElement('button');button.type='button';button.textContent=label;if(mode==='chat'){button.className='workspace-dedicated-chip';button.innerHTML=dedicatedChip.innerHTML;}button.dataset.mode=mode;button.setAttribute('role','tab');button.onclick=()=>selectWaveSidebar(mode);button.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.stopPropagation();event.preventDefault();selectWaveSidebar(event.key==='Home'?'analysis':event.key==='End'?'chat':sidebarMode==='analysis'?'chat':'analysis');waveNav.querySelector('[aria-selected="true"]').focus();}};waveNav.append(button);}
  function selectWaveSidebar(mode){
    sidebarMode=mode;
    if(current&&view.tab==='wave'){
      const item=opened.get(current.project.id+':'+current.testcase.id);
      if(item)item.waveSidebar=mode;
      saveTabs();
    }
    syncSidebar();
  }
  /** Reuse the same sidebar boundary while swapping only its visible content. */
  function syncSidebar(){
    const entity=current?.testcase.entity_type;
    const wave=!!current&&view.tab==='wave'&&(!entity||entity==='case'||entity==='bug');
    const key=wave?current.project.id+':'+current.testcase.id+':'+(view.wave_case_id||'')+':'+(view.run_id||''):null;
    if(key&&key!==sidebarWaveKey){sidebarWaveKey=key;sidebarMode=opened.get(current.project.id+':'+current.testcase.id)?.waveSidebar==='chat'?'chat':'analysis';}
    if(!wave){sidebarWaveKey=null;sidebarMode='chat';}
    waveNav.querySelector('.workspace-dedicated-name').textContent=dedicatedName.textContent;
    waveNav.hidden=!wave||chatCollapsed;dedicated.hidden=wave;chatSurface.hidden=wave&&sidebarMode==='analysis';analysisPane.hidden=!wave||sidebarMode!=='analysis';
    for(const button of waveNav.children){const active=button.dataset.mode===sidebarMode;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;}
    if(wave){
      const content=waveAnalyses.get(current.project.id+':'+current.testcase.id);
      if(content){
        if(analysisPane.firstChild!==content)analysisPane.replaceChildren(content);
      }else{
        let empty=analysisPane.firstElementChild;
        if(empty?.dataset.placeholder!=='true'){
          empty=document.createElement('p');empty.className='case-detail-empty';empty.dataset.placeholder='true';
          analysisPane.replaceChildren(empty);
        }
        const message=entity==='bug'&&!view.wave_case_id?'选择关联测试用例后查看波形解读。':'正在读取波形解读…';
        if(empty.textContent!==message)empty.textContent=message;
      }
    }
  }
  tabActions.append(sidebarNav,dedicated,waveNav,document.getElementById('detail-toggle'),toggle);sidebarHeader.remove();tabs.append(tabActions);
  const tabBoundary=document.createElement('span');tabBoundary.className='workspace-tab-boundary';tabBoundary.setAttribute('aria-hidden','true');tabs.append(tabBoundary);
  document.body.prepend(tabs);
  /** Align the first tab with the content boundary as the project pane resizes or collapses. */
  function alignTabStart(){
    const dividerRect=document.querySelector('.splitter[data-split="0"]').getBoundingClientRect();
    // The selected tab's outer shoulder extends 8px left of its button edge.
    const offset=dividerRect.left+dividerRect.width/2-tabs.getBoundingClientRect().left+8;
    tabs.style.setProperty('--workspace-tabs-start',Math.max(54,offset)+'px');
    const brand=tabs.querySelector('.workspace-brand');if(brand)tabs.style.setProperty('--workspace-tab-boundary-left',(brand.getBoundingClientRect().right-tabs.getBoundingClientRect().left-8.5)+'px');
    tabs.style.setProperty('--workspace-sidebar-width',(current?bottom:document.querySelector('.detail-pane')).getBoundingClientRect().width+'px');
  }
  if(window.ResizeObserver){
    const observer=new ResizeObserver(alignTabStart);
    observer.observe(document.querySelector('.project-pane'));observer.observe(pane);observer.observe(document.querySelector('.detail-pane'));observer.observe(bottom);
  }else window.addEventListener('resize',alignTabStart);
  alignTabStart();
  /** Collapse the dedicated conversation without changing its session or saved split width. */
  function collapseChat(value,persist=false){
    chatCollapsed=value;pane.classList.toggle('case-chat-collapsed',value);
    bottom.inert=value&&!!current;divider.tabIndex=value?-1:0;
    toggle.title=(value?'展开':'收起')+(view.tab==='wave'?'右侧栏':current?.testcase.entity_type==='module'?'设计对话':current?.testcase.entity_type==='target'?'目标对话':current?.testcase.entity_type==='coverage'?'覆盖率对话':current?.testcase.entity_type==='bug'?'Bug 对话':'用例对话');toggle.setAttribute('aria-label',toggle.title);toggle.setAttribute('aria-expanded',String(!value));
    if(persist)window.FlowCanvasSidebarState?.save(value);
    syncSidebar();
  }
  toggle.onclick=()=>{globalThis.FlowCanvasSidebarMotion?.(pane,bottom,'case-chat-motion','case-chat-collapsed',!chatCollapsed);collapseChat(!chatCollapsed,true);};collapseChat(chatCollapsed);

  /** Keep the two regions usable and remember only explicit resize preferences. */
  function resize(value,persist=false){const available=Math.max(1,pane.clientWidth-7),width=window.FlowCanvasSidebarLayout.resize(available*(1-value),persist);ratio=1-width/available;divider.setAttribute('aria-valuenow',String(Math.round(ratio*100)));}
  divider.setAttribute('aria-valuemin','0');divider.setAttribute('aria-valuemax','100');
  document.addEventListener('flowcanvas-sidebar-width',event=>{ratio=1-event.detail.width/Math.max(1,pane.clientWidth-7);divider.setAttribute('aria-valuenow',String(Math.round(ratio*100)));});
  divider.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();divider.setPointerCapture(event.pointerId);const y=event.clientX,start=top.getBoundingClientRect().width,height=pane.clientWidth;document.body.classList.add('case-resizing');divider.onpointermove=move=>resize((start+move.clientX-y)/height);const finish=()=>{divider.onpointermove=null;document.body.classList.remove('case-resizing');resize(ratio,true);};divider.onpointerup=finish;divider.onpointercancel=finish;divider.onlostpointercapture=finish;};
  divider.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();resize(event.key==='Home'?.2:event.key==='End'?.8:ratio+(event.key==='ArrowRight'?.03:-.03),true);};
  divider.ondblclick=()=>resize(.6,true);
  /** Publish a small selection snapshot; the server supplies authoritative source and run metadata. */
  function publish(activate=false){if(!current)return;const label=({overview:'概览',source:'源码',runs:'运行记录',wave:'波形',coverage:'覆盖率'})[view.tab]||'概览';context.textContent='正在查看：'+(current.testcase.title||current.testcase.name)+' › '+label+(view.run_id?' › '+view.run_id.slice(0,8):'');document.dispatchEvent(new CustomEvent('flowcanvas-case-context',{detail:{...current,activate,...(current.testcase.entity_type==='coverage'?{testcase:{...current.testcase,id:'coverage_'+current.testcase.coverage_metric,title:current.testcase.title.split(' · ')[0]}}:{}),view:{...view}}}));}
  /** Restore the ordinary chat without interrupting any running case conversation. */
  function leave(){restoreKey=null;if(!current){paintTabs();return;}current=null;view={};pane.classList.remove('case-workspace-open','case-chat-collapsed','case-chat-motion');bottom.inert=false;top.hidden=divider.hidden=context.hidden=true;document.dispatchEvent(new CustomEvent('flowcanvas-case-close'));paintTabs();}
  /** Update keyed editor tabs without replacing click targets during a double click. */
  function paintTabs(){
    syncSidebar();
    let main=tabs.querySelector('.case-main-tab'),list=tabs.querySelector('.case-workspace-tab-scroll');
    if(!main){
      main=document.createElement('button');main.type='button';main.className='case-workspace-tab case-main-tab';main.innerHTML='<span class="case-workspace-tab-icon" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M3 3h10v8H7l-4 3zM5.5 6h5M5.5 8h3"/></svg></span>';main.title='主对话 · 固定标签页';main.setAttribute('aria-label','主对话（固定）');main.setAttribute('role','tab');main.onclick=leave;
      list=document.createElement('div');list.className='case-workspace-tab-scroll';const brand=document.createElement('span');brand.className='workspace-brand';brand.setAttribute('aria-label','UCAgent FlowCanvas');brand.innerHTML='<span class="workspace-brand-name">UC<span class="brand-accent">Agent</span></span><span class="workspace-brand-product">FlowCanvas</span>';brand.prepend(document.querySelector('.brand-home'));
      const projectToggle=document.createElement('button');projectToggle.type='button';projectToggle.id='project-sidebar-toggle';projectToggle.className='project-sidebar-toggle detail-toggle';projectToggle.setAttribute('aria-controls','project-content');
      projectToggle.innerHTML=document.getElementById('detail-toggle').innerHTML;
      const projectCollapsed=pane.closest('.workspace').classList.contains('project-collapsed');projectToggle.setAttribute('aria-expanded',String(!projectCollapsed));projectToggle.title=projectToggle.ariaLabel=projectCollapsed?'展开左侧栏':'收起左侧栏';
      projectToggle.onclick=()=>toggleProject(!pane.closest('.workspace').classList.contains('project-collapsed'));
      brand.append(projectToggle);tabs.prepend(brand,main,list);
    }
    main.setAttribute('aria-selected',String(!current));main.tabIndex=current?-1:0;
    for(const wrapper of [...list.children])if(!opened.has(wrapper.dataset.key))wrapper.remove();
    for(const [key,item] of opened){
      const active=current?.project.id===item.project.id&&current?.testcase.id===item.testcase.id;
      let wrapper=[...list.children].find(node=>node.dataset.key===key);
      if(!wrapper){
        wrapper=document.createElement('div');wrapper.className='case-workspace-tab-item';wrapper.dataset.key=key;
        const icon=document.createElement('span');icon.className='case-workspace-tab-icon';icon.setAttribute('aria-hidden','true');icon.innerHTML='<svg viewBox="0 0 16 16"><path d="M4 2h5l3 3v9H4zM9 2v4h3M6 9h4M6 11h3"/></svg>';
        const tab=document.createElement('button');tab.type='button';tab.className='case-workspace-tab';tab.setAttribute('role','tab');
        tab.onclick=()=>{if(current?.project.id!==item.project.id||current?.testcase.id!==item.testcase.id)restoreTab(opened.get(key));};
        tab.ondblclick=event=>{event.preventDefault();pinTab(key);};
        const close=document.createElement('button');close.type='button';close.className='case-workspace-tab-close';close.innerHTML='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg>';
        close.onclick=()=>{
          const keys=[...opened.keys()],index=keys.indexOf(key),isCurrent=current?.project.id===item.project.id&&current?.testcase.id===item.testcase.id;opened.delete(key);
          if(isCurrent){const next=opened.get(keys[index+1])||opened.get(keys[index-1]);if(next)restoreTab(next);else leave();}
          document.dispatchEvent(new CustomEvent('flowcanvas-case-tab-closed',{detail:{project_id:item.project.id,case_id:item.testcase.id}}));paintTabs();
        };
        const label=document.createElement('span');label.className='case-workspace-tab-label';tab.append(icon,label);wrapper.append(tab,close);list.append(wrapper);
      }
      wrapper.classList.toggle('is-active',active);wrapper.classList.toggle('is-preview',!!item.preview);
      const iconPath=({module:'M2 2h4v4H2zM10 10h4v4h-4zM2 10h4v4H2zM4 6v4M4 8h8v2',target:'M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M12 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0M8 6v4M6 8h4',bug:'M5 5h6v6a3 3 0 0 1-6 0zM6 5V4a2 2 0 0 1 4 0v1M2 6l3 1M11 7l3-1M2 10h3M11 10h3M3 14l2-2M11 12l2 2'})[item.testcase.entity_type]||'M4 2h5l3 3v9H4zM9 2v4h3M6 9h4M6 11h3';
      wrapper.querySelector('.case-workspace-tab-icon path').setAttribute('d',iconPath);
      const tab=wrapper.querySelector('[role="tab"]'),text=item.testcase.title||item.testcase.name;
      tab.querySelector('.case-workspace-tab-label').textContent=text;tab.title=text+(item.preview?' · 预览，双击固定':'');tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
      wrapper.querySelector('.case-workspace-tab-close').setAttribute('aria-label','关闭 '+text);
    }
    alignTabStart();list.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest',inline:'nearest'});saveTabs();
  }
  /** Keep an existing preview tab open; only explicit double clicks fix a case tab. */
  function pinTab(key){const item=opened.get(key);if(item?.preview){item.preview=false;paintTabs();}}
  tabs.onkeydown=event=>{
    if(event.target.getAttribute('role')!=='tab'||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();const buttons=[...tabs.querySelectorAll('.case-workspace-tab[role="tab"]')],index=buttons.indexOf(event.target);
    const next=event.key==='Home'?buttons[0]:event.key==='End'?buttons.at(-1):buttons[(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length];next.click();
    requestAnimationFrame(()=>{const active=tabs.querySelector('[aria-selected="true"]');active?.focus();});
  };
  paintTabs();
  document.addEventListener('flowcanvas-view',event=>{if(event.detail!=='测试用例'&&event.detail!=='设计总览'&&event.detail!=='验证目标'&&event.detail!=='Bug 管理')leave();});
  document.addEventListener('flowcanvas-case-ordinary',leave);
  document.addEventListener('flowcanvas-case-view',event=>{
    if(current?.testcase.id!==event.detail.case_id)return;
    view=event.detail.view;
    const item=opened.get(current.project.id+':'+current.testcase.id);
    if(item&&(!current.testcase.entity_type||['case','bug','target'].includes(current.testcase.entity_type))){
      const {tab,run_id,binding_id,wave_case_id}=view;
      const previous=item.view;
      if(previous?.tab!==tab||previous?.run_id!==run_id||previous?.binding_id!==binding_id||previous?.wave_case_id!==wave_case_id){
        item.view={tab,run_id,binding_id,wave_case_id};
        saveTabs();
      }
    }
    syncSidebar();
    publish();
  });
  document.addEventListener('flowcanvas-case-sidebar-chat',()=>{if(current){selectWaveSidebar('chat');collapseChat(false,true);}});
  window.FlowCanvasCaseWorkspace={
    ready,
    /** Dock the selected case's existing explanation node without copying its callbacks or state. */
    setWaveAnalysis(projectId,caseId,node){const key=projectId+':'+caseId;if(node){node.open=true;const heading=node.querySelector('summary');if(heading)heading.addEventListener('click',event=>{if(event.target===heading)event.preventDefault();});waveAnalyses.set(key,node);}else waveAnalyses.delete(key);syncSidebar();},
    /** Read saved tab identity without treating its cached metadata as source evidence. */
    tab(projectId,id){return opened.get(projectId+':'+id)?.testcase||(id.startsWith('coverage_')?[...opened.values()].find(item=>item.project.id===projectId&&item.testcase.entity_type==='coverage'&&item.testcase.coverage_metric===id.slice(9))?.testcase:undefined);},
    mount(detail){detail.hidden=!current||detail.dataset.entityId!==current.testcase.id;top.append(detail);},
    reconcile(project,cases,entity='case'){
      let changed=false;
      for(const [key,item] of opened){
        if(item.project.id!==project.id||(item.testcase.entity_type||'case')!==entity)continue;
        const node=cases.find(node=>node.id===item.testcase.id);
        if(!node){opened.delete(key);if(restoreKey===key)restoreKey=null;changed=true;document.dispatchEvent(new CustomEvent('flowcanvas-case-tab-closed',{detail:{project_id:project.id,case_id:item.testcase.id}}));}
        else if(node.title!==item.testcase.title||node.name!==item.testcase.name){opened.set(key,{...item,project,testcase:node});changed=true;}
      }
      if(changed)paintTabs();
    },
    pin(projectId,caseId){pinTab(projectId+':'+caseId);},
    open(project,testcase,initialView){
      const key=project.id+':'+testcase.id,existing=opened.get(key);
      if(!existing){
        for(const [oldKey,item] of opened){
          if(!item.preview||item.testcase.entity_type==='module')continue;
          opened.delete(oldKey);document.dispatchEvent(new CustomEvent('flowcanvas-case-tab-closed',{detail:{project_id:item.project.id,case_id:item.testcase.id}}));
        }
      }
      const changed=current?.testcase.id!==testcase.id||current?.project.id!==project.id;
      const sidebarState=window.FlowCanvasSidebarState?.enter('tab:'+(testcase.entity_type||'case')+':'+testcase.id,project.id);if(sidebarState)chatCollapsed=sidebarState.rightCollapsed;
      restoreKey=null;current={project,testcase};opened.set(key,{...existing,project,testcase,preview:existing?existing.preview:true});
      if(changed||testcase.entity_type==='module')view=initialView||{tab:'overview'};
      if(!testcase.entity_type||['case','bug','target'].includes(testcase.entity_type))opened.get(key).view=view;
      dedicatedName.textContent=testcase.entity_type==='coverage'?testcase.title.split(' · ')[0]:testcase.title||testcase.name;dedicated.title='专属对话 · '+dedicatedName.textContent;for(const detail of top.querySelectorAll('.testcase-detail-pane'))detail.hidden=detail.dataset.entityId!==testcase.id;pane.classList.add('case-workspace-open');collapseChat(chatCollapsed);top.hidden=divider.hidden=context.hidden=false;
      publish(true);ratio=1-window.FlowCanvasSidebarLayout.width/Math.max(1,pane.clientWidth-7);divider.setAttribute('aria-valuenow',String(Math.round(ratio*100)));paintTabs();
    },
    context(){return current?{...current,view:{...view}}:null;}
  };
})();
