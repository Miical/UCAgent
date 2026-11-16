/** One central case workspace reuses the existing chat surface and tracks its visible context. */
(()=>{
  const pane=document.querySelector('.chat-pane'),bottom=document.createElement('div');bottom.className='case-chat-bottom';
  bottom.append(...pane.children);
  const top=document.createElement('section');top.className='case-view-area';top.hidden=true;top.ariaLabel='详情查看区域';
  const divider=document.createElement('div');divider.className='case-horizontal-divider';divider.tabIndex=0;divider.hidden=true;
  divider.setAttribute('role','separator');divider.ariaLabel='调整用例详情与专属对话宽度';divider.setAttribute('aria-orientation','vertical');
  const context=document.createElement('div');context.className='case-view-context';context.hidden=true;context.setAttribute('role','status');
  bottom.querySelector('.composer').prepend(context);pane.append(top,divider,bottom);
  const identity=document.createElement('div');identity.className='case-conversation-identity';
  const label=document.createElement('strong');label.textContent='用例专属对话';const name=document.createElement('span');identity.append(label,name);bottom.prepend(identity);
  const toggle=document.getElementById('detail-toggle').cloneNode(true);toggle.id='case-chat-toggle';toggle.classList.add('case-chat-toggle');
  bottom.id='case-dedicated-chat';toggle.setAttribute('aria-controls',bottom.id);top.append(toggle);
  let current=null,view={},ratio=.6,chatCollapsed=false,motionTimer;
  const opened=new Map();
  const tabs=document.createElement('nav');tabs.className='case-workspace-tabs';tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','工作区标签页');
  pane.closest('.workspace').prepend(tabs);
  try{chatCollapsed=localStorage.getItem('flowcanvas.caseChatCollapsed')==='true';}catch{}
  /** Collapse the dedicated conversation without changing its session or saved split width. */
  function collapseChat(value,persist=false){
    chatCollapsed=value;pane.classList.toggle('case-chat-collapsed',value);
    bottom.inert=value&&!!current;divider.tabIndex=value?-1:0;
    toggle.title=(value?'展开':'收起')+(current?.testcase.entity_type==='module'?'设计对话':current?.testcase.entity_type==='target'?'目标对话':'用例对话');toggle.setAttribute('aria-label',toggle.title);toggle.setAttribute('aria-expanded',String(!value));
    if(persist)try{localStorage.setItem('flowcanvas.caseChatCollapsed',String(value));}catch{}
  }
  toggle.onclick=()=>{clearTimeout(motionTimer);pane.classList.add('case-chat-motion');collapseChat(!chatCollapsed,true);motionTimer=setTimeout(()=>pane.classList.remove('case-chat-motion'),500);};collapseChat(chatCollapsed);

  try{const saved=Number(localStorage.getItem('flowcanvas.caseHorizontalRatio'));if(saved>=.2&&saved<=.8)ratio=saved;}catch{}
  /** Keep the two regions usable and remember only explicit resize preferences. */
  function resize(value,persist=false){ratio=Math.max(.2,Math.min(.8,value));pane.style.setProperty('--case-view-width',(ratio*100)+'%');pane.style.setProperty('--case-view-track',(ratio*100)+'fr');pane.style.setProperty('--case-chat-track',((1-ratio)*100)+'fr');divider.setAttribute('aria-valuenow',String(Math.round(ratio*100)));if(persist)try{localStorage.setItem('flowcanvas.caseHorizontalRatio',String(ratio));}catch{}}
  resize(ratio);divider.setAttribute('aria-valuemin','20');divider.setAttribute('aria-valuemax','80');
  divider.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();divider.setPointerCapture(event.pointerId);const y=event.clientX,start=top.getBoundingClientRect().width,height=pane.clientWidth;document.body.classList.add('case-resizing');divider.onpointermove=move=>resize((start+move.clientX-y)/height);const finish=()=>{divider.onpointermove=null;document.body.classList.remove('case-resizing');resize(ratio,true);};divider.onpointerup=finish;divider.onpointercancel=finish;divider.onlostpointercapture=finish;};
  divider.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();resize(event.key==='Home'?.2:event.key==='End'?.8:ratio+(event.key==='ArrowRight'?.03:-.03),true);};
  divider.ondblclick=()=>resize(.6,true);
  /** Publish a small selection snapshot; the server supplies authoritative source and run metadata. */
  function publish(){if(!current)return;const label=({overview:'概览',source:'源码',runs:'运行记录',wave:'波形',coverage:'覆盖率'})[view.tab]||'概览';context.textContent='正在查看：'+(current.testcase.title||current.testcase.name)+' › '+label+(view.run_id?' › '+view.run_id.slice(0,8):'');document.dispatchEvent(new CustomEvent('flowcanvas-case-context',{detail:{...current,view:{...view}}}));}
  /** Restore the ordinary chat without interrupting any running case conversation. */
  function leave(){if(!current){paintTabs();return;}current=null;view={};pane.classList.remove('case-workspace-open','case-chat-collapsed','case-chat-motion');bottom.inert=false;top.hidden=divider.hidden=context.hidden=true;document.dispatchEvent(new CustomEvent('flowcanvas-case-close'));paintTabs();}
  /** Update keyed editor tabs without replacing click targets during a double click. */
  function paintTabs(){
    let main=tabs.querySelector('.case-main-tab'),list=tabs.querySelector('.case-workspace-tab-scroll');
    if(!main){
      main=document.createElement('button');main.type='button';main.className='case-workspace-tab case-main-tab';main.textContent='主对话';main.setAttribute('role','tab');main.onclick=leave;
      list=document.createElement('div');list.className='case-workspace-tab-scroll';tabs.append(main,list);
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
        tab.onclick=()=>{if(current?.project.id!==item.project.id||current?.testcase.id!==item.testcase.id)document.dispatchEvent(new CustomEvent('flowcanvas-case-restore',{detail:{project_id:item.project.id,case_id:item.testcase.id}}));};
        tab.ondblclick=event=>{event.preventDefault();pinTab(key);};
        const close=document.createElement('button');close.type='button';close.className='case-workspace-tab-close';close.textContent='×';
        close.onclick=()=>{
          const keys=[...opened.keys()],index=keys.indexOf(key),isCurrent=current?.project.id===item.project.id&&current?.testcase.id===item.testcase.id;opened.delete(key);
          if(isCurrent){const next=opened.get(keys[index+1])||opened.get(keys[index-1]);if(next)document.dispatchEvent(new CustomEvent('flowcanvas-case-restore',{detail:{project_id:next.project.id,case_id:next.testcase.id}}));else leave();}
          document.dispatchEvent(new CustomEvent('flowcanvas-case-tab-closed',{detail:{project_id:item.project.id,case_id:item.testcase.id}}));paintTabs();
        };
        const label=document.createElement('span');label.className='case-workspace-tab-label';tab.append(icon,label);wrapper.append(tab,close);list.append(wrapper);
      }
      wrapper.classList.toggle('is-active',active);wrapper.classList.toggle('is-preview',!!item.preview);
      const tab=wrapper.querySelector('[role="tab"]'),text=item.testcase.title||item.testcase.name;
      tab.querySelector('.case-workspace-tab-label').textContent=text;tab.title=text+(item.preview?' · 预览，双击固定':'');tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
      wrapper.querySelector('.case-workspace-tab-close').setAttribute('aria-label','关闭 '+text);
    }
    list.querySelector('[aria-selected="true"]')?.scrollIntoView({block:'nearest',inline:'nearest'});
  }
  /** Keep an existing preview tab open; only explicit double clicks fix a case tab. */
  function pinTab(key){const item=opened.get(key);if(item?.preview){item.preview=false;paintTabs();}}
  tabs.onkeydown=event=>{
    if(event.target.getAttribute('role')!=='tab'||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();const buttons=[...tabs.querySelectorAll('[role="tab"]')],index=buttons.indexOf(event.target);
    const next=event.key==='Home'?buttons[0]:event.key==='End'?buttons.at(-1):buttons[(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length];next.click();
    requestAnimationFrame(()=>{const active=tabs.querySelector('[aria-selected="true"]');active?.focus();});
  };
  paintTabs();
  document.addEventListener('flowcanvas-view',event=>{if(event.detail!=='测试用例'&&event.detail!=='设计总览'&&event.detail!=='验证目标')leave();});
  document.addEventListener('flowcanvas-case-ordinary',leave);
  document.addEventListener('flowcanvas-case-view',event=>{if(current?.testcase.id!==event.detail.case_id)return;view=event.detail.view;publish();});
  window.FlowCanvasCaseWorkspace={
    mount(detail){top.append(detail);},
    reconcile(project,cases,entity='case'){
      let changed=false;
      for(const [key,item] of opened){
        if(item.project.id!==project.id||(item.testcase.entity_type||'case')!==entity)continue;
        const node=cases.find(node=>node.id===item.testcase.id);
        if(!node){opened.delete(key);changed=true;document.dispatchEvent(new CustomEvent('flowcanvas-case-tab-closed',{detail:{project_id:project.id,case_id:item.testcase.id}}));}
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
      current={project,testcase};opened.set(key,{project,testcase,preview:existing?existing.preview:true});
      name.textContent=testcase.title||testcase.name;label.textContent=testcase.entity_type==='module'?'设计专属对话':testcase.entity_type==='target'?'验证目标专属对话':'用例专属对话';for(const detail of top.querySelectorAll('.testcase-detail-pane'))detail.hidden=detail.dataset.entityId!==testcase.id;pane.classList.add('case-workspace-open');collapseChat(chatCollapsed);top.hidden=divider.hidden=context.hidden=false;
      if(changed||testcase.entity_type==='module')view=initialView||{tab:'overview'};publish();paintTabs();
    },
    context(){return current?{...current,view:{...view}}:null;}
  };
})();
