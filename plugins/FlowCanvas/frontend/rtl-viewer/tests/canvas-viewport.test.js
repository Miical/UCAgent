/** Check hidden/revealed and resizing canvas lifecycles without launching a browser. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createViewportFitter } from '../src/layout/canvasViewport.js'
/** Control frames and observer callbacks to reproduce iframe and splitter transitions deterministically. */
function setup(width=0,height=0) {
  let size={width,height},next=0,notify,disconnected=false;const frames=new Map(),fits=[]
  const host={isConnected:true,getBoundingClientRect:()=>size}
  class Observer { constructor(callback){notify=callback} observe(){} disconnect(){disconnected=true} }
  const fitter=createViewportFitter(host,()=>fits.push({...size}),{requestFrame:callback=>{frames.set(++next,callback);return next},cancelFrame:id=>frames.delete(id),Observer})
  return {fits,fitter,host,resize(width,height){size={width,height};notify()},frame(){const pending=[...frames.values()];frames.clear();pending.forEach(callback=>callback())},get disconnected(){return disconnected}}
}
test('does not fit a hidden iframe; fits once after it becomes visible and stable',()=>{
  const view=setup();view.frame();assert.equal(view.fits.length,0)
  view.resize(720,480);view.frame();assert.equal(view.fits.length,0);view.frame();assert.deepEqual(view.fits,[{width:720,height:480}])
  view.resize(0,0);view.frame();view.resize(820,500);view.frame();view.frame();assert.deepEqual(view.fits.at(-1),{width:820,height:500})
})
test('waits through split-pane animation and repeated fit requests',()=>{
  const view=setup(20,480);view.frame();view.resize(300,480);view.frame();view.resize(600,480);view.frame();assert.equal(view.fits.length,0);view.frame();assert.equal(view.fits.length,1)
  view.fitter.request();view.fitter.request();view.frame();view.frame();assert.equal(view.fits.length,2)
})
test('disconnect cancels pending frames and ignores later resize notifications',()=>{
  const view=setup(720,480);view.frame();view.fitter.disconnect();view.frame();view.resize(900,600);view.frame();assert.equal(view.fits.length,0);assert.equal(view.disconnected,true)
})
