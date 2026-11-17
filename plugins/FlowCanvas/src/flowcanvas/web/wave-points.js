/** Read the actual waveform clip rectangle so annotations follow native column resizing. */
if(new URL(location.href).searchParams.get('embedded')==='1'){
  for(const name of ['WebGLRenderingContext','WebGL2RenderingContext']){
    const prototype=window[name]?.prototype;if(!prototype||Object.hasOwn(prototype,'flowCanvasScissor'))continue;
    const scissor=prototype.scissor;prototype.flowCanvasScissor=true;
    prototype.scissor=function(x,y,width,height){
      const canvas=this.canvas;
      if(x>0&&x+width>=canvas.width-2&&height>canvas.height*.8&&width>canvas.width*.25)window.FlowCanvasWavePlot={canvas,x,y,width,height};
      return scissor.call(this,x,y,width,height);
    };
  }
}
/** Draw unobtrusive full-height time guides using the renderer's current viewport and exact clip. */
window.FlowCanvasWavePoints=function(api,points,end){
  if(!points.length)return {select(){},dispose(){}};
  const layer=document.createElement('div');layer.className='flowcanvas-wave-points';layer.style.cssText='position:fixed;pointer-events:none;z-index:5;overflow:hidden;display:none';document.body.append(layer);
  const grouped=new Map();points.forEach((point,index)=>{if(!grouped.has(point.time))grouped.set(point.time,[]);grouped.get(point.time).push(index);});
  let active=0,disposed=false,busy=false,last='';
  const guides=[...grouped].map(([time,indices])=>{const line=document.createElement('div'),label=document.createElement('span');line.className='flowcanvas-wave-point-guide';line.style.cssText='position:absolute;top:0;bottom:0;transform:translateX(-50%)';label.style.cssText='position:absolute;bottom:4px;left:4px;font:10px/14px system-ui,sans-serif;white-space:nowrap';line.append(label);label.textContent=indices.map(index=>index+1).join(',');layer.append(line);return {line,label,time:Number(time),indices};});
  /** Observe pan, zoom and resize without changing the native viewer's input behavior. */
  async function draw(){
    if(disposed||busy||document.hidden)return;const plot=window.FlowCanvasWavePlot;if(!plot||!plot.canvas.getBoundingClientRect().width)return;
    busy=true;
    try{
      const state=await api.get_state();if(disposed)return;
      const viewport=state?.match(/viewports:\s*\[\s*\(\s*curr_left:\s*\(([-+\d.eE]+)\),\s*curr_right:\s*\(([-+\d.eE]+)\)/);
      if(!viewport)return;const left=Number(viewport[1]),right=Number(viewport[2]);if(!Number.isFinite(left)||!(right>left))return;
      const rect=plot.canvas.getBoundingClientRect(),scale=rect.width/plot.canvas.width;
      const key=[left,right,plot.x,plot.y,plot.width,plot.height,rect.width,rect.height,active].join(':');if(key===last)return;last=key;
      Object.assign(layer.style,{display:'block',left:(rect.left+plot.x*scale)+'px',top:(rect.top+(plot.canvas.height-plot.y-plot.height)*scale)+'px',width:plot.width*scale+'px',height:plot.height*scale+'px'});
      for(const guide of guides){const selected=guide.indices.includes(active),position=(guide.time-left*Number(end))/((right-left)*Number(end));guide.line.style.left=(position*100)+'%';guide.line.style.width=selected?'3px':'1px';guide.line.style.background=selected?'#2c60ef':'rgba(100,116,139,.38)';guide.line.style.display=position>=0&&position<=1?'block':'none';guide.label.style.color=selected?'#2c60ef':'rgba(100,116,139,.55)';guide.label.style.fontWeight=selected?'600':'400';}
    }finally{busy=false;}
  }
  // Read the native viewport once per browser frame, with only one query in flight.
  // The renderer resolves its state query during drawing, before the browser paints.
  let frame;
  async function tick(){
    try{await draw();}catch{}
    if(!disposed)frame=requestAnimationFrame(tick);
  }
  frame=requestAnimationFrame(tick);
  window.addEventListener('pagehide',dispose,{once:true});
  /** Release the annotation layer when its owning iframe is discarded. */
  function dispose(){disposed=true;cancelAnimationFrame(frame);layer.remove();window.removeEventListener('pagehide',dispose);}
  return {select(index){active=index;draw().catch(()=>{});},dispose};
};
