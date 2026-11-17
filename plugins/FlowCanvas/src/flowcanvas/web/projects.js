/* Open existing RTL folders and confirm their top-level design before entering the workspace. */
(()=>{
  const $=id=>document.getElementById(id),dialog=$('project-dialog');
  let folder='',parent='',candidate=null,busy=false,folderInfo=null,folderPreview=null,folderQuery='',browsing=false,browseVersion=0;
  async function api(path,body){const response=await fetch('/api/chat/projects'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const data=await response.json();if(!response.ok)throw Error(response.status===404?'项目服务尚未加载，请重启工作台后重试':data.detail||'操作失败');return data;}
  function error(message=''){$('project-error').hidden=!message;$('project-error').textContent=message;}
  function mode(importing){
    $('project-actions').hidden=importing;$('project-browse').hidden=importing;$('project-import').hidden=!importing;$('project-back').hidden=!importing;
    $('project-dialog-title').textContent=importing?'创建项目':'打开项目';$('project-dialog-hint').textContent=importing?'请选择 RTL 顶层模块':'选择项目文件夹';
    $('project-confirm').textContent=importing?'创建并打开':'打开项目';$('project-confirm').disabled=importing;
    if(!importing)updateFolderSelection();
  }
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
      folderPreview=data;folderQuery=prefix;renderFolders();
      matches=data.folders.filter(item=>item.name.toLocaleLowerCase().startsWith(prefix.toLocaleLowerCase())).slice(0,60);
      suggestions.replaceChildren();matchIndex=-1;
      for(const [index,item] of matches.entries()){const option=document.createElement('div');option.id='project-path-option-'+index;option.setAttribute('role','option');option.setAttribute('aria-selected','false');option.textContent=item.name+'/';option.title=item.path;option.onpointerdown=event=>event.preventDefault();option.onclick=()=>{matchIndex=index;acceptCompletion();};suggestions.append(option);}
      suggestions.hidden=!matches.length;pathInput.setAttribute('aria-expanded',String(Boolean(matches.length)));pathInput.removeAttribute('aria-activedescendant');
    }catch{if(version===completionVersion)hideSuggestions();}
  }
  function scheduleCompletion(){clearTimeout(completionTimer);completionVersion++;completionTimer=setTimeout(completePath,120);}
  pathInput.addEventListener('input',()=>{const value=pathInput.value.trim();folderPreview=null;folderQuery=value===folder||value===folder+'/'?'':value.startsWith(folder.replace(/\/$/,'')+'/')?value.slice(folder.replace(/\/$/,'').length+1):value.split('/').at(-1);renderFolders();scheduleCompletion();});
  pathInput.addEventListener('focus',scheduleCompletion);
  pathInput.addEventListener('blur',hideSuggestions);
  pathInput.addEventListener('keydown',event=>{
    if(event.isComposing)return;
    if(event.key==='Escape'&&!suggestions.hidden){event.preventDefault();event.stopPropagation();hideSuggestions();return;}
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();endPathEdit();$('project-address').focus();return;}
    if(suggestions.hidden||!matches.length)return;
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();highlight(matchIndex<0?(event.key==='ArrowDown'?0:matches.length-1):(matchIndex+(event.key==='ArrowDown'?1:-1)+matches.length)%matches.length);}
    else if(event.key==='Tab'&&!event.shiftKey||event.key==='Enter'&&matchIndex>=0){event.preventDefault();if(matchIndex<0)matchIndex=0;acceptCompletion(event.key==='Enter');}
  });
  dialog.addEventListener('close',hideSuggestions);
  /** Describe the selected directory before opening it or confirming its RTL design. */
  function updateFolderSelection(){
    const selected=$('project-folder-selection');
    selected.textContent=browsing?'正在读取文件夹…':'';selected.hidden=!browsing;
    selected.title=folder||'';
    $('project-parent').disabled=browsing||!folder||folder===parent;
    if(!candidate){$('project-confirm').disabled=busy||browsing||!folderInfo;$('project-confirm').textContent=folderInfo?.is_project?'打开项目':'选择此文件夹';}
  }
  /** List subfolders as keyboard-accessible navigation targets, without scanning their RTL. */
  function renderFolders(){
    const list=$('project-folders'),query=folderQuery.trim().toLocaleLowerCase();list.replaceChildren();
    const folders=((folderPreview||folderInfo)?.folders||[]).filter(item=>item.name.toLocaleLowerCase().includes(query));
    for(const item of folders){
      const button=document.createElement('button');button.type='button';button.className='project-folder-row';button.title=item.path;
      button.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 6V4h6l2 2h7v10H2Z"/></svg>';
      const name=document.createElement('span');name.className='project-folder-name';name.textContent=item.name;button.append(name);
      if(item.is_project){const tag=document.createElement('small');tag.textContent='项目';button.append(tag);}
      const arrow=document.createElement('span');arrow.className='project-folder-arrow';arrow.textContent='›';button.append(arrow);
      button.onclick=()=>browse(item.path).catch(e=>error(e.message));list.append(button);
    }
    if(!folders.length){const empty=document.createElement('p');empty.className='project-folders-empty';empty.textContent=query?'没有匹配的文件夹':'没有子文件夹，可选择当前目录';list.append(empty);}
  }
  /** Navigate with breadcrumbs, directory rows or a pasted path; discard stale responses. */
  async function browse(path=''){
    const version=++browseVersion,fromList=$('project-folders').contains(document.activeElement);hideSuggestions();browsing=true;error();updateFolderSelection();$('project-folders').setAttribute('aria-busy','true');
    try{
      const data=await api('/folders?path='+encodeURIComponent(path));if(version!==browseVersion)return;
      folderInfo=data;folder=data.path;parent=data.parent;pathInput.value=folder;endPathEdit();
      localStorage.setItem('flowcanvas.folder',folder);const crumbs=$('project-breadcrumbs');crumbs.replaceChildren();
      let current='';for(const part of ['/',...folder.split('/').filter(Boolean)]){
        current=part==='/'?'/':current.replace(/\/$/,'')+'/'+part;const target=current,button=document.createElement('button');button.type='button';button.textContent=part;button.title=target;
        if(target===folder)button.setAttribute('aria-current','location');button.onclick=()=>target===folder?editPath():browse(target).catch(e=>error(e.message));crumbs.append(button);
      }
      renderFolders();crumbs.scrollLeft=crumbs.scrollWidth;if(fromList)($('project-folders').querySelector('button')||$('project-address')).focus();
    }finally{if(version===browseVersion){browsing=false;$('project-folders').setAttribute('aria-busy','false');updateFolderSelection();}}
  }
  $('project-parent').onclick=()=>browse(parent).catch(e=>error(e.message));$('project-home').onclick=()=>browse('').catch(e=>error(e.message));
  /** Edit the existing address bar in place, like a desktop file browser. */
  function editPath(){$('project-breadcrumbs').hidden=true;$('project-path-form').hidden=false;pathInput.value=folder;pathInput.focus();pathInput.select();}
  /** Return to clickable breadcrumbs without altering the selected directory. */
  function endPathEdit(){hideSuggestions();folderQuery='';folderPreview=null;renderFolders();$('project-path-form').hidden=true;$('project-breadcrumbs').hidden=false;pathInput.value=folder;}
  $('project-address').onclick=event=>{if(!event.target.closest('button')&&!$('project-path-form').contains(event.target))editPath();};
  $('project-address').onkeydown=event=>{if(event.target===$('project-address')&&['Enter',' '].includes(event.key)){event.preventDefault();editPath();}};
  dialog.addEventListener('keydown',event=>{if(event.altKey&&event.key.toLowerCase()==='d'&&!$('project-browse').hidden){event.preventDefault();editPath();}});
  $('project-address').addEventListener('focusout',()=>{requestAnimationFrame(()=>{if(!$('project-address').contains(document.activeElement))endPathEdit();});});
  $('project-folders').onkeydown=event=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    const rows=[...$('project-folders').querySelectorAll('button')];if(!rows.length)return;event.preventDefault();const index=rows.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?rows.length-1:Math.max(0,Math.min(rows.length-1,index+(event.key==='ArrowDown'?1:-1)));rows[next].focus();
  };
  let modules=[],moduleIndex=0,moduleMatches=[],rtlVersion=0,rtlSelection=null,rtlReady=false;
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
    const version=++rtlVersion;rtlSelection=item;rtlReady=false;$('project-confirm').disabled=true;topInput.value=item.name;moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');topInput.removeAttribute('aria-activedescendant');
    $('project-rtl-status').textContent='正在解析依赖…';$('project-filelist').replaceChildren();
    try{const result=await api('/dependencies',{path:candidate.path,top:item.name,top_file:item.file});if(version!==rtlVersion)return;rtlReady=result.ready;$('project-confirm').disabled=!rtlReady;
      $('project-rtl-status').textContent=result.ready?'已找到 '+result.files.length+' 个源码文件、'+result.headers.length+' 个头文件':'依赖需要处理：'+result.issues.join('\n');
      for(const file of result.files){const row=document.createElement('div');row.textContent=file;$('project-filelist').append(row);}
    }catch(e){if(version===rtlVersion)$('project-rtl-status').textContent=e.message;}
  }
  async function loadModules(path,inventory=null){
    const version=++rtlVersion;rtlSelection=null;rtlReady=false;$('project-confirm').disabled=true;modules=[];topInput.value='';topInput.disabled=true;moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');$('project-filelist').replaceChildren();$('project-rtl-status').textContent='正在扫描 Verilog / SystemVerilog 模块…';
    try{const result=inventory||await api('/modules',{path});if(version!==rtlVersion)return;modules=result.modules;topInput.disabled=false;$('project-rtl-status').textContent=modules.length?'找到 '+modules.length+' 个模块，请选择顶层':'未发现模块定义；已有 RTL 可能存在解析错误';}
    catch(e){if(version===rtlVersion){topInput.disabled=false;$('project-rtl-status').textContent=e.message;}}
  }
  topInput.oninput=()=>{rtlVersion++;rtlSelection=null;rtlReady=false;$('project-confirm').disabled=true;$('project-filelist').replaceChildren();$('project-rtl-status').textContent='请选择一个顶层模块';showModules();};
  topInput.onfocus=showModules;
  topInput.onblur=()=>{moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');};
  topInput.onkeydown=event=>{
    if(event.isComposing)return;
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();moduleOptions.hidden=true;topInput.setAttribute('aria-expanded','false');return;}
    if(moduleOptions.hidden||!moduleMatches.length)return;
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();moduleIndex=(moduleIndex+(event.key==='ArrowDown'?1:-1)+moduleMatches.length)%moduleMatches.length;for(const [i,row]of [...moduleOptions.children].entries())row.setAttribute('aria-selected',String(i===moduleIndex));topInput.setAttribute('aria-activedescendant','project-module-'+moduleIndex);moduleOptions.children[moduleIndex].scrollIntoView({block:'nearest'});}
    else if(event.key==='Enter'||event.key==='Tab'&&!event.shiftKey){event.preventDefault();selectModule(moduleMatches[moduleIndex]);}
  };
  async function open(path){if(!path)throw Error('请先选择文件夹');error();const result=await api('/open',{path});if(result.needs_import){candidate=result;mode(true);$('project-name').value=result.name;$('project-import-path').textContent=result.path;$('project-rtl-import').hidden=false;$('project-name').focus();loadModules(result.path,result.modules?{modules:result.modules}:null);}else location.reload();}
  async function showProjectDialog(){dialog.showModal();rtlVersion++;candidate=null;mode(false);error();try{const state=await api('');try{await browse(state.current?.path||localStorage.getItem('flowcanvas.folder')||'');}catch{await browse('');}$('project-recents').replaceChildren();for(const project of state.recent){const button=document.createElement('button');button.className='project-recent';const name=document.createElement('strong');name.textContent=project.name;const path=document.createElement('span');path.textContent=project.path;button.append(name,path);button.title=project.path;button.onclick=()=>open(project.path).catch(e=>error(e.message));$('project-recents').append(button);}if(!state.recent.length){const empty=document.createElement('p');empty.textContent='暂无最近项目';$('project-recents').append(empty);}}catch(e){error(e.message);}}
  $('project-open').onclick=()=>showProjectDialog();
  /** Close only the project view; source files, definitions and history remain on disk. */
  async function closeProject(){const button=$('project-leave');button.disabled=true;try{await api('/close',{});location.reload();}catch(e){error(e.message);if(!dialog.open)dialog.showModal();}finally{button.disabled=false;}}
  $('project-leave').onclick=closeProject;
  $('project-close').onclick=()=>dialog.close();
  $('project-path-form').onsubmit=event=>{event.preventDefault();error();browse($('project-path').value).catch(e=>error(e.message));};
  $('project-back').onclick=()=>{rtlVersion++;candidate=null;mode(false);error();};
  $('project-confirm').onclick=async()=>{if(busy)return;busy=true;$('project-confirm').disabled=true;error();try{if(candidate){if(!rtlSelection||!rtlReady)throw Error('请选择依赖完整的 RTL 顶层模块');await api('/initialize',{path:candidate.path,name:$('project-name').value,top:rtlSelection?.name||'',top_file:rtlSelection?.file||''});location.reload();}else await open($('project-path').value.trim()||folder);}catch(e){error(e.message);}finally{busy=false;if(candidate)$('project-confirm').disabled=!rtlReady;else updateFolderSelection();}};
  api('').then(state=>{
    window.FlowCanvasSidebarState?.activate(state.current?.id);
    const pane=document.querySelector('.project-pane');$('project-leave').hidden=!state.current;
    if(state.current){
      $('project-open').title='项目：'+state.current.name+'\n'+state.current.path;
      $('project-open').setAttribute('aria-label','切换项目：'+state.current.name);
    }else{
      pane.classList.add('no-project');
      const empty=document.createElement('div');empty.className='project-empty-actions';
      const icon=document.createElement('div');icon.className='project-empty-icon';icon.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z"/><path d="M8 12h8m-8 4h5"/></svg>';
      const heading=document.createElement('h2');heading.textContent='打开 RTL 项目';
      const hint=document.createElement('p');hint.textContent='选择包含 RTL 设计的文件夹，开始查看与验证。';
      const actions=document.createElement('div');actions.className='project-empty-buttons';
      const button=document.createElement('button');button.type='button';button.innerHTML='<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M2 6V4h6l2 2h7v3M2 9h16l-3 8H2Z"/></svg><span>打开项目</span>';
      button.onclick=()=>showProjectDialog();actions.append(button);
      empty.append(icon,heading,hint,actions);
      $('project-content').replaceChildren(empty);
    }
  }).catch(()=>{});
})();
