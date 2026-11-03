/* Layout-only shell. No project, agent or workflow is started here. */
"use strict";
UCAgentShell.init({actionsSelector:'#theme-actions'});
// FlowCanvas defaults to Light independently of other UCAgent pages.
let preferredTheme='light';
try{const stored=localStorage.getItem('flowcanvas.theme');if(UCAgentShell.themes[stored])preferredTheme=stored;}catch{}
UCAgentShell.applyTheme(preferredTheme,false);
UCAgentShell.onThemeChange(name=>{try{localStorage.setItem('flowcanvas.theme',name);}catch{}});
const lightButton=document.querySelector('#theme-actions button[data-theme="light"]');
if(lightButton)lightButton.parentElement.prepend(lightButton);
const settings=document.getElementById('settings-panel');
const toggle=document.getElementById('settings-toggle');
function closeSettings(){settings.hidden=true;toggle.setAttribute('aria-expanded','false');}
toggle.onclick=()=>{settings.hidden=!settings.hidden;toggle.setAttribute('aria-expanded',String(!settings.hidden));};
document.addEventListener('click',event=>{if(!settings.contains(event.target)&&!toggle.contains(event.target))closeSettings();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeSettings();toggle.focus();}});
for(const button of document.querySelectorAll('[data-view]')){
  button.onclick=()=>{
    const wasActive=button.classList.contains('active');
    toggleProject(wasActive&&!workspace.classList.contains('project-collapsed'));
    for(const item of document.querySelectorAll('[data-view]')){item.classList.toggle('active',item===button);if(item===button)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');}
    document.getElementById('project-heading').textContent=button.dataset.view;
    for(const item of document.querySelectorAll('[data-view]'))item.setAttribute('aria-expanded',String(item===button&&!workspace.classList.contains('project-collapsed')));
    document.getElementById('project-content').setAttribute('aria-label',button.dataset.view+'内容');
    document.dispatchEvent(new CustomEvent('flowcanvas-view',{detail:button.dataset.view}));
  };
}
for(const group of document.querySelectorAll('[role="tablist"]')){
  const tabs=[...group.querySelectorAll('[role="tab"]')];
  function select(tab){for(const item of tabs){item.setAttribute('aria-selected',String(item===tab));item.tabIndex=item===tab?0:-1;}document.getElementById(tab.getAttribute('aria-controls')).setAttribute('aria-labelledby',tab.id);}
  for(const tab of tabs){tab.onclick=()=>select(tab);tab.onkeydown=event=>{const i=tabs.indexOf(tab);let next;if(event.key==='ArrowRight')next=tabs[(i+1)%tabs.length];if(event.key==='ArrowLeft')next=tabs[(i+tabs.length-1)%tabs.length];if(event.key==='Home')next=tabs[0];if(event.key==='End')next=tabs.at(-1);if(next){event.preventDefault();select(next);next.focus();}};}
}
const workspace=document.querySelector('.workspace');
let widths=[25,45,30];
try{const stored=JSON.parse(localStorage.getItem('flowcanvas.layout'));if(Array.isArray(stored)&&stored.length===3&&stored.every(v=>Number.isFinite(v)&&v>0)){const total=stored.reduce((a,b)=>a+b,0);if(Number.isFinite(total))widths=stored.map(v=>v/total*100);}}catch(_){}
function paint(){['project','chat','detail'].forEach((name,i)=>workspace.style.setProperty('--'+name,widths[i]+'fr'));document.querySelectorAll('.splitter').forEach((el,i)=>el.setAttribute('aria-valuenow',String(Math.round(i?widths[0]+widths[1]:widths[0]))));}
const collapsedKeys=['--collapsed-project','--collapsed-chat','--left-hidden-chat','--left-hidden-detail'];
try{const saved=JSON.parse(localStorage.getItem('flowcanvas.collapsedWidths')||'{}');for(const key of collapsedKeys){const value=saved[key];if(typeof value==='number'&&Number.isFinite(value)&&value>0)workspace.style.setProperty(key,value+'fr');}}catch{}
/** Persist expanded and collapsed layouts independently so either view survives reload. */
function save(){try{localStorage.setItem('flowcanvas.layout',JSON.stringify(widths));const values={};for(const key of collapsedKeys){const value=parseFloat(workspace.style.getPropertyValue(key));if(Number.isFinite(value)&&value>0)values[key]=value;}localStorage.setItem('flowcanvas.collapsedWidths',JSON.stringify(values));}catch(_){}}
function move(index,change,initial){if(workspace.classList.contains('project-collapsed')){if(index!==1)return;const available=workspace.clientWidth-7;const total=initial[1]+initial[2];const value=Math.max(500/available*100,Math.min(total-190/available*100,initial[1]+change));workspace.style.setProperty('--left-hidden-chat',value+'fr');workspace.style.setProperty('--left-hidden-detail',(total-value)+'fr');return;}if(workspace.classList.contains('detail-collapsed')){const total=initial[0]+initial[1];const available=workspace.clientWidth-((workspace.classList.contains('detail-collapsed')||workspace.classList.contains('project-collapsed'))?7:14);const value=Math.max(230/available*100,Math.min(total-500/available*100,initial[0]+change));workspace.style.setProperty('--project-track',(value/100*available)+'px');workspace.style.setProperty('--collapsed-project',value+'fr');workspace.style.setProperty('--collapsed-chat',(total-value)+'fr');return;}const available=workspace.clientWidth-((workspace.classList.contains('detail-collapsed')||workspace.classList.contains('project-collapsed'))?7:14);const minimum=[230,500,190].map(value=>value/available*100);const sum=initial[index]+initial[index+1];const value=Math.max(minimum[index],Math.min(sum-minimum[index+1],initial[index]+change));widths=[...initial];widths[index]=value;widths[index+1]=sum-value;workspace.style.setProperty('--project-track',(widths[0]/100*available)+'px');paint();}
for(const splitter of document.querySelectorAll('.splitter')){
  const index=Number(splitter.dataset.split);
  splitter.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();splitter.setPointerCapture(event.pointerId);const available=workspace.clientWidth-((workspace.classList.contains('detail-collapsed')||workspace.classList.contains('project-collapsed'))?7:14);const initial=[...workspace.querySelectorAll(':scope > .pane')].map(pane=>pane.getBoundingClientRect().width/available*100),x=event.clientX;document.body.classList.add('resizing');splitter.onpointermove=e=>move(index,(e.clientX-x)/(workspace.clientWidth-((workspace.classList.contains('detail-collapsed')||workspace.classList.contains('project-collapsed'))?7:14))*100,initial);const finish=()=>{splitter.onpointermove=null;document.body.classList.remove('resizing');save();};splitter.onpointerup=finish;splitter.onpointercancel=finish;splitter.onlostpointercapture=finish;};
  splitter.onkeydown=event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const initial=[...workspace.querySelectorAll(':scope > .pane')].map(pane=>pane.getBoundingClientRect().width/(workspace.clientWidth-((workspace.classList.contains('detail-collapsed')||workspace.classList.contains('project-collapsed'))?7:14))*100);move(index,event.key==='ArrowLeft'?-1:1,initial);save();}};
  splitter.ondblclick=()=>{workspace.style.removeProperty('--project-track');widths=[25,45,30];for(const key of collapsedKeys)workspace.style.removeProperty(key);paint();save();};
}
paint();

/** Toggle the detail pane while preserving the expanded column proportions. */
function toggleDetails(collapsed){
  // Preserve the project pane when only the detail pane is changing.
  if(!workspace.classList.contains('project-collapsed')){
    const width=document.querySelector('.project-pane').getBoundingClientRect().width;
    workspace.style.setProperty('--project-track',width+'px');
  }
  workspace.classList.toggle('detail-collapsed',collapsed);
  document.querySelector('.detail-pane').inert=collapsed;
  document.querySelector('.splitter[data-split="1"]').tabIndex=collapsed?-1:0;
  const button=document.getElementById('detail-toggle');
  button.setAttribute('aria-expanded',String(!collapsed));button.title=button.ariaLabel=collapsed?'展开右侧栏':'收起右侧栏';
  try{localStorage.setItem('flowcanvas.detailCollapsed',String(collapsed));}catch{}
}
let detailMotionTimer;
document.getElementById('detail-toggle').onclick=()=>{
  clearTimeout(detailMotionTimer);workspace.classList.add('detail-motion');
  toggleDetails(!workspace.classList.contains('detail-collapsed'));
  detailMotionTimer=setTimeout(()=>workspace.classList.remove('detail-motion'),500);
};
try{if(localStorage.getItem('flowcanvas.detailCollapsed')==='true')toggleDetails(true);}catch{}

/** Collapse the project column from its active navigation icon, keeping navigation visible. */
let projectMotionTimer;
function toggleProject(collapsed,animate=true){
  if(animate){clearTimeout(projectMotionTimer);workspace.classList.add('project-motion');projectMotionTimer=setTimeout(()=>workspace.classList.remove('project-motion'),500);}
  workspace.classList.toggle('project-collapsed',collapsed);
  document.querySelector('.project-pane').inert=collapsed;
  document.querySelector('.splitter[data-split="0"]').tabIndex=collapsed?-1:0;
  for(const button of document.querySelectorAll('[data-view]')){button.setAttribute('aria-expanded',String(!collapsed&&button.dataset.view===document.getElementById('project-heading').textContent));button.setAttribute('aria-controls','project-content');}
  try{localStorage.setItem('flowcanvas.projectCollapsed',String(collapsed));}catch{}
}
try{toggleProject(localStorage.getItem('flowcanvas.projectCollapsed')==='true',false);}catch{}

/** Return to the initial workbench view without reloading or interrupting conversations. */
document.querySelector('.brand-home').onclick=event=>{
  if(event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  event.preventDefault();closeSettings();
  document.getElementById('project-heading').textContent='验证进度';
  document.getElementById('project-content').setAttribute('aria-label','验证进度内容');
  for(const button of document.querySelectorAll('[data-view]')){
    const selected=button.dataset.view==='验证进度';button.classList.toggle('active',selected);
    if(selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
    button.setAttribute('aria-expanded',String(selected&&!workspace.classList.contains('project-collapsed')));
  }
  document.dispatchEvent(new CustomEvent('flowcanvas-home'));
  document.dispatchEvent(new CustomEvent('flowcanvas-view',{detail:'验证进度'}));
};

// Workflow navigation opens details without toggling an already visible pane.
document.addEventListener('flowcanvas-open-workflow',()=>{
  if(workspace.classList.contains('detail-collapsed')){
    clearTimeout(detailMotionTimer);workspace.classList.add('detail-motion');toggleDetails(false);
    detailMotionTimer=setTimeout(()=>workspace.classList.remove('detail-motion'),500);
  }
});

// Persist navigation independently from pane sizes and collapsed state.
document.addEventListener('flowcanvas-view',event=>{
  if(![...document.querySelectorAll('[data-view]')].some(button=>button.dataset.view===event.detail))return;
  try{localStorage.setItem('flowcanvas.activeView',event.detail);}catch{}
});
try{
  const saved=localStorage.getItem('flowcanvas.activeView');
  const buttons=[...document.querySelectorAll('[data-view]')];
  if(buttons.some(button=>button.dataset.view===saved)){
    document.getElementById('project-heading').textContent=saved;
    document.getElementById('project-content').setAttribute('aria-label',saved+'内容');
    for(const button of buttons){
      const selected=button.dataset.view===saved;
      button.classList.toggle('active',selected);
      if(selected)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');
      button.setAttribute('aria-expanded',String(selected&&!workspace.classList.contains('project-collapsed')));
    }
    // Panels loaded after this script initialize from project-heading.
    document.dispatchEvent(new CustomEvent('flowcanvas-view',{detail:saved}));
  }
}catch{}
