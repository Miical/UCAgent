/** Main-tab restoration retains its selected conversation across scoped views and page reloads. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/chat.js','utf8');
const start=source.indexOf("document.addEventListener('flowcanvas-case-close'");
const handler=source.slice(start,source.indexOf('/** Load sessions independently',start));
/** Execute the real close handler with a minimal history/DOM transport. */
function fixture(remembered,saved,rows){
  const listeners={},chosen=[],storage=new Map([['flowcanvas.main.session',saved]]);let created=0;
  const context={caseContext:{testcase:{id:'case'}},caseSwitch:0,caseSwitching:false,ordinarySession:remembered,caseReady:Promise.resolve(),session:{id:'scoped'},
    scopedStorageKey:key=>key,localStorage:{getItem:key=>storage.get(key)},document:{addEventListener:(name,callback)=>listeners[name]=callback},saveDraft(){},saveQuote(){},controls(){},
    list:async()=>rows,choose:async(id,internal)=>chosen.push([id,internal]),create:async()=>{created++;},error:message=>{throw Error(message);}};
  vm.createContext(context);vm.runInContext(handler,context);
  return {context,chosen,get created(){return created;},close:async()=>{listeners['flowcanvas-case-close']();await context.caseReady;}};
}
test('return to the previous main conversation without creating another one',async()=>{
  const f=fixture('main','main',[{id:'main'},{id:'scoped',case_binding:{}}]);await f.close();
  assert.deepEqual(f.chosen,[['main',true]]);assert.equal(f.created,0);
});
test('shared history selected on main remains its return destination',async()=>{
  const f=fixture('shared','shared',[{id:'main'},{id:'shared',case_binding:{case_id:'other'}}]);await f.close();
  assert.deepEqual(f.chosen,[['shared',true]]);assert.equal(f.created,0);
});
test('refresh recovers the main conversation from independent persistent selection',async()=>{
  const f=fixture(null,'main',[{id:'main'},{id:'scoped',case_binding:{}}]);await f.close();
  assert.deepEqual(f.chosen,[['main',true]]);assert.equal(f.created,0);
});
test('an intentional main draft stays a draft',async()=>{
  const f=fixture('__new__','__new__',[{id:'main'}]);await f.close();
  assert.equal(f.chosen.length,0);assert.equal(f.created,1);
});
test('a deleted selection falls back to existing main history',async()=>{
  const f=fixture('deleted','deleted',[{id:'scoped',case_binding:{}},{id:'main'}]);await f.close();
  assert.deepEqual(f.chosen,[['main',true]]);assert.equal(f.created,0);
});
test('a newer tab switch prevents a delayed main restoration',async()=>{
  const f=fixture('main','main',[{id:'main'}]);let resolve;
  f.context.list=()=>new Promise(done=>resolve=done);
  const closing=f.close();f.context.caseSwitch++;resolve([{id:'main'}]);await closing;
  assert.equal(f.chosen.length,0);assert.equal(f.created,0);
});
