/** First-visit sidebar defaults and explicit choices belong to each project and panel. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/app.js','utf8');
const code=source.slice(source.indexOf('let sidebarScope='),source.indexOf('// Keep the project width independent'));
/** Execute the real state controller with independent width-free layout spies. */
function fixture(storage=new Map()){
  const classes=new Set();let right=true;
  const context={window:{},workspace:{classList:{contains:key=>classes.has(key)}},document:{getElementById:()=>({textContent:'设计总览'})},
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
    toggleProject:value=>{if(value)classes.add('project-collapsed');else classes.delete('project-collapsed');},toggleDetails:value=>{right=value;}};
  vm.createContext(context);vm.runInContext(code,context);
  return {api:context.window.FlowCanvasSidebarState,storage,classes,get right(){return right;},get left(){return classes.has('project-collapsed');}};
}
test('new projects and new navigation panels always open left and collapse right',()=>{
  const f=fixture(new Map([['flowcanvas.projectCollapsed','true'],['flowcanvas.detailCollapsed','false']]));
  f.api.activate('a');assert.equal(f.left,false);assert.equal(f.right,true);
  f.classes.add('project-collapsed');f.api.save(false);
  f.api.enter('nav:验证目标');assert.equal(f.left,false);assert.equal(f.right,true);
  f.api.activate('b');assert.equal(f.left,false);assert.equal(f.right,true);
});
test('explicit choices survive returning to a panel and a page reload',()=>{
  const f=fixture();f.api.activate('a');f.classes.add('project-collapsed');f.api.save(false);
  f.api.enter('nav:测试用例');f.api.enter('nav:设计总览');assert.equal(f.left,true);assert.equal(f.right,false);
  const reload=fixture(f.storage);reload.api.activate('a');assert.equal(reload.left,true);assert.equal(reload.right,false);
});
test('each dedicated detail starts collapsed and retains only its own explicit choice',()=>{
  const f=fixture();f.api.activate('a');
  assert.equal(f.api.enter('tab:case:one','a').rightCollapsed,true);f.api.save(false);
  assert.equal(f.api.enter('tab:case:two','a').rightCollapsed,true);
  assert.equal(f.api.enter('tab:case:one','a').rightCollapsed,false);
  assert.equal(f.api.enter('tab:case:one','b').rightCollapsed,true);
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
