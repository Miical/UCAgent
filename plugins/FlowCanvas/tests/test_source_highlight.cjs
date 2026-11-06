/* Whole-file links do not imply line one; explicit locations retain highlighting. */
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../frontend/local-source.js'),'utf8').replaceAll('export ','');
function fixture(){
 const nodes=[];
 const createElement=tag=>{const n={tag,children:[],className:'',open:false,offsetTop:0,clientHeight:100,
  append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
  addEventListener(){},showModal(){this.open=true;},querySelector(){return this.children.find(row=>row.className==='target-code-selected');}};
  nodes.push(n);return n;};
 const c={document:{createElement,body:{append(){}}},URL,encodeURIComponent,
  fetch:async()=>({ok:true,json:async()=>({file:'/project/file.py',content:'one\ntwo\nthree'})}),
  requestAnimationFrame:fn=>fn()};
 vm.createContext(c);vm.runInContext(code,c);return {c,nodes};
}
test('plain paths omit line references while explicit line one remains explicit',()=>{
 const {c}=fixture();
 assert.equal(c.localReference('/project/file.py').line,undefined);
 assert.equal(c.localReference('file:///project/file.py').line,undefined);
 assert.equal(c.localReference('/project/file.py:1').line,1);
 assert.equal(c.localReference('/project/file.py#L2-L3').end,3);
});
test('preview highlights only explicitly requested lines',async()=>{
 for(const [reference,expected] of [[{path:'file.py'},[]],[{path:'file.py',line:1,end:1},[1]],
  [{path:'file.py',line:2},[2]],[{path:'file.py',line:2,end:3},[2,3]]]){
  const f=fixture();await f.c.openLocalSource(reference);
  const pre=f.nodes.find(n=>n.tag==='pre');
  assert.deepEqual(pre.children.flatMap((row,index)=>row.className==='target-code-selected'?[index+1]:[]),expected);
 }
});
