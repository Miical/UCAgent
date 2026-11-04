/** Local Markdown pipeline: CommonMark/GFM-style blocks with escaped, highlighted code. */
import MarkdownIt from 'markdown-it';
import {localReference,openLocalSource} from './local-source.js';
import taskLists from 'markdown-it-task-lists';
import hljs from 'highlight.js/lib/core';
import verilog from 'highlight.js/lib/languages/verilog';
import python from 'highlight.js/lib/languages/python';
import cpp from 'highlight.js/lib/languages/cpp';
import bash from 'highlight.js/lib/languages/bash';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import json from 'highlight.js/lib/languages/json';
import yaml from 'highlight.js/lib/languages/yaml';
import diff from 'highlight.js/lib/languages/diff';
import xml from 'highlight.js/lib/languages/xml';
import css from 'highlight.js/lib/languages/css';

for (const [name, definition] of Object.entries({verilog,python,cpp,bash,javascript,typescript,json,yaml,diff,xml,css})) {
  hljs.registerLanguage(name, definition);
}
hljs.registerAliases(['sv','systemverilog'],{languageName:'verilog'});
const parser = new MarkdownIt({
  html:false, breaks:true, linkify:true,
  highlight(source, language) {
    // Unsupported or still-streaming language names fall back to escaped plain code.
    if(language && source.length < 100000 && hljs.getLanguage(language)) {
      return hljs.highlight(source,{language,ignoreIllegals:true}).value;
    }
    return '';
  }
}).use(taskLists,{enabled:false});

const validateLink=parser.validateLink.bind(parser);
parser.validateLink=url=>url.startsWith('file:')?!!localReference(url):validateLink(url);

/** Produce safe HTML without enabling user-authored HTML or executable URLs. */
export function renderMarkup(text) {return parser.render(text);}

/** Render message content, add code controls, and retain horizontal scroll positions. */
export function markdown(target,text) {
  const offsets=[...target.querySelectorAll('pre,.markdown-table')].map(node=>node.scrollLeft);
  target.classList.add('markdown-body');
  // HTML is generated only by the parser with html:false and registered trusted plugins.
  target.innerHTML=renderMarkup(text);
  for(const link of target.querySelectorAll('a')){
    const reference=localReference(link.getAttribute('href'));
    if(reference){link.title=reference.path+':'+reference.line;link.onclick=event=>{event.preventDefault();openLocalSource(reference);};}
    else {link.target='_blank';link.rel='noopener noreferrer';}
  }
  for(const image of target.querySelectorAll('img')){image.loading='lazy';image.referrerPolicy='no-referrer';}
  for(const table of target.querySelectorAll('table')){
    const wrapper=document.createElement('div');wrapper.className='markdown-table';
    table.before(wrapper);wrapper.append(table);
  }
  for(const pre of target.querySelectorAll('pre')){
    const code=pre.querySelector('code');if(!code)continue;
    const block=document.createElement('div');block.className='code-block';
    const header=document.createElement('div');header.className='code-header';
    const language=document.createElement('span');
    language.textContent=[...code.classList].find(name=>name.startsWith('language-'))?.slice(9)||'text';
    const copy=document.createElement('button');copy.type='button';copy.textContent='复制代码';copy.ariaLabel='复制代码';
    copy.onclick=async()=>{
      try{await navigator.clipboard.writeText(code.textContent);copy.textContent='已复制';}
      catch{copy.textContent='复制失败';}
      setTimeout(()=>{copy.textContent='复制代码';},1500);
    };
    header.append(language,copy);pre.before(block);block.append(header,pre);
  }
  [...target.querySelectorAll('pre,.markdown-table')].forEach((node,index)=>{node.scrollLeft=offsets[index]||0;});
}

export {openLocalSource} from './local-source.js';
