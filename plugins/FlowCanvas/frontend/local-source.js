/** Resolve Markdown file references without navigating the application. */
export function localReference(href) {
  if(!href || href.startsWith('#') || href.startsWith('//'))return null;
  let path=href;
  if(/^https?:/i.test(href)){
    try{
      const url=new URL(href);
      if(!['127.0.0.1','localhost','[::1]'].includes(url.hostname)||!/^\/(?:home|Users|tmp|var|workspace|mnt)\//.test(url.pathname))return null;
      path=url.pathname+url.hash;
    }catch{return null;}
  }
  if(/^[a-z][a-z\d+.-]*:/i.test(path)&&!path.startsWith('file:')&&!/^[^:]+\.[^:]+:\d+(?::\d+)?(?:-\d+)?$/.test(path))return null;
  if(path.startsWith('file:')){
    try{const url=new URL(path);if(url.hostname&&url.hostname!=='localhost')return null;path=url.pathname+url.hash;}catch{return null;}
  }
  try{path=decodeURIComponent(path);}catch{return null;}
  const suffix=path.match(/(?:#L(\d+)(?:C\d+)?(?:-L?(\d+)(?:C\d+)?)?|:(\d+)(?::\d+)?(?:-(\d+))?)$/);
  if(!suffix)return {path};
  const line=Number(suffix[1]||suffix[3]);
  const end=Math.max(line,Number(suffix?.[2]||suffix?.[4]||line));
  if(suffix)path=path.slice(0,suffix.index);
  return {path,line:Math.max(1,line),end};
}

/** Route supported project images through the same confined file access boundary. */
export function localImageUrl(reference) {
  return reference&&/\.(?:svg|png|jpe?g|gif|webp)$/i.test(reference.path)
    ? '/api/chat/projects/source?media=true&path='+encodeURIComponent(reference.path):null;
}

/** Show a full text file with referenced lines highlighted, using text nodes only. */
export async function openLocalSource(reference) {
  const dialog=document.createElement('dialog');dialog.className='targets-dialog targets-source-dialog';
  const header=document.createElement('header'),title=document.createElement('strong'),close=document.createElement('button');
  title.textContent=reference.path;close.textContent='×';close.className='targets-close';close.type='button';close.ariaLabel='关闭源码';close.onclick=()=>dialog.close();
  header.append(title,close);
  const body=document.createElement('div');body.className='targets-dialog-content';body.textContent='正在读取文件…';dialog.append(header,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
  const imageUrl=localImageUrl(reference);
  if(imageUrl){
    const image=document.createElement('img');image.className='local-image-preview';image.alt=reference.path;
    image.onload=()=>{if(dialog.open)body.replaceChildren(image);};
    image.onerror=()=>{body.textContent='无法显示图片，请检查文件是否存在且属于已打开的项目。';};
    image.src=imageUrl;
    return;
  }
  try{
    const response=await fetch('/api/chat/projects/source?path='+encodeURIComponent(reference.path));
    const result=await response.json();if(!response.ok)throw Error(result.detail||'无法读取文件');
    if(!dialog.open)return;
    title.textContent=result.file;
    const pre=document.createElement('pre');pre.className='targets-source';pre.tabIndex=0;
    const start=Number.isInteger(reference.line)&&reference.line>0?reference.line:null;
    const end=start!==null&&Number.isInteger(reference.end)&&reference.end>=start?reference.end:start;
    result.content.split('\n').forEach((text,index)=>{
      const row=document.createElement('div'),number=document.createElement('span'),code=document.createElement('code');
      number.className='targets-line';number.textContent=String(index+1);code.textContent=text||' ';
      if(start!==null&&index+1>=start&&index+1<=end)row.className='target-code-selected';
      row.append(number,code);pre.append(row);
    });body.replaceChildren(pre);
    requestAnimationFrame(()=>{const row=pre.querySelector('.target-code-selected');if(row)pre.scrollTop=Math.max(0,row.offsetTop-pre.offsetTop-pre.clientHeight*.2);});
  }catch(error){body.textContent=error.message;}
}
