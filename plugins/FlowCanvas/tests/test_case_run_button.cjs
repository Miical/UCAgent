/* Run availability follows each case command, not a removed project runner. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/flowcanvas/web/targets.js'),'utf8');
const start=source.indexOf('  function paintRunButtons(){');
const code=source.slice(start,source.indexOf('  async function refreshRuns()',start));
function fixture(targets){
  const buttons=targets.map(node=>({dataset:{caseRun:node.id,caseUnavailable:String(node.lifecycle!=='ready'),runLabel:'运行测试用例'},classList:{toggle(){}},setAttribute(key,value){this[key]=value;}}));
  const context={caseMode:true,panel:{querySelectorAll:()=>buttons},data:{targets},pendingRun:null,runCases:new Set(),runsLoaded:true};
  vm.createContext(context);vm.runInContext(code,context);return {buttons,context};
}
test('per-case runner enables the button without a project-level runner',()=>{
  const f=fixture([{id:'ready',lifecycle:'ready',runner:{command:'pytest {case}'}}]);
  f.context.paintRunButtons();assert.equal(f.buttons[0].disabled,false);
  assert.equal(f.buttons[0].title,'运行测试用例');
});
test('missing commands disable only their own case and explain why',()=>{
  const f=fixture([{id:'a',lifecycle:'ready',runner:{command:'pytest {case}'}},{id:'b',lifecycle:'ready'}]);
  f.context.paintRunButtons();assert.equal(f.buttons[0].disabled,false);assert.equal(f.buttons[1].disabled,true);
  assert.equal(f.buttons[1].title,'此用例尚未配置运行命令');
});
test('active runs, pending starts and unavailable cards remain locked',()=>{
  const f=fixture([{id:'a',lifecycle:'ready',runner:{command:'run {case}'}}]);
  f.context.runCases.add('other');f.context.paintRunButtons();assert.equal(f.buttons[0].disabled,true);
  f.context.runCases.clear();f.context.pendingRun='a';f.context.paintRunButtons();assert.equal(f.buttons[0].disabled,true);
  f.context.pendingRun=null;f.buttons[0].dataset.caseUnavailable='true';f.context.paintRunButtons();assert.equal(f.buttons[0].disabled,true);
  f.buttons[0].dataset.caseUnavailable='false';f.context.runsLoaded=false;f.context.paintRunButtons();assert.equal(f.buttons[0].disabled,true);
});
