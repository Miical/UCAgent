/** Resolve Markdown file references without navigating the application. */
export function localReference(href) {
  if(!href || href.startsWith('#') || href.startsWith('//'))return null;
  if(/^[a-z][a-z\d+.-]*:/i.test(href)&&!href.startsWith('file:')&&!/^[^:]+\.[^:]+:\d+(?::\d+)?(?:-\d+)?$/.test(href))return null;
  let path=href;
  if(path.startsWith('file:')){
    try{const url=new URL(path);if(url.hostname&&url.hostname!=='localhost')return null;path=url.pathname+url.hash;}catch{return null;}
  }
  try{path=decodeURIComponent(path);}catch{return null;}
  const suffix=path.match(/(?:#L(\d+)(?:C\d+)?(?:-L?(\d+)(?:C\d+)?)?|:(\d+)(?::\d+)?(?:-(\d+))?)$/);
  const line=Number(suffix?.[1]||suffix?.[3]||1);
  const end=Math.max(line,Number(suffix?.[2]||suffix?.[4]||line));
  if(suffix)path=path.slice(0,suffix.index);
  return {path,line:Math.max(1,line),end};
}

/** Show a full text file with referenced lines highlighted, using text nodes only. */
export async function openLocalSource(reference) {
  const dialog=document.createElement('dialog');dialog.className='targets-dialog targets-source-dialog';
  const header=document.createElement('header'),title=document.createElement('strong'),close=document.createElement('button');
  title.textContent=reference.path;close.textContent='×';close.className='targets-close';close.type='button';close.ariaLabel='关闭源码';close.onclick=()=>dialog.close();
  header.append(title,close);
  const body=document.createElement('div');body.className='targets-dialog-content';body.textContent='正在读取文件…';dialog.append(header,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
  try{
    const response=await fetch('/api/chat/projects/source?path='+encodeURIComponent(reference.path));
    const result=await response.json();if(!response.ok)throw Error(result.detail||'无法读取文件');
    if(!dialog.open)return;
    title.textContent=result.file;
    const pre=document.createElement('pre');pre.className='targets-source';pre.tabIndex=0;
    result.content.split('\n').forEach((text,index)=>{
      const row=document.createElement('div'),number=document.createElement('span'),code=document.createElement('code');
      number.className='targets-line';number.textContent=String(index+1);code.textContent=text||' ';
      if(index+1>=reference.line&&index+1<=reference.end)row.className='target-code-selected';
      row.append(number,code);pre.append(row);
    });body.replaceChildren(pre);
    requestAnimationFrame(()=>{const row=pre.querySelector('.target-code-selected');if(row)pre.scrollTop=Math.max(0,row.offsetTop-pre.offsetTop-pre.clientHeight*.2);});
  }catch(error){body.textContent=error.message;}
}
