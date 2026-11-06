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
 const context={$:get,active:new Set(['running']),session:{status:'running'},busy:false,models:[{}],
  backendCatalogs:[],backendChoice:'codex',paintBackends(){},paintPermissions(){},updateSpeed(){}};
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
