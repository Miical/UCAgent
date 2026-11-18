/** Keep one in-page index for the visible detail tab. */
window.FlowCanvasDetailIndex=function(workspace){
  const create=(tag,className,text)=>{
    const node=document.createElement(tag);
    node.className=className;
    if(text!==undefined)node.textContent=text;
    return node;
  };
  const index=create('div','detail-index');
  const handle=create('button','detail-index-handle');
  handle.type='button';
  handle.ariaLabel='页内索引';
  handle.setAttribute('aria-controls','detail-index-navigation');
  handle.setAttribute('aria-expanded','false');
  const nav=create('nav','detail-index-navigation');
  nav.id='detail-index-navigation';
  nav.ariaLabel='页内索引';
  nav.setAttribute('aria-hidden','true');
  nav.inert=true;
  const list=create('ul','detail-index-list');
  nav.append(list);
  index.append(handle,nav);
  index.hidden=true;
  workspace.append(index);

  let panel=null,entries=[],currentEntry=null,scheduled=false,roomy=false,hideTimer;
  const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');

  function setOpen(open,animate=false){
    clearTimeout(hideTimer);
    open=roomy||open;
    if(roomy)index.classList.remove('has-motion');
    if(open===!nav.inert)return;
    index.classList.toggle('has-motion',animate&&!roomy);
    index.classList.toggle('is-open',open);
    handle.setAttribute('aria-expanded',String(open));
    nav.inert=!open;
    nav.setAttribute('aria-hidden',String(!open));
    if(open&&currentEntry)revealEntry(currentEntry);
  }

  function revealEntry(entry){
    const link=entry.link.getBoundingClientRect(),viewport=list.getBoundingClientRect();
    list.scrollTop+=Math.min(0,link.top-viewport.top)+Math.max(0,link.bottom-viewport.bottom);
  }

  function markCurrent(entry){
    if(currentEntry!==entry){
      currentEntry?.link.removeAttribute('aria-current');
      entry.link.setAttribute('aria-current','location');
      currentEntry=entry;
      if(!nav.inert)revealEntry(entry);
    }
  }

  function highlight(){
    if(!panel||!entries.length)return;
    let current=entries[0];
    const threshold=panel.getBoundingClientRect().top+24;
    for(const entry of entries){
      if(entry.heading.getBoundingClientRect().top>threshold)break;
      current=entry;
    }
    if(panel.scrollHeight-panel.clientHeight>2&&panel.scrollHeight-panel.clientHeight-panel.scrollTop<=2){
      current=entries.at(-1);
    }
    markCurrent(current);
  }

  function jump(entry){
    const section=entry.heading.parentElement;
    if(section instanceof HTMLDetailsElement)section.open=true;
    const offset=entry.heading.getBoundingClientRect().top-panel.getBoundingClientRect().top;
    if(entry.heading.tagName!=='SUMMARY')entry.heading.tabIndex=-1;
    entry.heading.focus({preventScroll:true});
    setOpen(false);
    markCurrent(entry);
    panel.scrollTo({top:panel.scrollTop+offset-12,behavior:reducedMotion.matches?'instant':'smooth'});
  }

  function refresh(){
    scheduled=false;
    const detail=workspace.querySelector(':scope > .testcase-detail-pane:not([hidden])');
    const body=detail?.querySelector(':scope > .case-detail-body:not([hidden],[data-tab="wave"])');
    const next=body?.querySelector(':scope > .coverage-code-body')||body;
    const headings=[...(next?.querySelectorAll('details.module-section > summary, .case-section > h4.module-section-heading')||[])]
      .filter(heading=>heading.getClientRects().length)
      .map(heading=>({heading,label:(heading.querySelector(':scope > span')?.textContent||heading.textContent).trim()}))
      .filter(entry=>entry.label);
    const repeated=new Map();
    for(const {label} of headings)repeated.set(label,(repeated.get(label)||0)+1);
    for(const entry of headings){
      const file=entry.heading.querySelector(':scope > code')?.textContent;
      if(file&&repeated.get(entry.label)>1)entry.label+=' · '+file;
    }
    index.hidden=workspace.hidden||headings.length<2;
    if(index.hidden){
      roomy=false;
      workspace.style.removeProperty('--detail-content-shift');
      setOpen(false);
      return;
    }
    if(panel!==next||headings.length!==entries.length||headings.some((entry,i)=>entry.heading!==entries[i].heading||entry.label!==entries[i].label)){
      if(panel!==next)setOpen(false);
      panel=next;
      entries=headings;
      list.replaceChildren(...entries.map((entry,position)=>{
        const row=create('li','detail-index-row');
        entry.link=create('button','detail-index-link');
        entry.link.type='button';
        entry.link.title=entry.label;
        entry.link.onclick=()=>jump(entry);
        const label=create('span','detail-index-label',entry.label);
        label.style.setProperty('--detail-index-label-delay',Math.min(position,5)*15+'ms');
        entry.link.append(label);
        row.append(entry.link);
        return row;
      }));
    }

    const bounds=workspace.getBoundingClientRect();
    const bodyBounds=panel.getBoundingClientRect();
    const {paddingLeft,paddingRight}=getComputedStyle(panel);
    const gutter=(parseFloat(paddingLeft)+parseFloat(paddingRight))/2;
    const expandedWidth=parseFloat(getComputedStyle(index).getPropertyValue('--detail-index-expanded-width'));
    const gap=24;
    const hasSpace=gutter>=expandedWidth+gap;
    const shift=hasSpace?(expandedWidth+gap)/2:0;
    workspace.style.setProperty('--detail-content-shift',shift+'px');
    if(roomy!==hasSpace){
      roomy=hasSpace;
      setOpen(index.matches(':hover, :focus-within'));
    }
    handle.hidden=roomy;
    index.classList.toggle('has-space',roomy);
    const scrollEdge=bodyBounds.left+panel.clientLeft+panel.clientWidth;
    const scrollbarSpace=Math.max(0,bounds.right-scrollEdge);
    const inset=roomy?Math.max(10,gutter+shift-expandedWidth-gap):10;
    index.style.setProperty('--detail-index-edge',(scrollbarSpace+inset)+'px');
    index.style.setProperty('--detail-index-top',(bodyBounds.top-bounds.top)+'px');
    index.style.setProperty('--detail-index-height',bodyBounds.height+'px');
    highlight();
  }

  function schedule(){
    if(!scheduled){
      scheduled=true;
      requestAnimationFrame(refresh);
    }
  }

  index.onpointerenter=event=>{if(event.pointerType==='mouse')setOpen(true,true);};
  index.onpointerleave=event=>{
    if(event.pointerType==='mouse'){
      hideTimer=setTimeout(()=>{if(!index.querySelector(':focus-visible'))setOpen(false,true);},150);
    }
  };
  index.addEventListener('focusin',event=>{
    if(event.target!==handle||handle.matches(':focus-visible'))setOpen(true);
  });
  index.addEventListener('focusout',event=>{if(!index.contains(event.relatedTarget)&&!index.matches(':hover'))setOpen(false);});
  index.onkeydown=event=>{
    if(event.key==='Escape'&&!roomy&&!nav.inert){
      event.preventDefault();
      event.stopPropagation();
      handle.focus();
      setOpen(false);
    }
  };
  handle.onclick=()=>{
    setOpen(true);
    (currentEntry||entries[0])?.link.focus({preventScroll:true});
  };
  document.addEventListener('pointerdown',event=>{if(!nav.inert&&!index.contains(event.target))setOpen(false);},true);
  workspace.addEventListener('scroll',event=>{if(event.target===panel)highlight();},{capture:true,passive:true});
  new MutationObserver(changes=>{
    if(changes.some(({target})=>{
      const element=target instanceof Element?target:target.parentElement;
      return !index.contains(target)&&!element?.closest('.case-detail-log,.case-detail-source,.wave-panel');
    }))schedule();
  }).observe(workspace,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['hidden','open']});
  new ResizeObserver(schedule).observe(workspace);
};
