/** Switch the right sidebar between workflows and child agents. */
(()=>{
  const workflow=document.getElementById('workflow-page-tab'),agents=document.getElementById('agents-page-tab');
  /** Keep sidebar content and selected navigation in sync. */
  function showAgents(active){
    document.getElementById('subagents-panel').hidden=!active;
    document.getElementById('detail-content').hidden=active;
    document.querySelector('.workflow-task-heading').hidden=active;
    const popover=document.getElementById('workflow-popover');
    if(active&&popover){if(popover.matches(':popover-open'))popover.hidePopover();popover.hidden=true;document.getElementById('workflow-toggle').setAttribute('aria-expanded','false');}
    for(const [tab,selected] of [[workflow,!active],[agents,active]]){tab.classList.toggle('active',selected);tab.setAttribute('aria-current',selected?'page':'false');}
    document.dispatchEvent(new CustomEvent('flowcanvas-detail-page',{detail:active?'agents':'workflow'}));
  }
  workflow.onclick=()=>showAgents(false);agents.onclick=()=>showAgents(true);
  document.addEventListener('flowcanvas-open-workflow',()=>showAgents(false));
})();
