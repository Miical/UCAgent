/* Layout-only shell. No project, agent or workflow is started here. */
"use strict";
UCAgentShell.init({actionsSelector:'#theme-actions'});
const settings=document.getElementById('settings-panel');
const toggle=document.getElementById('settings-toggle');
function closeSettings(){settings.hidden=true;toggle.setAttribute('aria-expanded','false');}
toggle.onclick=()=>{settings.hidden=!settings.hidden;toggle.setAttribute('aria-expanded',String(!settings.hidden));};
document.addEventListener('click',event=>{if(!settings.contains(event.target)&&!toggle.contains(event.target))closeSettings();});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){closeSettings();toggle.focus();}});
for(const button of document.querySelectorAll('[data-view]')){
  button.onclick=()=>{
    for(const item of document.querySelectorAll('[data-view]')){item.classList.toggle('active',item===button);if(item===button)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');}
    document.getElementById('project-heading').textContent=button.dataset.view;
    document.getElementById('project-content').setAttribute('aria-label',button.dataset.view+'内容');
  };
}
for(const group of document.querySelectorAll('[role="tablist"]')){
  const tabs=[...group.querySelectorAll('[role="tab"]')];
  function select(tab){for(const item of tabs){item.setAttribute('aria-selected',String(item===tab));item.tabIndex=item===tab?0:-1;}document.getElementById(tab.getAttribute('aria-controls')).setAttribute('aria-labelledby',tab.id);}
  for(const tab of tabs){tab.onclick=()=>select(tab);tab.onkeydown=event=>{const i=tabs.indexOf(tab);let next;if(event.key==='ArrowRight')next=tabs[(i+1)%tabs.length];if(event.key==='ArrowLeft')next=tabs[(i+tabs.length-1)%tabs.length];if(event.key==='Home')next=tabs[0];if(event.key==='End')next=tabs.at(-1);if(next){event.preventDefault();select(next);next.focus();}};}
}
const workspace=document.querySelector('.workspace');
let widths=[25,45,30];
try{const stored=JSON.parse(localStorage.getItem('flowcanvas.layout'));if(Array.isArray(stored)&&stored.length===3&&stored.every(v=>Number.isFinite(v)&&v>=15)&&Math.abs(stored.reduce((a,b)=>a+b,0)-100)<.01)widths=stored;}catch(_){}
function paint(){['project','chat','detail'].forEach((name,i)=>workspace.style.setProperty('--'+name,widths[i]+'fr'));document.querySelectorAll('.splitter').forEach((el,i)=>el.setAttribute('aria-valuenow',String(Math.round(i?widths[0]+widths[1]:widths[0]))));}
function save(){try{localStorage.setItem('flowcanvas.layout',JSON.stringify(widths));}catch(_){}}
function move(index,change,initial){const sum=initial[index]+initial[index+1];const value=Math.max(18,Math.min(sum-20,initial[index]+change));widths=[...initial];widths[index]=value;widths[index+1]=sum-value;paint();}
for(const splitter of document.querySelectorAll('.splitter')){
  const index=Number(splitter.dataset.split);
  splitter.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();splitter.setPointerCapture(event.pointerId);const initial=[...widths],x=event.clientX;document.body.classList.add('resizing');splitter.onpointermove=e=>move(index,(e.clientX-x)/(workspace.clientWidth-14)*100,initial);const finish=()=>{splitter.onpointermove=null;document.body.classList.remove('resizing');save();};splitter.onpointerup=finish;splitter.onpointercancel=finish;splitter.onlostpointercapture=finish;};
  splitter.onkeydown=event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();move(index,event.key==='ArrowLeft'?-1:1,widths);save();}};
  splitter.ondblclick=()=>{widths=[25,45,30];paint();save();};
}
paint();
