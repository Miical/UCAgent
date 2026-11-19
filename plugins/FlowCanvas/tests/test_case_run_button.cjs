/* Run availability follows each case command, not a removed project runner. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../src/flowcanvas/web/targets.js'),'utf8');
const start=source.indexOf('  function paintRunButtons(){');
const code=source.slice(start,source.indexOf('  async function refreshRuns()',start));
function fixture(targets){
  const fakeButton=(node={})=>({
    dataset:{caseRun:node.id,caseUnavailable:String(node.lifecycle!=='ready'),runLabel:'运行测试用例'},
    classList:{toggle(){},add(){}},setAttribute(key,value){this[key]=value;},
    querySelector(){return this.caption;},append(caption){this.caption=caption;},
  });
  const buttons=targets.map(fakeButton),detail=[];
  const context={caseMode:true,batchStarting:false,batch:null,paintBatchSelection(){},panel:{querySelectorAll:selector=>selector==='[data-case-run]'?buttons:[]},
    document:{querySelectorAll:()=>detail},data:{targets},pendingRun:null,runCases:new Set(),runsLoaded:true,
    el:(_tag,_class,textContent)=>({textContent,setAttribute(){}}),queueMicrotask,
    smallAction:(_label,_path,onclick)=>Object.assign(fakeButton(),{onclick}),
  };
  vm.createContext(context);vm.runInContext(code,context);return {buttons,detail,context};
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


test('the inspector and list share the run lock and only start one execution',async()=>{
  const node={id:'case',lifecycle:'ready',runner:{command:'pytest {case}'}};
  const {context,buttons,detail}=fixture([node]);
  const events=[],requests=[];
  let finish;
  Object.assign(context,{
    project:{id:'p'},
    fetch:(url,options)=>{requests.push([url,options]);return new Promise(resolve=>{finish=resolve;});},
    CustomEvent:class {constructor(type,options){this.type=type;this.detail=options.detail;}},
  });
  context.document.dispatchEvent=event=>{events.push(event);context.runCases.add(event.detail.run.case_id);};
  const fromList=context.createRunButton(node),fromDetail=context.createRunButton(node,()=>{});
  buttons[0]=fromList;detail.push(fromDetail);
  context.paintRunButtons();
  assert.equal(fromList.disabled,false);
  assert.equal(fromDetail.disabled,false);
  const starting=fromDetail.onclick();
  assert.equal(fromList.disabled,true);
  assert.equal(fromDetail.disabled,true);
  assert.equal(fromDetail.caption.textContent,'运行中');
  await fromList.onclick();
  assert.equal(requests.length,1);
  assert.equal(requests[0][0],'/api/chat/projects/p/test-runs');
  assert.deepEqual(JSON.parse(requests[0][1].body),{case_id:'case'});
  finish({ok:true,json:async()=>({id:'run',case_id:'case'})});
  await starting;
  assert.equal(events[0].type,'flowcanvas-test-run');
  context.runCases.clear();context.paintRunButtons();
  assert.equal(fromList.disabled,false);
  assert.equal(fromDetail.disabled,false);
  assert.equal(fromDetail.caption.textContent,'运行测试用例');
});


test('a pending rerun preserves the last completed result until it finishes',async()=>{
  const runCode=source.slice(source.indexOf('  async function refreshRuns()'),source.indexOf('  /** Load associations'));
  const previous={id:'first',case_id:'a',status:'completed',test_result:{status:'passed'}};
  const pending={id:'second',case_id:'a',status:'running',test_result:{status:'running'}};
  let rows=[pending,previous,{...pending,case_id:'new'}], renders=0;
  const context=vm.createContext({
    caseMode:true, project:{id:'p'}, runsChecking:false, runsLoaded:true,
    caseResults:new Map([['a',previous]]), runCases:new Set(),
    caseGrouping:'result', data:{}, paintRunButtons(){},
    fetch:async()=>({ok:true,json:async()=>rows}),render(){renders++;},
  });
  vm.runInContext(runCode,context);
  await context.refreshRuns();
  assert.equal(context.caseResults.get('a'),previous);
  assert.equal(context.caseResults.has('new'),false);
  assert.equal(context.runCases.has('new'),true);
  assert.equal(renders,0);

  rows=[{...pending,status:'failed',test_result:{status:'failed'}},previous];
  await context.refreshRuns();
  assert.equal(context.caseResults.get('a').test_result.status,'failed');
  assert.equal(context.runCases.size,0);
  assert.equal(renders,1);
});


test('target grouping can show all associations without repeating a case within a group',()=>{
  const context=vm.createContext({});
  vm.runInContext(source.slice(0,source.indexOf('function mountDefinitionPanel')),context);
  const cases=[{id:'a',category:'directed'},{id:'b',category:'data_pattern'},{id:'c',category:'basic_api'},{id:'d',category:'boundary'}];
  const targets={
    targets:[
      {id:'root1',name:'输入接口',kind:'group',parent:null},
      {id:'point1',name:'驱动',kind:'point',parent:'root1'},
      {id:'leaf1',name:'合法输入',kind:'testpoint',parent:'point1'},
      {id:'leaf2',name:'边界输入',kind:'testpoint',parent:'point1'},
      {id:'root2',name:'进位',kind:'group',parent:null},
      {id:'point2',name:'进位链',kind:'point',parent:'root2'},
      {id:'leaf3',name:'连续进位',kind:'testpoint',parent:'point2'},
    ],
    planned:{a:['leaf3','leaf2','leaf1','root1','root2'],b:['leaf3','leaf1','root2'],c:['root1'],d:['leaf3']},
    actual:{a:['leaf3','leaf1','root1'],d:['leaf3']},
  };
  const results=new Map([['a',{test_result:{status:'passed'}}],['b',{test_result:{status:'failed'}}]]);
  const groups=(mode,scope,repeat,level)=>Object.fromEntries(Array.from(context.groupTestCases(cases,mode,scope,targets,results,repeat,level),
    group=>[group.label,Array.from(group.entries,entry=>entry.node.id)]));
  assert.deepEqual(groups('purpose'),{'接口与环境':['c'],'基本功能':['a'],'数据模式':['b'],'边界与异常':['d']});
  assert.deepEqual(groups('target','planned'),{'输入接口':['a','b','c'],'进位':['d']});
  const [primary]=context.groupTestCases(cases,'target','planned',targets,results);
  assert.deepEqual(Array.from(primary.entries[0].relatedGroups),['进位']);
  assert.deepEqual(Array.from(primary.entries[1].relatedGroups),['进位']);
  assert.deepEqual(groups('target','planned',true),{'输入接口':['a','b','c'],'进位':['a','b','d']});
  assert.deepEqual(groups('target','actual'),{'输入接口':['a'],'进位':['d'],'无本批命中记录':['b','c']});
  assert.deepEqual(groups('target','actual',true),{'输入接口':['a'],'进位':['a','d'],'无本批命中记录':['b','c']});
  assert.deepEqual(groups('target','planned',false,'point'),{'驱动':['a','b'],'进位链':['d'],'未关联功能点':['c']});
  assert.deepEqual(groups('target','planned',true,'testpoint'),{'合法输入':['a','b'],'边界输入':['a'],'连续进位':['a','b','d'],'未关联测试点':['c']});
  assert.deepEqual(groups('target','actual',true,'point'),{'驱动':['a'],'进位链':['a','d'],'无本批命中记录':['b','c']});
  const tree=context.nestCaseGroups(context.groupTestCases(cases,'target','planned',targets,results,true,'testpoint'));
  const input=tree.get('root1'),drive=input.children.get('point1');
  assert.deepEqual(Array.from(drive.children.keys()),['leaf1','leaf2']);
  assert.equal(drive.children.get('leaf1').entries.length,2);
  assert.equal(input.caseIds.size,2);
  assert.deepEqual(Array.from(tree.get('unlinked').caseIds),['c']);
  assert.deepEqual(groups('result'),{'未运行':['c','d'],'已通过':['a'],'未通过':['b']});
});
