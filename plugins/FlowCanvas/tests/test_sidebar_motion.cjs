/** Sidebar folding clips fixed-width content and releases geometry without changing saved state. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
/** Model only the live grid width and timer lifecycle exercised by the animation helper. */
function fixture(collapsed=false,reduced=false){
  const timers=new Map(),classes=new Set(collapsed?['collapsed']:[]),paneClasses=new Set(),properties=new Map();let next=0;
  const classList=set=>({contains:key=>set.has(key),add:key=>set.add(key),remove:key=>set.delete(key)});
  const container={classList:classList(classes),style:{transition:''},getBoundingClientRect:()=>({width:1000})};
  const pane={classList:classList(paneClasses),style:{setProperty:(key,value)=>properties.set(key,value),removeProperty:key=>properties.delete(key)},getBoundingClientRect:()=>({width:classes.has('collapsed')?0:320})};
  const context={window:{matchMedia:()=>({matches:reduced})},setTimeout:callback=>{timers.set(++next,callback);return next;},clearTimeout:id=>timers.delete(id)};
  vm.createContext(context);vm.runInContext(fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/sidebar-motion.js','utf8'),context);
  return {container,pane,classes,paneClasses,properties,timers,begin:closing=>context.window.FlowCanvasSidebarMotion(container,pane,'motion','collapsed',closing)};
}
test('closing keeps content width while the outside grid collapses',()=>{
  const f=fixture();f.begin(true);f.classes.add('collapsed');
  assert.equal(f.properties.get('--sidebar-content-width'),'320px');assert.ok(f.paneClasses.has('sidebar-content-fixed'));
  [...f.timers.values()][0]();assert.equal(f.properties.size,0);assert.ok(!f.paneClasses.has('sidebar-content-fixed'));assert.ok(f.classes.has('collapsed'));
});
test('opening measures the expanded target without changing the actual collapsed state',()=>{
  const f=fixture(true);f.begin(false);
  assert.ok(f.classes.has('collapsed'));assert.equal(f.properties.get('--sidebar-content-width'),'320px');assert.ok(f.classes.has('motion'));
});
test('rapid reverse cancels old cleanup and keeps only the latest animation',()=>{
  const f=fixture();f.begin(true);f.classes.add('collapsed');f.begin(false);
  assert.equal(f.timers.size,1);assert.equal(f.properties.get('--sidebar-content-width'),'320px');
  [...f.timers.values()][0]();assert.equal(f.timers.size,0);assert.ok(!f.classes.has('motion'));
});
test('reduced motion does not freeze content or schedule a transition',()=>{
  const f=fixture(false,true);f.begin(true);assert.equal(f.timers.size,0);assert.equal(f.properties.size,0);assert.ok(!f.classes.has('motion'));
});
