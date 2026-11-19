/* Running-state refresh closes locked settings while preserving usable menus. */
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/flowcanvas/web/chat.js'),'utf8');
test('repeated running updates preserve attachment and workflow menus',()=>{
 const nodes=new Map();
 const get=id=>{
  if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,open:true,classList:{toggle(){}},
   matches(){return this.open;},hidePopover(){this.open=false;},setAttribute(){},focus(){}});
  return nodes.get(id);
 };
 const context={$:get,document:{querySelector:()=>get('workspace')},active:new Set(['running']),
  session:{status:'running'},busy:false,caseSwitching:false,models:[{}],
  backendCatalogs:[],backendChoice:'codex',paintBackends(){},paintPermissions(){},updateSpeed(){},
  updateWorkflowMenuActions(){},closeWorkflowFolder(){}};
 vm.createContext(context);
 const close=source.split('\n').find(line=>line.startsWith('function closePicker('));
 const start=source.indexOf('function controls(){'),end=source.indexOf('/** Persist permission choices',start);
 vm.runInContext(close+'\n'+source.slice(start,end),context);
 for(let i=0;i<3;i++)context.controls();
 for(const name of ['attach','workflow','session']){
  assert.equal(get(name+'-popover').open,true);assert.equal(get(name+'-popover').hidden,false);
 }
 for(const name of ['model','backend','permission']){
  assert.equal(get(name+'-popover').open,false);assert.equal(get(name+'-popover').hidden,true);
 }
 context.closePicker();
 for(const name of ['attach','workflow','session'])assert.equal(get(name+'-popover').open,false);
});

test('the open workflow menu follows model loading and registration',()=>{
 const ready={item:{id:'ready',name:'可运行',can_start:true},row:{classList:{toggle(){}}},attach:{classList:{toggle(){}}},direct:{}};
 const unavailable={...ready,item:{id:'missing',name:'不可运行',can_start:false},direct:{}};
 const context=vm.createContext({
  $:()=>({hidden:false}),workflowMenuActions:[ready,unavailable],session:{permission_mode:'workspace'},
  backendCatalogs:[],backendChoice:'codex',models:[],catalogPending:true,catalogError:''
 });
 const start=source.indexOf('function updateWorkflowMenuActions(){');
 const end=source.indexOf('/** Read the curated menu',start);
 vm.runInContext(source.slice(start,end),context);
 context.updateWorkflowMenuActions();
 assert.equal(ready.direct.title,'正在加载模型…');
 assert.equal(ready.direct.disabled,true);
 context.models=[{}];context.catalogPending=false;
 context.updateWorkflowMenuActions();
 assert.equal(ready.direct.disabled,false);
 assert.equal(unavailable.direct.disabled,true);
 assert.equal(unavailable.direct.title,'工作流暂不可用');
});
