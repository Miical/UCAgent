(()=>{
  const panel=document.getElementById('test-output-panel'),workflow=document.getElementById('workflow-page-tab'),output=document.getElementById('output-page-tab');
  let project=null,selected=null,offset=0,polling=false,active=false,epoch=0;
  const el=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const tabs=el('div','output-run-tabs'),header=el('div','output-run-header'),title=el('strong','output-run-title'),status=el('span','output-run-status'),time=el('span','output-run-time');
  tabs.setAttribute('aria-label','用例运行记录');header.append(title,status,time);
  // Translate a vertical wheel into horizontal navigation; retain native trackpad gestures.
  tabs.addEventListener('wheel',event=>{
    if(event.ctrlKey||Math.abs(event.deltaX)>=Math.abs(event.deltaY)||tabs.scrollWidth<=tabs.clientWidth)return;
    event.preventDefault();
    const unit=event.deltaMode===1?16:event.deltaMode===2?tabs.clientWidth:1;
    tabs.scrollLeft+=event.deltaY*unit;
  },{passive:false});
  const details=el('details','output-command-details'),summary=el('summary','','执行命令'),command=el('pre','test-output-command');details.append(summary,command);
  const terminal=el('div','output-terminal'),log=el('pre','test-output-log');
  log.tabIndex=0;log.setAttribute('aria-label','运行终端输出');terminal.append(log);
  const empty=el('div','output-empty');empty.append(el('span','output-empty-icon','>_'),el('strong','','尚无运行记录'),el('p','','点击测试用例旁的运行按钮，实时输出将在这里显示。'));
  panel.append(tabs,header,details,terminal,empty);
  const labels={starting:'启动中',running:'运行中',completed:'已结束',failed:'运行失败',interrupted:'已中断'};
  function show(value){
    active=value;panel.hidden=!value;document.querySelector('.detail-pane').classList.toggle('show-test-output',value);
    document.getElementById('detail-content').hidden=value;document.querySelector('.workflow-task-heading').hidden=value;
    const popover=document.getElementById('workflow-popover');if(value&&popover){if(popover.matches(':popover-open'))popover.hidePopover();popover.hidden=true;document.getElementById('workflow-toggle').setAttribute('aria-expanded','false');}
    workflow.classList.toggle('active',!value);output.classList.toggle('active',value);workflow.setAttribute('aria-current',value?'false':'page');output.setAttribute('aria-current',value?'page':'false');if(value)refresh();
  }
  function choose(id){selected=id;offset=0;epoch++;log.textContent='';details.open=false;try{localStorage.setItem('flowcanvas.testRun.'+project.id,id);}catch{}refresh();}
  function hasRun(value){for(const n of [tabs,header,details,terminal])n.hidden=!value;empty.hidden=value;}
  hasRun(false);
  async function refresh(){
    if(!active||!project||polling)return;polling=true;const generation=epoch;
    try{
      const base='/api/chat/projects/'+encodeURIComponent(project.id)+'/test-runs';
      const response=await fetch(base);if(!response.ok)throw Error('无法读取运行列表');const rows=await response.json();
      if(generation!==epoch)return;
      if(!rows.some(r=>r.id===selected)){selected=rows[0]?.id||null;offset=0;log.textContent='';}
      hasRun(!!selected);
      const signature=JSON.stringify(rows.map(r=>[r.id,r.status]));
      if(tabs.dataset.signature!==signature){
        const scroll=tabs.scrollLeft;tabs.replaceChildren();
        for(const row of rows){const button=el('button','output-run-tab');button.type='button';button.dataset.id=row.id;button.dataset.status=row.status;
          const dot=el('span','output-status-dot'),name=el('span','',row.name);button.append(dot,name);button.title=row.name+' · '+labels[row.status]+' · '+new Date(row.started*1000).toLocaleString();button.onclick=()=>choose(row.id);tabs.append(button);}
        tabs.dataset.signature=signature;tabs.scrollLeft=scroll;
      }
      for(const button of tabs.children){button.classList.toggle('active',button.dataset.id===selected);button.setAttribute('aria-pressed',String(button.dataset.id===selected));}
      if(!selected)return;
      const result=await fetch(base+'/'+selected+'/output?offset='+offset);if(!result.ok)throw Error('无法读取命令输出');const data=await result.json();if(generation!==epoch)return;
      const row=data.run;title.textContent=row.name;title.title=row.name;status.dataset.status=row.status;status.textContent=labels[row.status]+(row.exit_code!==null?' · 退出码 '+row.exit_code:'');
      const seconds=Math.max(0,Math.floor((row.ended||Date.now()/1000)-row.started));time.textContent=(seconds>=60?Math.floor(seconds/60)+'分 ':'')+seconds%60+'秒';time.title='开始于 '+new Date(row.started*1000).toLocaleString();
      command.textContent=row.command;status.title=row.coverage_error||row.error||'';if(row.coverage_status)status.textContent+=' · '+({pending:'待采集',extracting:'提取覆盖率',merging:'合并覆盖率',merged:'覆盖率已累计',failed:'覆盖率处理失败',previous_batch:'历史批次'}[row.coverage_status]||row.coverage_status);
      const follow=log.scrollHeight-log.scrollTop-log.clientHeight<40;
      if(data.text)log.append(document.createTextNode(data.text));offset=data.offset;
      log.dataset.empty=String(!log.textContent);log.dataset.running=String(['starting','running'].includes(row.status));
      if(log.textContent.length>2000000)log.textContent='[较早输出已省略，完整日志保存在项目 .flowcanvas/test-runs]\n'+log.textContent.slice(-1500000);
      if(follow)log.scrollTop=log.scrollHeight;
    }catch(e){hasRun(true);status.textContent=e.message;status.dataset.status='failed';}finally{polling=false;}
  }
  workflow.onclick=()=>show(false);output.onclick=()=>show(true);
  document.addEventListener('flowcanvas-open-workflow',()=>show(false));
  document.addEventListener('flowcanvas-test-run',event=>{project=event.detail.project;choose(event.detail.run.id);document.dispatchEvent(new Event('flowcanvas-open-workflow'));show(true);});
  fetch('/api/chat/projects').then(r=>r.json()).then(state=>{project=state.current;if(project){selected=localStorage.getItem('flowcanvas.testRun.'+project.id);}}).catch(()=>{});
  setInterval(()=>{if(!document.hidden)refresh();},500);
})();
