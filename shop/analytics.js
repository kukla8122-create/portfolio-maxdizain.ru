/* Optional Yandex Metrika, same counter as the existing site. No buyer data. */
(()=>{'use strict';
const ID=108660885,KEY='maximum_shop_analytics_v1';let consent='no',started=false;
try{consent=localStorage.getItem(KEY)||'unset'}catch{}
function safeURL(){const u=new URL(location.href);u.hash='';const p=new URLSearchParams();for(const k of ['utm_source','utm_medium','utm_campaign','utm_content','utm_term']){const v=u.searchParams.get(k);if(v&&/^[a-zA-Z0-9_-]{1,80}$/.test(v))p.set(k,v)}u.search=p.toString();return u.href}
function track(name,params={}){if(started&&consent==='yes')window.ym(ID,'reachGoal',name,params)}
function start(){if(started||consent!=='yes')return;started=true;window.ym=window.ym||function(){(window.ym.a=window.ym.a||[]).push(arguments)};window.ym.l=Date.now();const s=document.createElement('script');s.async=true;s.src='https://mc.yandex.ru/metrika/tag.js';document.head.append(s);window.ym(ID,'init',{defer:true,webvisor:false,clickmap:false,trackLinks:false,trackHash:false,accurateTrackBounce:false,sendTitle:false,disableYtm:true});window.ym(ID,'hit',safeURL(),{referer:''});const paths={'/shop/kitchen-workbook/':'kitchen-workbook','/shop/kitchen-budget/':'kitchen-budget','/shop/renovation-planner/':'renovation-planner','/shop/complete-set/':'complete-set'};if(paths[location.pathname])track('digital_product_view',{sku:paths[location.pathname]});else if(location.pathname.includes('/articles/'))track('digital_article_view');else if(location.pathname==='/shop/')track('digital_catalog_view')}
const bar=document.createElement('aside');bar.className='statistics-choice';bar.hidden=consent!=='unset';bar.setAttribute('aria-label','Статистика посещений');bar.innerHTML='<p>Разрешить статистику посещений в Яндекс Метрике? Она помогает улучшать материалы. Бесплатный PDF доступен при любом выборе. <a href="/shop/privacy/">Подробнее</a></p><button type="button" data-choice="yes">Разрешить</button> <button type="button" data-choice="no">Без статистики</button>';
document.body.append(bar);
bar.addEventListener('click',e=>{const choice=e.target.getAttribute('data-choice');if(!choice)return;consent=choice;try{localStorage.setItem(KEY,choice)}catch{}bar.hidden=true;if(choice==='yes')start();else if(started)location.reload()});
const settings=document.createElement('button');settings.type='button';settings.className='statistics-settings';settings.textContent='Настройки статистики';settings.onclick=()=>{bar.hidden=false;bar.scrollIntoView({block:'nearest'})};document.querySelector('footer .wrap')?.append(settings);
document.addEventListener('click',e=>{const a=e.target.closest('a');if(a?.getAttribute('href')==='/shop/free/kitchen-15-questions.pdf')track('digital_free_download')});
start();
})();
