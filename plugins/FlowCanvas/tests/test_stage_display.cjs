/* Parent-first presentation never rewrites UCAgent's child-first execution list. */
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../src/flowcanvas/web/chat.js'),'utf8');
const code=source.slice(source.indexOf('function workflowDisplayStages('),source.indexOf('function paintWorkflowRun(){'));
const c={};vm.createContext(c);vm.runInContext(code,c);
test('parents precede nested children while sibling order and source records stay intact',()=>{
 const stages=[{name:'compile'},{name:'child1',parent:'parent'},{name:'grandchild',parent:'child2'},
  {name:'child2',parent:'parent'},{name:'parent'},{name:'next'}];
 const before=JSON.stringify(stages),run={stages,current_stage:'grandchild'};
 const shown=c.workflowDisplayStages(run);
 assert.deepEqual(Array.from(shown,s=>s.name),['compile','parent','child1','child2','grandchild','next']);
 assert.equal(JSON.stringify(stages),before);assert.equal(run.current_stage,'grandchild');
 assert.equal(shown[4],stages[2]);
});
test('missing parents, empty lists and malformed cycles do not hide stages or loop',()=>{
 assert.equal(c.workflowDisplayStages({}).length,0);
 const stages=[{name:'orphan',parent:'absent'},{name:'one',parent:'two'},{name:'two',parent:'one'}];
 const shown=c.workflowDisplayStages({stages});
 assert.equal(shown.length,3);assert.equal(new Set(shown.map(s=>s.name)).size,3);
});
