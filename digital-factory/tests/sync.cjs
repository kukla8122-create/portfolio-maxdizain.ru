const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../index.html'),'utf8');
const script=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m=>m[1]).find(s=>s.includes("'use strict'"));
new vm.Script(script);new vm.Script(fs.readFileSync(require('node:path').join(__dirname,'../sw.js'),'utf8'));
const data=new Map(),storage=new Map();let fail=false;
class Query{
 constructor(table){this.table=table;this.filters=[];this.action='read'}
 eq(k,v){this.filters.push(r=>r[k]===v);return this}is(k,v){return this.eq(k,v)}
 select(){return this}order(){return this}limit(){return this}maybeSingle(){this.single=true;return this}
 insert(row){this.action='insert';this.row=row;return this}update(row){this.action='update';this.row=row;return this}
 delete(){this.action='delete';return this}upsert(row){this.action='upsert';this.row=row;return this}
 then(ok,bad){return Promise.resolve().then(()=>{
  if(fail)return {error:{code:'NETWORK'}};
  if(this.table!=='factory_tasks')return {data:this.single?null:[],error:null};
  const rows=[...data.values()].filter(r=>this.filters.every(f=>f(r)));
  if(this.action==='insert'){if(data.has(this.row.client_id))return {error:{code:'23505'}};data.set(this.row.client_id,{...this.row});return {data:[this.row],error:null}}
  if(this.action==='update')for(const r of rows)data.set(r.client_id,{...r,...this.row});
  if(this.action==='delete')for(const r of rows)data.delete(r.client_id);
  return {data:this.single?(rows[0]||null):rows.map(r=>({...r})),error:null};
 }).then(ok,bad)}
}
const els=new Map();const el=()=>({textContent:'',className:'',classList:{toggle(){},add(){},remove(){}},style:{}});
const ctx={console,crypto:require('node:crypto').webcrypto,Date,Map,Set,JSON,Intl,Number,Promise,Error,
 document:{querySelector(s){if(!els.has(s))els.set(s,el());return els.get(s)},querySelectorAll(){return[]},addEventListener(){}},
 window:{supabase:{createClient(){return {from:t=>new Query(t),removeChannel:async()=>{}}}},addEventListener(){}},
 localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},
 setTimeout:()=>1,clearTimeout(){},renderHeader(){},renderAgents(){},renderFocus(){},renderTab(){},renderAll(){},renderFlatWorld(){},renderer:{},};
vm.createContext(ctx);
const prefix=script.slice(0,script.indexOf('function renderHeader()'));
vm.runInContext(prefix+`globalThis.api={normalize,cloudTaskRow,pushTasksToCloud,switchAccount,resolveConflict,get:()=>state,set:(s)=>{state=normalize(s);cloudUser={id:'A'};state.pendingCloud.owner='A';storageKey=STORAGE+'_user_A';taskSnapshot=new Map(state.tasks.map(t=>[t.id,JSON.stringify(t)]))}};})();`,ctx);
const api=ctx.api;
function task(title='Original'){return {id:'task-1',title,project:'',notes:'',agent:'dispatcher',priority:'mid',due:'',katya:false,status:'open',source:'user'}}
function setup(t,base,deleted=false){data.clear();fail=false;api.set({tasks:deleted?[]:[t],logs:[],cloudBase:base?{'task-1':base}:{},pendingCloud:{owner:'A',updates:deleted?{}:{'task-1':t},deleted:deleted?{'task-1':1}:{},logs:{}}});if(base)data.set('task-1',{...base});}
async function run(){
 assert.throws(()=>api.normalize({tasks:[{...task(),due:'2026-02-31'}]}));
 assert.throws(()=>api.normalize({tasks:[task(),task()]}));
 assert.throws(()=>api.normalize({tasks:[{...task(),id:'__proto__'}]}));
 setup(task());await api.pushTasksToCloud();assert.equal(data.get('task-1').title,'Original');assert.equal(Object.keys(api.get().pendingCloud.updates).length,0);
 const base=api.cloudTaskRow(task());setup(task('My edit'),base);await api.pushTasksToCloud();assert.equal(data.get('task-1').title,'My edit');
 setup(task('My edit'),base);data.get('task-1').title='Other device';await api.pushTasksToCloud();assert.equal(data.get('task-1').title,'Other device');assert(api.get().tasks.some(t=>t.title==='My edit — конфликт: моя версия'));assert(api.get().tasks.some(t=>t.title==='Other device'));await api.pushTasksToCloud();assert.equal(data.size,2);
 setup(task(),base,true);data.get('task-1').title='Other edit';await api.pushTasksToCloud();assert.equal(data.get('task-1').title,'Other edit');assert.equal(api.get().tasks[0].title,'Other edit');
 setup(task(),base,true);await api.pushTasksToCloud();assert.equal(data.size,0);
 setup(task('Offline'),base);fail=true;await assert.rejects(api.pushTasksToCloud());assert.equal(Object.keys(api.get().pendingCloud.updates).length,1);fail=false;await api.pushTasksToCloud();assert.equal(data.get('task-1').title,'Offline');
 setup(task('Private A'),base);await api.switchAccount({id:'B'});assert.equal(api.get().tasks.length,0);assert.equal(api.get().pendingCloud.owner,'B');await api.switchAccount({id:'A'});assert.equal(api.get().tasks[0].title,'Private A');await api.switchAccount(null);assert.equal(api.get().tasks.length,0);
 assert(!html.includes('SEA-22'));assert(!script.slice(script.indexOf('function agentDecision'),script.indexOf('// ---------- PWA')).includes('addLog'));
 console.log('PASS: syntax, import validation, insert, update, edit conflict, deletion conflict, deletion, offline retry, account isolation, public seed removal, simulated journal removal');
}
run().catch(e=>{console.error(e);process.exitCode=1});
