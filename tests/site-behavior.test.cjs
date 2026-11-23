const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const test=require('node:test');
const source=fs.readFileSync('attribution.js','utf8');
function campaign(search,storage=new Map(),blocked=false){
 const ctx={window:{},location:{search},URLSearchParams,sessionStorage:{getItem:k=>{if(blocked)throw Error();return storage.get(k);},setItem:(k,v)=>{if(blocked)throw Error();storage.set(k,v);}}};
 vm.runInNewContext(source,ctx);return JSON.parse(JSON.stringify(ctx.window.maximumAttribution.get()));
}
test('Campaign survives internal navigation and new source replaces previous campaign',()=>{
 const storage=new Map();campaign('?utm_source=telegram&utm_campaign=alexey',storage);
 assert.equal(campaign('',storage).utm_source,'telegram');assert.equal(campaign('',storage).utm_campaign,'alexey');
 const next=campaign('?utm_source=vk',storage);assert.equal(next.utm_source,'vk');assert.equal(next.utm_campaign,'');
});
test('Attribution tolerates blocked or corrupt storage and ignores unrelated values',()=>{
 assert.equal(campaign('?utm_source=vk',new Map(),true).utm_source,'vk');
 assert.equal(campaign('',new Map([['maximum-campaign-v1','broken']])).utm_source,'');
 assert.equal(campaign('',new Map([['maximum-campaign-v1','null']])).utm_source,'');
 const values=campaign('?phone=79999999999&name=Test&utm_campaign=%3Cscript%3E');
 assert.equal(values.phone,undefined);assert.equal(values.name,undefined);assert.equal(values.utm_campaign,'script');
});
test('Personal MAX contact and MAX channel count as different actions, only once',()=>{
 let listener;const events=[];const ctx={window:{gtag:(...args)=>events.push(args)},location:new URL('https://portfolio-maxdizain.ru/'),URL,document:{addEventListener:(_,fn)=>listener=fn}};
 vm.runInNewContext(fs.readFileSync('marketing.js','utf8'),ctx);
 const click=href=>{events.length=0;listener({target:{closest:()=>({getAttribute:()=>href})}});return JSON.parse(JSON.stringify(events));};
 let e=click('https://max.ru/channel_maxmebel_52');assert.equal(e.length,1);assert.equal(e[0][1],'channel_open');
 e=click('https://max.ru/u/known-contact');assert.equal(e.length,1);assert.equal(e[0][1],'contact_click');assert.equal(e[0][2].contact_channel,'max_chat');
 e=click('https://vk.me/maximummebel');assert.equal(e.length,1);assert.equal(e[0][2].contact_channel,'vk_chat');
 e=click('tel:+79159530929');assert.equal(e.length,1);assert.equal(e[0][2].contact_channel,'phone');assert(!JSON.stringify(e).includes('79159530929'));
 assert.equal(click('https://max.ru.example.com/fake').length,0);
 assert.equal(click('/?service=design#contact')[0][1],'contact_section_open');
 assert.equal(click('/projects/alexey-apartment-52m/documents/alexey-complete-project.pdf#page=18')[0][1],'project_pdf_open');
});
test('Calculator bounds area, calculates price and keeps selected listing',()=>{
 const elements={};for(const id of ['area','areaRange','sockets','city','avito','result','totalComment','socketsComment']) elements[id]={value:id==='city'?'nizhniy':'40',checked:false,textContent:'',addEventListener(name,fn){this[name]=fn;}};
 const script=fs.readFileSync('kalkulyator-dizayn-proekta.html','utf8').match(/<script>\s*\(function\(\)\{[\s\S]*?<\/script>/)[0].replace(/<\/?script>/g,'');
 vm.runInNewContext(script,{document:{getElementById:id=>elements[id]},Intl,URL,URLSearchParams});
 assert.match(elements.result.textContent,/18\s000/);
 elements.area.value='52';elements.area.input();assert.match(elements.result.textContent,/23\s400/);
 elements.area.value='-10';elements.area.input();assert.equal(elements.area.value,5);
 elements.areaRange.value='600';elements.areaRange.input();assert.equal(elements.area.value,500);
 elements.city.value='kazan';elements.city.change();assert.match(elements.avito.href,/avito.ru\/kazan\//);
});
test('Lead submission preserves attribution and reports success only on server confirmation',async()=>{
 function node(){return {value:'',checked:false,hidden:false,dataset:{},children:[],classList:{add(){},remove(){}},setAttribute(){},querySelector(){return this.label||(this.label=node());},querySelectorAll(){return this.children;},append(n){this.children.push(n);},addEventListener(name,fn){this[name]=fn;},reset(){this.resetCalled=true;}};}
 const ids={};for(const id of ['kitchenChoices','selectionResult','selectionStatus','selectionCount','selectionLink','copySelection','shareSelection','downloadSelection','contactForm','nameInput','phoneInput','serviceInput','contactStatus','formSuccess','privacyConsent','websiteHoneypot','requestText','copyRequest'])ids[id]=node();
 ids.nameInput.value='Проверка';ids.phoneInput.value='+79159530929';ids.serviceInput.value='Дизайн-проект';ids.privacyConsent.checked=true;
 const events=[];let body,ok=true;
 const ctx={document:{getElementById:id=>ids[id],createElement:()=>node(),querySelectorAll:()=>[]},window:{maximumAttribution:{get:()=>({utm_source:'telegram',utm_campaign:'alexey'})}},location:new URL('https://portfolio-maxdizain.ru/?service=design'),URL,URLSearchParams,Set,Date,navigator:{},localStorage:{getItem:()=>null,setItem(){}},gtag:(...args)=>events.push(args),AbortController,setTimeout,clearTimeout,fetch:async(_,options)=>{body=JSON.parse(options.body);return{ok,json:async()=>({ok})};}};
 vm.runInNewContext(fs.readFileSync('selection.js','utf8'),ctx);
 await ids.contactForm.submit({preventDefault(){}});assert.equal(body.utm_source,'telegram');assert.equal(body.utm_campaign,'alexey');assert(ids.contactForm.resetCalled);assert(events.some(e=>e[1]==='lead_submitted'));assert.equal(ids.contactForm.querySelector().disabled,false);
 events.length=0;ids.contactForm.resetCalled=false;ok=false;
 await ids.contactForm.submit({preventDefault(){}});assert(!ids.contactForm.resetCalled);assert(!events.some(e=>e[1]==='lead_submitted'));assert.match(ids.contactStatus.textContent,/Не удалось подтвердить/);assert.equal(ids.contactForm.querySelector().disabled,false);
});
