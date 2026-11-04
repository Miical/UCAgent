/* Embed the source-built viewer and supply the current project view.json URL. */
(()=>{
  const host=document.getElementById('project-content');
  let panel=null,project=null,loading=false;
  async function load(){
    if(loading||!project)return;loading=true;
    const status=panel.querySelector('.rtl-view-status'),frame=panel.querySelector('iframe');
    status.hidden=false;status.textContent='正在解析 RTL 结构…';
    const url='/api/chat/projects/'+encodeURIComponent(project.id)+'/rtl-view';
    try{
      const response=await fetch(url);const payload=await response.json();if(!response.ok)throw Error(payload.detail||'RTL 解析失败');
      frame.src='/rtl-viewer/?view='+encodeURIComponent(url)+'&reload='+Date.now();frame.hidden=false;status.hidden=true;
    }catch(e){status.textContent=e.message;frame.hidden=true;}finally{loading=false;}
  }
  function show(view){
    if(!project||!panel)return;
    const visible=view==='设计总览';panel.hidden=!visible;host.classList.toggle('has-rtl-view',visible);
  }
  document.addEventListener('flowcanvas-view',event=>show(event.detail));
  fetch('/api/chat/projects').then(r=>r.json()).then(state=>{
    project=state.current;if(!project)return;
    panel=document.createElement('section');panel.className='rtl-view-panel';
    const status=document.createElement('p');status.className='rtl-view-status';const frame=document.createElement('iframe');frame.title='RTL 结构查看器 · rtl-buddy-sch';frame.hidden=true;
    panel.append(status,frame);host.append(panel);show(document.getElementById('project-heading').textContent);load();
  }).catch(()=>{});
})();
