const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const marketing = fs.readFileSync('marketing.js', 'utf8');
test('Yandex receives events without Google and never receives form fields or URL query', () => {
  const events = [];
  const ctx = {window:{ym:(...args)=>events.push(args)},location:new URL('https://portfolio-maxdizain.ru/?phone=secret'),URL,document:{addEventListener(){}}};
  vm.runInNewContext(marketing,ctx);
  ctx.window.maximumTrack('lead_submitted',{name:'secret',phone:'secret',message:'secret'});
  assert.equal(events[0][0],108660885);assert.equal(events[0][2],'lead_submitted');
  assert.equal(JSON.stringify(events).includes('secret'),false);
  ctx.window.gtag=()=>{throw Error('blocked');};
  assert.doesNotThrow(()=>ctx.window.maximumTrack('contact_click',{contact_channel:'phone'}));
  assert.equal(events.length,2);
});
test('Case views count on load; technical sections unobserve after visibility', () => {
  const events=[],observed=[],removed=[];let callback;
  const heading={closest:()=>({id:'electrical'})};
  const ctx={window:{ym:(...args)=>events.push(args),IntersectionObserver:class{constructor(cb){callback=cb;}observe(el){observed.push(el);}unobserve(el){removed.push(el);}}},location:new URL('https://portfolio-maxdizain.ru/projects/alexey-apartment-52m/'),URL,document:{addEventListener(){},querySelectorAll:()=>[heading]}};
  vm.runInNewContext(marketing,ctx);assert.equal(events[0][2],'portfolio_open');
  assert.equal(events.length,1);assert.equal(observed.length,1);
  callback([{isIntersecting:false,target:heading}]);assert.equal(events.length,1);
  callback([{isIntersecting:true,target:heading}]);assert.equal(events[1][2],'technical_section_view');assert.equal(events[1][3].section,'electrical');assert.equal(removed[0],heading);
});
test('Counter initializes only once with recordings disabled', () => {
  const scripts=[];const ctx={window:{},document:{createElement:()=>({}),head:{appendChild:s=>scripts.push(s)}}};
  const source=fs.readFileSync('metrika.js','utf8');vm.runInNewContext(source,ctx);vm.runInNewContext(source,ctx);
  assert.equal(scripts.length,1);assert.equal(ctx.window.ym.a.length,1);assert.equal(ctx.window.ym.a[0][0],108660885);assert.equal(ctx.window.ym.a[0][2].webvisor,false);
});
test('Old contacts address preserves campaign and section when redirecting', () => {
  const source=fs.readFileSync('links.html','utf8').match(/<script>(.*?)<\/script>/)[1];let result;
  vm.runInNewContext(source,{location:{search:'?utm_source=telegram',hash:'#contact',replace:u=>result=u}});
  assert.equal(result,'/links/?utm_source=telegram#contact');
});
