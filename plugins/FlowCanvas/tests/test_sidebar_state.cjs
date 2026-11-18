/** Right sidebar visibility is shared across tabs. Left sidebar choices belong to each panel. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/app.js','utf8');
const code=source.slice(source.indexOf('let sidebarScope='),source.indexOf('// Keep the project width independent'));
/** Execute the real state controller with independent width-free layout spies. */
function fixture(storage=new Map(),heading='设计总览'){
  const classes=new Set();let right=true;
  const context={window:{},workspace:{classList:{contains:key=>classes.has(key)}},document:{getElementById:()=>({textContent:heading})},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
    toggleProject:value=>{if(value)classes.add('project-collapsed');else classes.delete('project-collapsed');},toggleDetails:value=>{right=value;}};
  vm.createContext(context);vm.runInContext(code,context);
  return {api:context.window.FlowCanvasSidebarState,storage,classes,get right(){return right;},get left(){return classes.has('project-collapsed');}};
}
test('switching projects keeps the right sidebar state',()=>{
  const f=fixture(new Map([['flowcanvas.projectCollapsed','true'],['flowcanvas.detailCollapsed','false']]));
  f.api.activate('a');assert.equal(f.left,false);assert.equal(f.right,true);
  f.classes.add('project-collapsed');f.api.save(false);
  f.api.enter('nav:验证目标');assert.equal(f.left,false);assert.equal(f.right,false);
  f.api.activate('b');assert.equal(f.left,false);assert.equal(f.right,false);
});
test('explicit choices survive returning to a panel and a page reload',()=>{
  const f=fixture();f.api.activate('a');f.classes.add('project-collapsed');f.api.save(false);
  f.api.enter('nav:测试用例');f.api.enter('nav:设计总览');assert.equal(f.left,true);assert.equal(f.right,false);
  const reload=fixture(f.storage);reload.api.activate('a');assert.equal(reload.left,true);assert.equal(reload.right,false);
  const otherView=fixture(f.storage,'验证目标');otherView.api.activate('a');assert.equal(otherView.right,false);
  otherView.api.enter('tab:module:one','a');otherView.api.save(true);
  otherView.api.enter('nav:设计总览');assert.equal(otherView.right,true);
  otherView.api.save(true);otherView.api.enter('nav:测试用例');assert.equal(otherView.right,true);
});
test('main and dedicated tabs share both open and closed states',()=>{
  const f=fixture();f.api.activate('a');
  assert.equal(f.api.enter('tab:case:one','a').rightCollapsed,true);f.api.save(false);
  assert.equal(f.api.enter('tab:case:two','a').rightCollapsed,false);
  f.api.save(true);
  assert.equal(f.api.enter('tab:case:one','a').rightCollapsed,true);
  f.api.enter('nav:设计总览');assert.equal(f.right,true);
  f.api.save(false);
  assert.equal(f.api.enter('tab:module:one','a').rightCollapsed,false);
  const reload=fixture(f.storage);
  assert.equal(reload.api.enter('tab:case:two','a').rightCollapsed,false);
});
test('late project activation does not override an already opened detail panel',()=>{
  const f=fixture();f.api.enter('tab:module:one','a');f.classes.add('project-collapsed');f.api.save(false);
  f.api.activate('a');assert.equal(f.left,true);
  assert.equal(f.api.enter('tab:module:one','a').rightCollapsed,false);
});

test('global navigation keeps the current main-chat sidebar state',()=>{
  const storage=new Map([['flowcanvas.sidebar-state.v1.global.nav:设计总览',JSON.stringify({leftCollapsed:false,rightCollapsed:false})]]);
  const f=fixture(storage);f.api.activate(null);assert.equal(f.right,true);
  f.api.enter('nav:测试用例');f.api.enter('nav:设计总览');assert.equal(f.right,true);
  f.api.save(false);assert.equal(f.api.enter('nav:验证目标').rightCollapsed,false);
  // An explicit open choice is shared on reload, rather than tied to a left navigation item.
  const reload=fixture(storage);reload.api.activate(null);assert.equal(reload.right,false);
});
