/* Local folder selection and explicit first-time project import. */
(()=>{
  const $=id=>document.getElementById(id),dialog=$('project-dialog');
  let folder='',parent='',candidate=null,busy=false;
  async function api(path,body){const response=await fetch('/api/chat/projects'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok)throw Error(response.status===404?'项目服务尚未加载，请重启工作台后重试':data.detail||'操作失败');return data;}
  function error(message=''){$('project-error').hidden=!message;$('project-error').textContent=message;}
  function mode(importing){$('project-actions').hidden=importing;$('project-browse').hidden=importing;$('project-import').hidden=!importing;$('project-back').hidden=!importing;$('project-dialog-title').textContent=importing?'导入项目':'打开项目';$('project-dialog-hint').textContent=importing?'此文件夹尚未初始化为 FlowCanvas 项目':'选择工作台所在机器上的项目文件夹';$('project-confirm').textContent=importing?'导入并打开':'选择此文件夹';}
  const pathInput=$('project-path'),suggestions=$('project-path-suggestions');
  let completionTimer,completionVersion=0,matches=[],matchIndex=-1;
  function hideSuggestions(){clearTimeout(completionTimer);completionVersion++;suggestions.hidden=true;pathInput.setAttribute('aria-expanded','false');pathInput.removeAttribute('aria-activedescendant');matches=[];matchIndex=-1;}
  function highlight(index){matchIndex=index;for(const [i,node] of [...suggestions.children].entries())node.setAttribute('aria-selected',String(i===index));const node=suggestions.children[index];if(node){pathInput.setAttribute('aria-activedescendant',node.id);node.scrollIntoView({block:'nearest'});}}
  function acceptCompletion(enter=false){const item=matches[matchIndex];if(!item)return;hideSuggestions();pathInput.value=item.path.replace(/\/$/,'')+'/';if(enter)browse(item.path).catch(e=>error(e.message));else{pathInput.focus();pathInput.setSelectionRange(pathInput.value.length,pathInput.value.length);scheduleCompletion();}}
  async function completePath(){
    const version=++completionVersion,value=pathInput.value;
    const slash=value.lastIndexOf('/');
    let base=slash<0?(folder||''):value.slice(0,slash+1),prefix=slash<0?value:value.slice(slash+1);
    if(value==='~'){base='~';prefix='';}
    if(base&&!base.startsWith('/')&&!base.startsWith('~'))base=(folder||'')+'/'+base;
    try{
      const data=await api('/folders?path='+encodeURIComponent(base));
      if(version!==completionVersion||value!==pathInput.value||document.activeElement!==pathInput)return;
      matches=data.folders.filter(item=>item.name.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase())).slice(0,60);
      suggestions.replaceChildren();matchIndex=-1;
      for(const [index,item] of matches.entries()){const option=document.createElement('div');option.id='project-path-option-'+index;option.setAttribute('role','option');option.setAttribute('aria-selected','false');option.textContent=item.name+'/';option.title=item.path;option.onpointerdown=event=>event.preventDefault();option.onclick=()=>{matchIndex=index;acceptCompletion();};suggestions.append(option);}
      suggestions.hidden=!matches.length;pathInput.setAttribute('aria-expanded',String(Boolean(matches.length)));if(matches.length)highlight(0);
    }catch{if(version===completionVersion)hideSuggestions();}
  }
  function scheduleCompletion(){clearTimeout(completionTimer);completionVersion++;completionTimer=setTimeout(completePath,120);}
  pathInput.addEventListener('input',scheduleCompletion);
  pathInput.addEventListener('focus',scheduleCompletion);
  pathInput.addEventListener('blur',hideSuggestions);
  pathInput.addEventListener('keydown',event=>{
    if(event.isComposing)return;
    if(event.key==='Escape'&&!suggestions.hidden){event.preventDefault();event.stopPropagation();hideSuggestions();return;}
    if(suggestions.hidden||!matches.length)return;
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();highlight((matchIndex+(event.key==='ArrowDown'?1:-1)+matches.length)%matches.length);}
    else if(event.key==='Tab'&&!event.shiftKey||event.key==='Enter'){event.preventDefault();acceptCompletion(event.key==='Enter');}
  });
  dialog.addEventListener('close',hideSuggestions);
  async function browse(path=''){
    hideSuggestions();
    const data=await api('/folders?path='+encodeURIComponent(path));folder=data.path;parent=data.parent;$('project-path').value=folder;
  }
  let modules=[],moduleIndex=0,moduleMatches=[],rtlVersion=0,rtlSelection=null;
  const topInput=$('project-top'),moduleOptions=$('project-module-options');
  function showModules(){
    const query=topInput.value.toLocaleLowerCase();moduleMatches=modules.filter(item=>(item.name+' '+item.file).toLocaleLowerCase().includes(query)).slice(0,100);moduleIndex=0;moduleOptions.replaceChildren();
    for(const [index,item] of moduleMatches.entries()){
      const row=document.createElement('div');row.id='project-module-'+index;row.setAttribute('role','option');row.setAttribute('aria-selected',String(index===0));
      const name=document.createElement('strong');name.textContent=item.name;const file=document.createElement('small');file.textContent=item.file+':'+item.line;row.append(name,file);
      row.onpointerdown=e=>e.preventDefault();row.onclick=()=>selectModule(item);moduleOptions.append(row);
    }
    moduleOptions.hidden=!moduleMatches.length;topInput.setAttribute('aria-expanded',String(Boolean(moduleMatches.length)));if(moduleMatches.length)topInput.setAttribute('aria-activedescendant','project-module-0');else topInput.removeAttribute('aria-activedescendant');
  }
  async function selectModule(item){
    const version=++rtlVersion;rtlSelection=item;topInput.value=item.name;moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');topInput.removeAttribute('aria-activedescendant');
    $('project-rtl-status').textContent='正在解析依赖…';$('project-filelist').replaceChildren();
    try{const result=await api('/dependencies',{path:candidate.path,top:item.name,top_file:item.file});if(version!==rtlVersion)return;
      $('project-rtl-status').textContent=result.ready?'已找到 '+result.files.length+' 个源码文件、'+result.headers.length+' 个头文件':'依赖需要处理：'+result.issues.join('\n');
      for(const file of result.files){const row=document.createElement('div');row.textContent=file;$('project-filelist').append(row);}
    }catch(e){if(version===rtlVersion)$('project-rtl-status').textContent=e.message;}
  }
  async function loadModules(path){
    const version=++rtlVersion;rtlSelection=null;modules=[];topInput.value='';topInput.disabled=true;moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');$('project-filelist').replaceChildren();$('project-rtl-status').textContent='正在扫描 Verilog / SystemVerilog 模块…';
    try{const result=await api('/modules',{path});if(version!==rtlVersion)return;modules=result.modules;topInput.disabled=false;$('project-rtl-status').textContent=modules.length?'找到 '+modules.length+' 个模块，请选择顶层':'未发现模块定义；已有 RTL 可能存在解析错误';}
    catch(e){if(version===rtlVersion){topInput.disabled=false;$('project-rtl-status').textContent=e.message;}}
  }
  topInput.oninput=()=>{rtlVersion++;rtlSelection=null;$('project-filelist').replaceChildren();$('project-rtl-status').textContent='请选择一个顶层模块';showModules();};
  topInput.onfocus=showModules;
  topInput.onblur=()=>{moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');};
  topInput.onkeydown=event=>{
    if(event.isComposing)return;
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');return;}
    if(moduleOptions.hidden||!moduleMatches.length)return;
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();moduleIndex=(moduleIndex+(event.key==='ArrowDown'?1:-1)+moduleMatches.length)%moduleMatches.length;for(const [i,row]of [...moduleOptions.children].entries())row.setAttribute('aria-selected',String(i===moduleIndex));topInput.setAttribute('aria-activedescendant','project-module-'+moduleIndex);moduleOptions.children[moduleIndex].scrollIntoView({block:'nearest'});}
    else if(event.key==='Enter'||event.key==='Tab'&&!event.shiftKey){event.preventDefault();selectModule(moduleMatches[moduleIndex]);}
  };
  async function open(path){if(!path)throw Error('请先选择文件夹');error();const result=await api('/open',{path});if(result.needs_import){candidate=result;mode(true);$('project-name').value=result.name;$('project-import-path').textContent=result.path;$('project-rtl-import').hidden=false;$('project-name').focus();loadModules(result.path);}else location.reload();}
  $('project-open').onclick=async()=>{dialog.showModal();rtlVersion++;candidate=null;mode(false);error();try{const state=await api('');await browse(state.current?.path||'');$('project-recents').replaceChildren();for(const project of state.recent){const button=document.createElement('button');button.className='project-recent';const name=document.createElement('strong');name.textContent=project.name;const path=document.createElement('span');path.textContent=project.path;button.append(name,path);button.title=project.path;button.onclick=()=>open(project.path).catch(e=>error(e.message));$('project-recents').append(button);}if(!state.recent.length){const empty=document.createElement('p');empty.textContent='暂无最近项目';$('project-recents').append(empty);}}catch(e){error(e.message);}};
  $('project-new').onclick=()=>{
    if(!folder){error('请先选择新项目的存放目录');return;}
    rtlVersion++;rtlSelection=null;candidate={path:folder,create:true};mode(true);$('project-rtl-import').hidden=true;
    $('project-dialog-title').textContent='新建项目';$('project-dialog-hint').textContent='在所选位置创建新的项目文件夹';
    $('project-import-path').textContent='存放位置：'+folder;$('project-name').value='';$('project-confirm').textContent='创建并打开';$('project-name').focus();
  };
  $('project-without').onclick=async()=>{try{await api('/close',{});location.reload();}catch(e){error(e.message);}};
  $('project-close').onclick=()=>dialog.close();
  $('project-path-form').onsubmit=event=>{event.preventDefault();error();browse($('project-path').value).catch(e=>error(e.message));};
  $('project-back').onclick=()=>{rtlVersion++;candidate=null;mode(false);error();};
  $('project-confirm').onclick=async()=>{if(busy)return;busy=true;$('project-confirm').disabled=true;error();try{if(candidate){await api(candidate.create?'/create':'/import',{path:candidate.path,name:$('project-name').value,top:rtlSelection?.name||'',top_file:rtlSelection?.file||''});location.reload();}else await open($('project-path').value.trim()||folder);}catch(e){error(e.message);}finally{busy=false;$('project-confirm').disabled=false;}};
  api('').then(state=>{
    const pane=document.querySelector('.project-pane');
    if(state.current){
      const name=document.createElement('button');name.type='button';name.className='project-identity';name.title=state.current.path;name.setAttribute('aria-label','切换项目：'+state.current.name);
      const icon=document.createElementNS('http://www.w3.org/2000/svg','svg');icon.setAttribute('viewBox','0 0 24 24');icon.setAttribute('aria-hidden','true');
      const shape=document.createElementNS('http://www.w3.org/2000/svg','path');shape.setAttribute('d','M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z');icon.append(shape);
      const label=document.createElement('span');label.textContent=state.current.name;
      const chevron=document.createElement('span');chevron.className='project-identity-chevron';chevron.setAttribute('aria-hidden','true');
      name.append(icon,label,chevron);name.onclick=()=>$('project-open').click();
      const heading=$('project-heading'),header=document.querySelector('.project-header');heading.classList.remove('project-section-heading');header.append(heading,name);
      $('project-open').title='项目：'+state.current.name+'\n'+state.current.path;
      $('project-open').setAttribute('aria-label','切换项目：'+state.current.name);
    }else{
      pane.classList.add('no-project');
      const empty=document.createElement('div');empty.className='project-empty-actions';
      const heading=document.createElement('p');heading.textContent='开始一个项目';empty.append(heading);
      for(const [label,create] of [['导入项目',false],['新建项目',true]]){
        const button=document.createElement('button');button.type='button';button.textContent=label;
        button.onclick=async()=>{folder='';await $('project-open').onclick();if(create&&folder)$('project-new').click();};empty.append(button);
      }
      $('project-content').replaceChildren(empty);
    }
  }).catch(()=>{});
})();
