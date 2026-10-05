const CACHE='maksimum-factory-v36';
const SCOPE=new URL('./',self.location.href);
const CORE=['./','./manifest.webmanifest','./icon.svg','./icon-192.png','./icon-512.png'].map(p=>new URL(p,SCOPE).href);
const SCRIPTS=['https://cdn.jsdelivr.net/npm/three@0.152.2/build/three.min.js','https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.117.2'];
self.addEventListener('install',e=>e.waitUntil(caches.open(CACHE).then(async cache=>{await cache.addAll(CORE);await Promise.allSettled(SCRIPTS.map(url=>cache.add(url)))}).then(()=>self.skipWaiting())));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('maksimum-factory-')&&k!==CACHE).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',e=>{
 if(e.request.method!=='GET')return;
 const url=new URL(e.request.url);
 if(url.origin!==SCOPE.origin&&!SCRIPTS.includes(url.href))return;
 if(url.origin===SCOPE.origin&&!url.pathname.startsWith(SCOPE.pathname))return;
 if(e.request.mode==='navigate'){
  if(url.pathname!==SCOPE.pathname&&url.pathname!==SCOPE.pathname+'index.html')return;
  e.respondWith(fetch(e.request,{cache:'no-store'}).then(async response=>{if(response.ok){const cache=await caches.open(CACHE);await cache.put(CORE[0],response.clone())}return response}).catch(()=>caches.match(CORE[0])));
  return;
 }
 if(!CORE.includes(url.href)&&!SCRIPTS.includes(url.href))return;
 e.respondWith(caches.match(e.request).then(hit=>hit||fetch(e.request).then(async response=>{if(response.ok){const cache=await caches.open(CACHE);await cache.put(e.request,response.clone())}return response})));
});
