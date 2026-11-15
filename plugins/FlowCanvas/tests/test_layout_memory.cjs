/** Exercise sidebar sizing and reload against the shell's actual layout handlers. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/flowcanvas/web/app.js'),'utf8');
const core=source.slice(source.indexOf('const workspace='),source.indexOf('/** Collapse the project column'));
function fixture(storage=new Map()){
  const classes=new Set(),styles=new Map();
  const workspace={clientWidth:1414,style:{setProperty:(k,v)=>styles.set(k,v),getPropertyValue:k=>styles.get(k)||'',removeProperty:k=>styles.delete(k)},classList:{contains:k=>classes.has(k),add:k=>classes.add(k),remove:k=>classes.delete(k),toggle(k,on){if(on)classes.add(k);else classes.delete(k);}}};
  const splitters=[0,1].map(i=>({dataset:{split:String(i)},setAttribute(){},setPointerCapture(){}}));
  const left={getBoundingClientRect:()=>({width:Number.parseFloat(styles.get('--project-track'))||400})};
  const chat={getBoundingClientRect:()=>({width:650})},detail={getBoundingClientRect:()=>({width:350})};
  workspace.querySelectorAll=()=>[left,chat,detail];
  const button={setAttribute(){}};
  const document={querySelector:q=>q==='.workspace'?workspace:q==='.project-pane'?left:q==='.detail-pane'?detail:splitters[1],querySelectorAll:()=>splitters,getElementById:()=>button,body:{classList:workspace.classList}};
  const context={document,localStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},setTimeout:()=>1,clearTimeout(){}};
  vm.createContext(context);vm.runInContext(core,context);
  return {context,storage,styles,classes,splitters};
}
test('dragged left width survives reload with the right sidebar collapsed or expanded',()=>{
  for(const collapsed of [true,false]){
    const storage=new Map([['flowcanvas.detailCollapsed',String(collapsed)]]);
    const first=fixture(storage);
    first.splitters[0].onpointerdown({button:0,pointerId:1,clientX:400,preventDefault(){}});
    first.splitters[0].onpointermove({clientX:480});
    first.splitters[0].onpointerup();
    const width=Number(storage.get('flowcanvas.projectWidth'));
    assert.ok(Math.abs(width-480)<.001);
    const refreshed=fixture(storage);
    assert.equal(parseFloat(refreshed.styles.get('--project-track')),width);
    refreshed.context.toggleDetails(!collapsed);
    assert.equal(parseFloat(refreshed.styles.get('--project-track')),width);
    refreshed.classes.add('project-collapsed');
    refreshed.context.toggleDetails(collapsed);
    refreshed.classes.delete('project-collapsed');
    assert.equal(parseFloat(refreshed.styles.get('--project-track')),width);
  }
});
test('reset removes remembered pixels and invalid saved values are ignored',()=>{
  const f=fixture(new Map([['flowcanvas.projectWidth','480']]));
  f.splitters[0].ondblclick();
  assert.equal(f.storage.has('flowcanvas.projectWidth'),false);
  assert.equal(f.styles.has('--project-track'),false);
  for(const value of ['NaN','Infinity','-1','0','100']){
    assert.equal(fixture(new Map([['flowcanvas.projectWidth',value]])).styles.has('--project-track'),false);
  }
});
