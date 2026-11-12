/** All code metrics reuse the line-coverage source projection without losing partial hits. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../frontend/rtl-viewer/src/coverageMetricRows.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('line keeps original hierarchy and code metrics classify partial source lines',async()=>{
 const {coverageMetricRows}=await modulePromise;
 const instances=[{path:'Top.child',covered:1,total:2}];
 assert.equal(coverageMetricRows({instances},'line','Top'),instances);
 const items=[{file:'Top.v',line:7,hits:1,sha256:'sha'},{file:'Top.v',line:7,hits:0,sha256:'sha'},
  {file:'Top.v',line:8,hits:0,sha256:'sha'},{file:'Top.v',line:9,hits:1,sha256:'sha'}];
 const rows=coverageMetricRows({code_metrics:{toggle:{dut:{covered:2,total:4,items}}}},'toggle','Top');
 assert.equal(rows[0].path,'Top');assert.equal(rows[0].covered,2);
 assert.deepEqual(rows[0].files[0].partial_lines,[7]);
 assert.deepEqual(rows[0].files[0].uncovered_lines,[8]);
 assert.deepEqual(rows[0].files[0].covered_lines,[9]);
 assert.deepEqual(coverageMetricRows({},'branch','Top'),[]);
});

test('native instance paths retain module hierarchy',async()=>{
 const {coverageMetricRows}=await modulePromise;
 const item={file:'child.v',line:4,hits:1,sha256:'sha',module:'Child',hierarchy:'TOP.Top_top.Top.u_child'};
 const rows=coverageMetricRows({code_metrics:{branch:{dut:{covered:1,total:1,items:[item]}}}},'branch','Top');
 assert.equal(rows[0].path,'Top.u_child');assert.equal(rows[0].module,'Child');
});

 test('all native metrics retain zero hits and unavailable metrics remain empty',async()=>{
  const {coverageMetricRows}=await modulePromise;
  for (const metric of ['branch','cond','fsm','assert']) {
   const items=[{file:'Top.sv',line:2,hits:0,hierarchy:'Top',module:'Top'},
    {file:'Top.sv',line:null,hits:1,hierarchy:'Top',module:'Top'}];
   const rows=coverageMetricRows({code_metrics:{[metric]:{dut:{items}}}},metric,'Top');
   assert.equal(rows[0].covered,1);assert.equal(rows[0].total,2);
   assert.deepEqual(rows[0].files[0].uncovered_lines,[2]);
   assert.deepEqual(rows[0].files[0].covered_lines,[]);
   assert.deepEqual(coverageMetricRows({},metric,'Top'),[]);
  }
 });
