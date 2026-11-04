/** Read-only child conversations scoped to the selected parent conversation. */
import {markdown} from './markdown-renderer.js';
const panel=document.getElementById('subagents-panel');
const el=(tag,cls,text)=>{const node=document.createElement(tag);node.className=cls;if(text!==undefined)node.textContent=text;return node;};
const list=el('div','subagent-list'),transcript=el('div','subagent-transcript'),notice=el('p','subagent-notice');
list.setAttribute('aria-label','当前对话的子 Agent');transcript.setAttribute('aria-label','子 Agent 对话');
panel.append(list,notice,transcript);
let session=null,selected=null,active=false,pending=false,generation=0,listSignature='',messageSignature='';
const remembered=new Map();
const labels={running:'运行中',pendingInit:'启动中',completed:'已结束',interrupted:'已中断',shutdown:'已关闭',errored:'出错',error:'出错',idle:'空闲',notLoaded:'未加载',unknown:'状态未知',notFound:'不可用'};
/** Clear parent-specific content immediately, before any new network response arrives. */
function reset(id){
  session=id;selected=remembered.get(id)||null;generation++;listSignature='';messageSignature='';list.replaceChildren();transcript.replaceChildren();notice.hidden=false;notice.textContent=id?'正在读取子 Agent…':'选择一个对话，查看其创建的子 Agent';
}
document.addEventListener('flowcanvas-session',event=>{if(session!==event.detail.id){reset(event.detail.id);refresh();}});
document.addEventListener('flowcanvas-detail-page',event=>{active=event.detail==='agents';if(active)refresh();});
/** Poll native history only while visible; discard replies from previously selected parents. */
async function refresh(){
  if(!active||!session||pending||document.hidden)return;
  const epoch=generation,parent=session;pending=true;
  try{
    const response=await fetch('/api/chat/sessions/'+encodeURIComponent(parent)+'/subagents'+(selected?'?selected='+encodeURIComponent(selected):''));
    const data=await response.json();if(epoch!==generation)return;if(!response.ok)throw Error(data.detail||'无法读取子 Agent');
    selected=data.selected;if(selected)remembered.set(parent,selected);
    notice.textContent=data.error||(!data.agents.length?'当前对话尚未创建子 Agent':'');notice.hidden=!notice.textContent;
    const nextList=JSON.stringify([data.agents,selected]);
    if(nextList!==listSignature){
      listSignature=nextList;list.replaceChildren();
      for(const agent of data.agents){
        const button=el('button','subagent-choice');button.type='button';button.classList.toggle('active',agent.id===selected);button.setAttribute('aria-pressed',String(agent.id===selected));button.title=agent.task||agent.name;
        button.append(el('span','subagent-dot '+(agent.status==='running'?'running':'')),el('span','',agent.name),el('small','',labels[agent.status]||agent.status||'状态未知'));
        button.onclick=()=>{if(selected===agent.id)return;selected=agent.id;generation++;messageSignature='';transcript.replaceChildren();notice.hidden=false;notice.textContent='正在读取对话…';refresh();};list.append(button);
      }
    }
    const nextMessages=JSON.stringify(data.items);
    if(nextMessages!==messageSignature){
      messageSignature=nextMessages;
      const follow=transcript.scrollHeight-transcript.scrollTop-transcript.clientHeight<80,scroll=transcript.scrollTop;
      const expanded=new Set([...transcript.querySelectorAll('details[open]')].map(node=>node.dataset.id));
      transcript.replaceChildren();
      let steps=null,count=0;
      for(const item of data.items){
        if(item.kind!=='tool'&&!item.text&&!item.input)continue;
        if(item.kind==='tool'){
          if(!steps){steps=el('details','subagent-steps');steps.dataset.id='steps-'+item.id;steps.open=expanded.has(steps.dataset.id);steps.append(el('summary','','执行步骤'));transcript.append(steps);count=0;}
          steps.firstChild.textContent='执行步骤 · '+(++count);
          const block=el('details','subagent-tool');block.dataset.id=item.id;block.open=expanded.has(item.id);block.append(el('summary','',item.title||'工具调用'));
          if(item.input)block.append(el('pre','',item.input));if(item.text)block.append(el('pre','',item.text));steps.append(block);
        }else{
          steps=null;
          const block=el('article','subagent-message '+(item.kind==='user'?'is-user':item.kind==='reasoning'?'is-summary':''));block.append(el('div','subagent-speaker',item.kind==='user'?'任务 / 消息':item.kind==='reasoning'?'思考摘要':'回复'));
          const content=el('div','');markdown(content,item.text||'');block.append(content);transcript.append(block);
        }
      }
      if(!transcript.children.length&&data.agents.length)transcript.append(el('p','subagent-notice','尚无对话内容'));
      transcript.scrollTop=follow?transcript.scrollHeight:scroll;
    }
  }catch(error){if(epoch===generation){notice.hidden=false;notice.textContent=error.message;}}
  finally{pending=false;if(epoch!==generation)refresh();}
}
setInterval(refresh,1500);
reset(null);
