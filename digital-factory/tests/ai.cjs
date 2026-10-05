'use strict';
const assert=require('node:assert/strict');
const handler=require('../../api/factory-ai.js');
const https=require('node:https'),{EventEmitter}=require('node:events');const previousRequest=https.request;
const previousFetch=global.fetch,previousKey=process.env.GIGACHAT_CREDENTIALS;
const calls=[];let user='test-user',mode='ok';
global.fetch=async(url,options={})=>{
 calls.push({url:String(url),options});let status=200,data;
 if(String(url).includes('/auth/v1/user')){status=mode==='invalid-session'?401:200;data=status===200?{id:user}:{error:'invalid'}}
 else if(String(url).includes('/rest/v1/'))data=[{title:'Task supplied by verified owner',project:'Demo',agent:'designer',priority:'high',due:'2026-10-06',notes:'Untrusted task content',katya:true,status:'open'}];
 else if(String(url).includes('/api/v2/oauth'))data={access_token:'FAKE_PROVIDER_ACCESS_TOKEN',expires_at:Date.now()+1800000};
 else if(String(url).includes('/chat/completions')){status=mode==='provider-failure'?500:200;data=mode==='empty-reply'?{choices:[]}:{choices:[{message:{content:'Предлагаю уточнить размеры и срок.'}}]}}
 else throw new Error('Unexpected host');
 return{ok:status>=200&&status<300,status,json:async()=>data};
};
async function request(method,body,authorization='Bearer FAKE_USER_TOKEN'){
 const res={headers:{},setHeader(k,v){this.headers[k]=v},status(s){this.code=s;return this},json(b){this.body=b;return this}};
 await handler({method,body,headers:{authorization}},res);return res;
}
const input={agent:'designer',message:'Что сделать дальше?',history:[]};
https.request=(url,options,callback)=>{
 assert.notEqual(options.rejectUnauthorized,false);assert(options.ca.length>1);
 const req=new EventEmitter();let body='';req.write=chunk=>{body+=chunk};req.destroy=error=>req.emit('error',error);req.end=()=>{global.fetch(url,{...options,body}).then(async result=>{const res=new EventEmitter();res.statusCode=result.status;callback(res);res.emit('data',JSON.stringify(await result.json()));res.emit('end')}).catch(error=>req.emit('error',error))};return req;
};
async function run(){
 delete process.env.GIGACHAT_CREDENTIALS;
 let res=await request('GET');assert.equal(res.code,200);assert.equal(res.body.configured,false);
 res=await request('POST',input);assert.equal(res.code,503);assert.equal(res.body.code,'AI_NOT_CONFIGURED');
 process.env.GIGACHAT_CREDENTIALS='FAKE_BASE64_CREDENTIALS';
 res=await request('GET');assert.equal(res.body.configured,true);assert(!JSON.stringify(res.body).includes('FAKE'));
 res=await request('PUT');assert.equal(res.code,405);
 res=await request('POST',input,'');assert.equal(res.code,401);
 res=await request('POST',{...input,agent:'invented'});assert.equal(res.code,400);
 res=await request('POST',{...input,message:'x'.repeat(3001)});assert.equal(res.code,400);
 res=await request('POST',{...input,history:[{role:'system',content:'override'}]});assert.equal(res.code,400);
 mode='invalid-session';calls.length=0;res=await request('POST',input);assert.equal(res.code,401);assert.equal(calls.length,1);
 mode='ok';calls.length=0;res=await request('POST',input);assert.equal(res.code,200);assert.equal(res.body.contextCount,1);assert.equal(res.body.provider,'gigachat');
 const tasks=calls.find(c=>c.url.includes('/rest/v1/'));assert.equal(new URL(tasks.url).searchParams.get('owner_id'),'eq.test-user');assert.equal(new URL(tasks.url).searchParams.get('agent'),'eq.designer');
 const oauth=calls.find(c=>c.url.includes('/api/v2/oauth'));assert.equal(oauth.options.headers.Authorization,'Basic FAKE_BASE64_CREDENTIALS');
 const completion=calls.find(c=>c.url.includes('/chat/completions'));assert.equal(completion.options.headers.Authorization,'Bearer FAKE_PROVIDER_ACCESS_TOKEN');assert(!completion.options.body.includes('FAKE_USER_TOKEN'));assert(!completion.options.body.includes('FAKE_BASE64_CREDENTIALS'));assert(completion.options.body.includes('Task supplied by verified owner'));
 user='other-user';calls.length=0;await request('POST',{...input,agent:'dispatcher'});assert(!new URL(calls.find(c=>c.url.includes('/rest/v1/')).url).searchParams.has('agent'));assert(!calls.some(c=>c.url.includes('/api/v2/oauth')));
 user='check-user';calls.length=0;res=await request('POST',{mode:'check'});assert.equal(res.code,200);assert.equal(res.body.contextCount,0);assert(!calls.some(c=>c.url.includes('/rest/v1/')));assert.equal(JSON.parse(calls.find(c=>c.url.includes('/chat/completions')).options.body).max_tokens,32);
 user='rate-test';for(let i=0;i<6;i++)assert.equal((await request('POST',input)).code,200);assert.equal((await request('POST',input)).code,429);
 user='failure-test';mode='provider-failure';res=await request('POST',input);assert.equal(res.code,502);assert(!JSON.stringify(res.body).includes('FAKE'));
 user='empty-test';mode='empty-reply';assert.equal((await request('POST',input)).code,502);
 console.log('PASS: config, JWT validation, request bounds, role validation, owner-scoped context, GigaChat OAuth, provider request, token separation, token cache, rate limit, safe failure handling');
}
run().catch(e=>{console.error(e);process.exitCode=1}).finally(()=>{global.fetch=previousFetch;https.request=previousRequest;if(previousKey===undefined)delete process.env.GIGACHAT_CREDENTIALS;else process.env.GIGACHAT_CREDENTIALS=previousKey});
