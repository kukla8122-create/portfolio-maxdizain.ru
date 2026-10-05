'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),{webcrypto}=require('node:crypto');
const elements=new Map();function element(){return{value:'',textContent:'',hidden:false,disabled:false,children:[],append(...items){this.children.push(...items)},replaceChildren(){this.children=[]},addEventListener(type,fn){this[type]=fn}}}
const el=id=>{if(!elements.has(id))elements.set(id,element());return elements.get(id)};
let authChanged,oauth,session={user:{id:'owner-one'}},posts=[],requests=[],grant='',failInsert=false,existing=null,holdRead=null;
const sb={auth:{getSession:async()=>({data:{session}}),onAuthStateChange:fn=>authChanged=fn},from:table=>{assert.equal(table,'factory_tasks');const chain={select(){return chain},eq(key,value){if(key==='owner_id')assert.equal(value,'owner-one');return chain},limit:async()=>({data:[{client_id:'task-one',title:'Task <script>',project:'Project',due:'2026-10-12'}]})};return chain}};
const response=(data,status=200)=>({ok:status<300,status,json:async()=>data});
const context={document:{getElementById:el,createElement:element},window:{supabase:{createClient:()=>sb},google:{accounts:{oauth2:{initTokenClient:options=>{oauth=options;return{requestAccessToken:()=>{}}},hasGrantedAnyScope:()=>true,hasGrantedAllScopes:()=>grant==='write'}}}},google:null,Intl,Date,URL,URLSearchParams,AbortSignal,crypto:webcrypto,location:{search:'?task=task-one'},setTimeout:()=>1,clearTimeout:()=>{},fetch:async(url,options={})=>{
 requests.push({url,options});if(url==='/api/factory-calendar')return response({clientId:'public-id'});
 assert.equal(options.headers.Authorization,'Bearer FAKE_GOOGLE_TOKEN');
 if(options.method==='POST'){const body=JSON.parse(options.body);posts.push(body);if(failInsert){failInsert=false;existing=body;throw Error('network failure')}return response({...body,summary:body.summary})}
 if(url.includes('/events/'))return response(existing);
 if(holdRead)return await holdRead;
 return response({items:[{summary:'Event <img>',start:{date:'2026-10-12'},htmlLink:'javascript:alert(1)'}]});
}};context.google=context.window.google;
vm.runInNewContext(fs.readFileSync('digital-factory/calendar.js','utf8'),context);
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r))};
(async()=>{
 await settle();assert.equal(el('title').value,'Task <script>');assert.equal(el('description').value,'Проект: Project');
 el('connect').onclick();assert(oauth.scope.endsWith('readonly'));await oauth.callback({access_token:'FAKE_GOOGLE_TOKEN',expires_in:3600});assert.equal(posts.length,0);
 assert.equal(el('events').children[0].children[0].textContent,'Event <img>');assert.equal(el('events').children[0].children.length,2);
 await el('eventForm').onsubmit({preventDefault(){}});assert(oauth.scope.endsWith('calendar.events.owned'));assert.equal(posts.length,0);
 grant='write';await oauth.callback({access_token:'FAKE_GOOGLE_TOKEN',expires_in:3600});
 el('end').value='2026-10-12T09:00';await el('eventForm').onsubmit({preventDefault(){}});assert.equal(posts.length,0);
 el('end').value='2026-10-12T11:00';failInsert=true;await el('eventForm').onsubmit({preventDefault(){}});await el('eventForm').onsubmit({preventDefault(){}});assert.equal(posts[0].id,posts[1].id);assert(!('attendees' in posts[0]));assert(!('notes' in posts[0]));assert(requests.some(r=>r.url.includes('sendUpdates=none')));
 await el('eventForm').onsubmit({preventDefault(){}});assert.equal(posts.length,2);
 let release;holdRead=new Promise(r=>release=r);const pending=el('refresh').onclick();await settle();session=null;authChanged('SIGNED_OUT',null);release(response({items:[{summary:'PRIVATE OLD EVENT',start:{date:'2026-10-12'}}]}));await pending;
 assert(!el('events').children.some(c=>c.children?.some(n=>n.textContent==='PRIVATE OLD EVENT')));assert.equal(el('create').disabled,true);assert.equal(el('title').value,'');
 console.log('PASS: owner-scoped tasks, safe event rendering, read then incremental write consent, date validation, no auto-create, stable retry ID, no invitations, completed draft protection, account isolation and stale-response protection');
})().catch(e=>{console.error(e);process.exitCode=1});
