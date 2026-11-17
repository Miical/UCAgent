/** Browser draft and main-selection storage follows global/project conversation ownership. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('plugins/FlowCanvas/src/flowcanvas/web/chat.js','utf8');
const helper=source.slice(source.indexOf("let conversationScope='global';"),source.indexOf('/** Remember the main tab'));
const start=source.indexOf("const conversationScopeReady=request('/projects')");
const init=source.slice(start,source.indexOf('/** Render numbered quote attachments',start));
/** Resolve the real scope initializer against controlled browser storage and project metadata. */
async function restore(project,values){
  const storage=new Map(Object.entries(values)),context={request:async()=>({current:project}),localStorage:{getItem:key=>storage.get(key)??null},ordinarySession:null,drafts:new Map(),quotes:new Map()};
  vm.createContext(context);vm.runInContext(helper+init,context);await vm.runInContext('conversationScopeReady',context);return context;
}
test('a new project never inherits the unsent global draft or selection',async()=>{
  const context=await restore({id:'p'},{'flowcanvas.drafts':JSON.stringify({__new__:'global draft'}),'flowcanvas.main.session':'global-chat'});
  assert.equal(context.drafts.has('__new__'),false);assert.equal(context.ordinarySession,null);
});
test('closing a project restores the saved global draft and conversation',async()=>{
  const context=await restore(null,{'flowcanvas.drafts.storage-v1.global':JSON.stringify({__new__:'global draft'}),'flowcanvas.main.session.storage-v1.global':'global-chat','flowcanvas.drafts.storage-v1.p':JSON.stringify({__new__:'project draft'})});
  assert.equal(context.drafts.get('__new__'),'global draft');assert.equal(context.ordinarySession,'global-chat');
});
test('each project restores its own independent state',async()=>{
  const values={'flowcanvas.drafts.storage-v1.a':JSON.stringify({__new__:'draft A'}),'flowcanvas.main.session.storage-v1.a':'chat-A','flowcanvas.drafts.storage-v1.b':JSON.stringify({__new__:'draft B'}),'flowcanvas.main.session.storage-v1.b':'chat-B'};
  const a=await restore({id:'a'},values),b=await restore({id:'b'},values);
  assert.equal(a.drafts.get('__new__'),'draft A');assert.equal(a.ordinarySession,'chat-A');
  assert.equal(b.drafts.get('__new__'),'draft B');assert.equal(b.ordinarySession,'chat-B');
});
test('retired unscoped drafts are ignored after the requested history reset',async()=>{
  const context=await restore(null,{'flowcanvas.drafts':JSON.stringify({__new__:'legacy draft'})});
  assert.equal(context.drafts.has('__new__'),false);
});
