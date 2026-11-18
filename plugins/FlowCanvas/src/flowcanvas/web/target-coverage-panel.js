/** Show the metrics reported by each coverage backend alongside the shared target tree. */
window.FlowCanvasTargetCoveragePanel=function({panel,project,content,refresh,fail}){
  const vcsMetrics=[['functional','功能覆盖率'],['line','行覆盖率'],['toggle','翻转覆盖率'],['branch','分支覆盖率'],['cond','条件覆盖率'],['fsm','状态机覆盖率'],['assert','断言']];
  const toffeeMetrics=[...vcsMetrics.slice(0,-1),['expr','表达式覆盖率']];
  const el=(tag,cls='',text)=>{const node=document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const button=(text,callback,cls='')=>{const node=el('button',cls,text);node.type='button';node.onclick=callback;return node;};
  const overview=el('div','target-coverage-overview'),actions=el('div','coverage-overview-actions');
  const runs=el('span','coverage-shared-runs');
  const menu=el('details','coverage-overview-menu'),trigger=el('summary','','⋯');
  trigger.title='覆盖率操作';trigger.setAttribute('aria-label','覆盖率操作');
  const tabs=el('nav','target-coverage-tabs');tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','覆盖率类型');
  const functionalPane=el('div','target-goal-pane'),details=el('div','target-line-pane');
  const frame=el('iframe');frame.title='代码覆盖率明细';
  const filesPane=el('div','toffee-coverage-files');
  const status=el('p','coverage-overview-status');status.setAttribute('role','status');
  functionalPane.setAttribute('role','tabpanel');functionalPane.append(...content);
  details.setAttribute('role','tabpanel');details.append(frame,filesPane);
  let snapshot=null,isToffee=false,metrics=vcsMetrics,preferred='functional',active='functional';
  const preference='fc-target-coverage-tab:'+project.id;
  try{preferred=localStorage.getItem(preference)||'functional';}catch{}
  const endpoint='/api/chat/projects/'+encodeURIComponent(project.id)+'/coverage';
  const reset=button('清空累计',async()=>{
    const dialog=document.getElementById('coverage-reset-dialog');
    if(dialog.open)return;
    menu.open=false;
    dialog.querySelector('#coverage-reset-description').textContent=isToffee
      ?'从下一次运行开始重新统计。历史运行、覆盖率产物和日志会保留。'
      :'清空后会删除共享覆盖率数据库，从新运行重新累计。历史运行和日志会保留。';
    dialog.returnValue='';
    dialog.showModal();
    await new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));
    trigger.focus();
    if(dialog.returnValue!=='reset')return;
    try{
      const response=await fetch(endpoint+'/reset',{method:'POST'});
      if(!response.ok)throw Error(response.status===409?'测试运行中，请结束后再清空累计':'清空失败');
      await refresh();if(frame.src)frame.contentWindow.location.reload();
    }catch(error){fail(error);}
  },'coverage-menu-reset');
  menu.append(trigger,reset);actions.append(runs,menu);
  overview.append(actions,status);panel.append(overview,tabs,functionalPane,details);
  menu.addEventListener('focusout',()=>setTimeout(()=>{if(!menu.contains(document.activeElement))menu.open=false;},0));
  menu.onkeydown=event=>{if(event.key==='Escape'){menu.open=false;trigger.focus();}};
  tabs.onkeydown=event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    const buttons=[...tabs.children],index=buttons.indexOf(document.activeElement);
    const next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowRight'?1:-1)+buttons.length)%buttons.length;
    buttons[next].focus();choose(buttons[next].dataset.metric);
  };

  function choose(mode){
    preferred=mode;
    try{localStorage.setItem(preference,mode);}catch{}
    activate();
  }

  function activate(){
    active=metrics.some(([key])=>key===preferred)?preferred:'functional';
    functionalPane.hidden=active!=='functional';details.hidden=active==='functional';
    for(const tab of tabs.children){const selected=tab.dataset.metric===active;tab.classList.toggle('active',selected);tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;}
    for(const card of overview.querySelectorAll('.coverage-overview-item'))card.setAttribute('aria-pressed',String(card.dataset.metric===active));
    frame.hidden=isToffee;filesPane.hidden=!isToffee;
    if(active==='functional')return;
    if(isToffee){
      if(frame.hasAttribute('src'))frame.removeAttribute('src');
      renderFiles();
    }else if(!frame.src||new URL(frame.src).searchParams.get('metric')!==active){
      const view='/api/chat/projects/'+encodeURIComponent(project.id)+'/rtl-view';
      frame.src='/rtl-viewer/?coverageOnly=1&metric='+encodeURIComponent(active)+'&view='+encodeURIComponent(view);
    }
  }

  function renderFiles(){
    const rows=new Map(),report=snapshot?.report;
    const rowFor=(instance,file)=>{
      const key=JSON.stringify([instance,file]);
      if(!rows.has(key))rows.set(key,{instance,file,covered:0,total:0});
      return rows.get(key);
    };
    if(active==='line'){
      for(const instance of report?.instances||[])for(const file of instance.files||[]){
        const row=rowFor(instance.path,file.path);
        row.covered=file.covered_lines?.length||0;
        row.total=Object.keys(file.line_hits||{}).length||row.covered+(file.uncovered_lines?.length||0);
      }
    }else for(const observation of report?.code_metrics?.[active]?.dut?.items||[]){
      const row=rowFor(observation.hierarchy||observation.module,observation.file);
      row.covered+=Number(observation.hits>0);row.total++;
    }
    const list=el('ul','toffee-coverage-file-list');
    for(const row of rows.values()){
      const link=button('',()=>window.FlowCanvasOpenCoverageSource(active,row.instance,row.file),'toffee-coverage-file');
      link.title=row.file;link.append(el('code','',row.file),el('span','',row.covered+' / '+row.total));
      const item=el('li');item.append(link);list.append(item);
    }
    const empty=active==='line'&&snapshot?.runs?.length
      ?'本批次没有采集到 DUT 行覆盖点。'
      :'暂无覆盖率数据。';
    filesPane.replaceChildren(rows.size?list:el('p','toffee-coverage-empty',empty));
  }

  function makeCard(mode,label){
    const card=button('',()=>choose(mode),'coverage-overview-item');card.dataset.metric=mode;
    const ring=el('span','coverage-overview-ring');
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 64 64');svg.setAttribute('aria-hidden','true');
    for(const cls of ['coverage-ring-track','coverage-ring-fill']){
      const circle=document.createElementNS(svg.namespaceURI,'circle');
      for(const [key,value] of Object.entries({cx:32,cy:32,r:27,pathLength:100,class:cls}))circle.setAttribute(key,value);
      svg.append(circle);
    }
    ring.append(svg,el('strong','coverage-metric-percent'));
    card.append(ring,el('span','coverage-metric-label',label),el('span','coverage-metric-count'));
    return card;
  }

  function update(value,definition){
    const oldKeys=metrics.map(([key])=>key).join(',');
    snapshot=value;isToffee=value?.provider==='toffee';
    const records=(definition?.targets||[]).filter(node=>node.kind==='group').map(node=>definition.coverage?.targets?.[node.id]);
    const bins=[...new Map(records.flatMap(record=>record?.observations||[]).map(row=>[row.instance+'::'+row.point,row])).values()];
    const report=value?.report,instances=report?.instances||[],code=report?.code_metrics||{};
    const coreMetrics=['functional','line','toggle'];
    metrics=isToffee?toffeeMetrics.filter(([mode])=>coreMetrics.includes(mode)||code[mode]?.dut?.total>0):vcsMetrics;
    if(!tabs.children.length||oldKeys!==metrics.map(([key])=>key).join(',')){
      tabs.replaceChildren(...metrics.map(([mode,label])=>{
        const tab=button(label,()=>choose(mode));tab.dataset.metric=mode;tab.setAttribute('role','tab');return tab;
      }));
      for(const card of overview.querySelectorAll('.coverage-overview-item'))card.remove();
      for(const [mode,label] of metrics)overview.append(makeCard(mode,mode==='assert'?'断言尝试覆盖率':label));
    }
    for(const card of overview.querySelectorAll('.coverage-overview-item')){
      const mode=card.dataset.metric,rows=mode==='functional'?bins:mode==='line'?instances:code[mode]?.dut?[code[mode].dut]:[];
      const covered=rows.reduce((sum,row)=>sum+row.covered,0),total=rows.reduce((sum,row)=>sum+row.total,0);
      const available=total>0&&(mode!=='functional'||(isToffee?records.some(record=>record?.status==='available'):records.every(record=>record?.status==='available')));
      const percentage=available?Math.min(100,100*covered/total):0;
      card.dataset.available=String(available);
      card.querySelector('.coverage-ring-fill').style.strokeDashoffset=String(100-percentage);
      card.querySelector('.coverage-metric-percent').textContent=available?percentage.toFixed(1)+'%':'—';
      card.querySelector('.coverage-metric-count').textContent=available?covered+' / '+total+' '+(mode==='functional'?'bin':mode==='line'?'行':'项'):'暂无数据';
      if(mode==='assert')card.title=available?'已尝试的断言比例；累计失败 '+(code.assert?.dut?.items||[]).reduce((sum,item)=>sum+(item.failures||0),0)+' 次，成功与失败明细请点击查看':'暂无断言数据';
      card.setAttribute('aria-label',card.querySelector('.coverage-metric-label').textContent+'，'+(available?percentage.toFixed(1)+'%，'+covered+'/'+total:'暂无有效数据')+'，点击查看明细');
    }
    const count=value?.runs?.length??definition?.coverage?.runs??0;
    runs.textContent='累计 '+count+' 次';
    reset.disabled=value?.native_artifacts?!value.can_reset:!count&&!value?.build_id;
    status.textContent=isToffee?value?.ingestion_error||'':'';
    status.hidden=!status.textContent;
    activate();
  }
  update(null,null);
  return {functionalPane,update};
};
