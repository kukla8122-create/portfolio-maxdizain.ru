'use strict';
(()=>{
 const $=id=>document.getElementById(id),READ='https://www.googleapis.com/auth/calendar.events.owned.readonly',WRITE='https://www.googleapis.com/auth/calendar.events.owned';
 const SUPABASE_URL='https://uhyaigqizvwtsbtmvkdr.supabase.co',SUPABASE_KEY='sb_publishable_fS6uiYMTofcNuYE5DuAfmg_lUaZQc_8',FACTORY_SYNC_URL=SUPABASE_URL+'/functions/v1/factory-sync-api';
 const sb=window.supabase?.createClient(SUPABASE_URL,SUPABASE_KEY,{auth:{persistSession:true,autoRefreshToken:true}});
 let clientId='',token='',expires=0,write=false,epoch=0,userId='',tasks=[],busy=false,draft=null,expiryTimer;
 function status(text){$('status').textContent=text}
 function clear(resetDraft=false){epoch++;token='';expires=0;write=false;if(resetDraft)draft=null;clearTimeout(expiryTimer);$('events').textContent='Подключи календарь, чтобы увидеть встречи.';$('refresh').hidden=true;$('disconnect').hidden=true;$('create').disabled=true}
 function active(){return !!token&&expires>Date.now()}
 function localDate(date){const shifted=new Date(date.getTime()-date.getTimezoneOffset()*60000);return shifted.toISOString().slice(0,16)}
 $('zone').textContent='Время устройства: '+Intl.DateTimeFormat().resolvedOptions().timeZone;
 const start=new Date();start.setMinutes(0,0,0);start.setHours(start.getHours()+1);$('start').value=localDate(start);$('end').value=localDate(new Date(+start+3600000));
 async function signedIn(){const {data,error}=await sb.auth.getSession();if(error||data.session?.user?.id!==userId)throw Error('Войди в свой аккаунт фабрики заново.');return data.session}
 async function factoryPull(){
  let session=await signedIn();
  const send=token=>fetch(FACTORY_SYNC_URL,{method:'POST',cache:'no-store',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token,apikey:SUPABASE_KEY},body:JSON.stringify({action:'pull'}),signal:AbortSignal.timeout(20000)});
  let response=await send(session.access_token);
  if(response.status===401){const refreshed=await sb.auth.refreshSession();if(refreshed.error||refreshed.data.session?.user?.id!==userId)throw Error('Войди в свой аккаунт фабрики заново.');session=refreshed.data.session;response=await send(session.access_token)}
  const data=await response.json().catch(()=>({}));if(!response.ok)throw Error('Не удалось загрузить задачи фабрики.');return data;
 }
 function authorize(edit=false){
  if(!userId)return status('Сначала войди в аккаунт фабрики.');
  if(!clientId)return status('Подключение ожидает настройки Google OAuth.');
  if(!window.google?.accounts?.oauth2)return status('Окно Google ещё не загрузилось. Повтори подключение.');
  if(busy)return;busy=true;$('connect').disabled=true;const requestEpoch=epoch;
  const client=google.accounts.oauth2.initTokenClient({client_id:clientId,scope:edit?WRITE:READ,include_granted_scopes:false,
   callback:async response=>{
    busy=false;$('connect').disabled=false;if(requestEpoch!==epoch)return;
    if(response.error||!response.access_token){status('Доступ Google не предоставлен.');return}
    const granted=google.accounts.oauth2.hasGrantedAnyScope(response,edit?WRITE:READ,WRITE);
    if(!granted){status('Разрешение на календарь не предоставлено.');return}
    token=response.access_token;expires=Date.now()+Number(response.expires_in||0)*1000-30000;write=google.accounts.oauth2.hasGrantedAllScopes(response,WRITE);
    clearTimeout(expiryTimer);expiryTimer=setTimeout(()=>{clear();status('Сессия Google закончилась. Подключи календарь снова.')},Math.max(0,expires-Date.now()));
    $('refresh').hidden=false;$('disconnect').hidden=false;$('create').disabled=false;$('connect').textContent=write?'Сменить Google-аккаунт':'Разрешить создание событий';status('Google Calendar подключён.'+(edit?' Проверь поля и нажми «Создать событие» снова.':''));await listEvents();
   },error_callback:()=>{busy=false;$('connect').disabled=false;status('Окно Google закрыто или заблокировано. Повтори подключение.')}});
  client.requestAccessToken({prompt:edit?'consent':'select_account'});
 }
 async function api(path,options={}){
  await signedIn();if(!active()){clear();throw Error('Подключи Google Calendar снова.')}
  const response=await fetch('https://www.googleapis.com/calendar/v3/calendars/primary/events'+path,{...options,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},signal:AbortSignal.timeout(20000)});
  const data=await response.json();if(!response.ok){if(response.status===401){clear();throw Error('Сессия Google закончилась. Подключи календарь снова.')}if(response.status===403)throw Error('Google не разрешил доступ. Проверь разрешения календаря и включение Calendar API.');if(response.status===409)return{conflict:true};throw Error('Google Calendar временно недоступен. Повтори запрос.')}return data;
 }
 async function listEvents(){const requestEpoch=epoch;try{
  const now=new Date(),until=new Date(+now+30*86400000);$('events').textContent='Загружаем встречи…';
  const data=await api('?'+new URLSearchParams({timeMin:now.toISOString(),timeMax:until.toISOString(),singleEvents:'true',orderBy:'startTime',maxResults:'100'}));if(requestEpoch!==epoch)return;
  status('Google Calendar подключён. Встречи обновлены.'+(write?' Можно создавать события.':' Создание событий потребует дополнительного разрешения Google.'));
  $('events').replaceChildren();const entries=(data.items||[]).filter(e=>e.status!=='cancelled');if(!entries.length)$('events').textContent='В ближайшие 30 дней встреч нет.';
  for(const event of entries){const card=document.createElement('div');card.className='event';const title=document.createElement('b');title.textContent=event.summary||'Без названия';card.append(title);const when=document.createElement('div');when.textContent=event.start?.dateTime?new Date(event.start.dateTime).toLocaleString('ru-RU'):event.start?.date+' · весь день';card.append(when);if(event.htmlLink){const url=new URL(event.htmlLink);if(url.protocol==='https:'&&['calendar.google.com','www.google.com'].includes(url.hostname)){const link=document.createElement('a');link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Открыть в Google Calendar';card.append(link)}}$('events').append(card)}
  if(data.nextPageToken){const hint=document.createElement('p');hint.textContent='Показаны первые 100 событий. Остальные доступны в Google Calendar.';$('events').append(hint)}
 }catch(error){if(requestEpoch===epoch){$('events').textContent='Встречи не загружены.';status(error.message)}}}
 $('connect').onclick=()=>authorize(active()&&!write);$('refresh').onclick=listEvents;
 $('disconnect').onclick=()=>{clear();status('Локальное подключение отключено. Разрешения Google можно отозвать в настройках Google-аккаунта.');$('connect').textContent='Подключить Google Calendar'};
 $('task').onchange=()=>{draft=null;const task=tasks.find(t=>t.client_id===$('task').value);if(!task)return;$('title').value=task.title||'';$('description').value=task.project?'Проект: '+task.project:'';if(task.due){$('start').value=task.due+'T10:00';$('end').value=task.due+'T11:00'}};
 $('eventForm').addEventListener('input',()=>{draft=null;$('result').textContent=''});
 $('eventForm').onsubmit=async event=>{event.preventDefault();if(busy)return;if(!active())return authorize();if(!write)return authorize(true);
  const start=new Date($('start').value),end=new Date($('end').value);if(!Number.isFinite(+start)||!Number.isFinite(+end)||end<=start){$('result').textContent='Конец должен быть позже начала.';return}
  const body={summary:$('title').value.trim(),description:$('description').value.trim(),start:{dateTime:start.toISOString()},end:{dateTime:end.toISOString()}};if(!body.summary)return;
  const signature=JSON.stringify(body);if(!draft||draft.signature!==signature)draft={signature,id:crypto.randomUUID().replaceAll('-',''),done:false};if(draft.done){$('result').textContent='Это событие уже создано. Измени поля для новой встречи.';return}
  const pending=draft,requestEpoch=epoch;busy=true;$('create').disabled=true;$('result').textContent='Создаём событие…';
  try{let data=await api('?sendUpdates=none',{method:'POST',body:JSON.stringify({...body,id:pending.id})});if(data.conflict)data=await api('/'+pending.id);if(requestEpoch!==epoch)return;pending.done=true;$('result').textContent='Событие создано: '+(data.summary||body.summary)+'. Приглашения не отправлялись.';await listEvents()}
  catch(error){if(requestEpoch===epoch)$('result').textContent=error.message+' Повторное нажатие не создаст копию той же встречи.'}
  finally{busy=false;if(requestEpoch===epoch)$('create').disabled=!active()}
 };
 async function init(){try{
  if(!sb)throw Error('Сервис входа не загрузился. Обнови страницу.');const {data,error}=await sb.auth.getSession();if(error||!data.session?.user)throw Error('Сначала войди в аккаунт фабрики по ссылке выше.');userId=data.session.user.id;
  sb.auth.onAuthStateChange((event,session)=>{if(session?.user?.id!==userId){clear(true);userId='';tasks=[];$('task').replaceChildren();$('title').value='';$('description').value='';status('Аккаунт фабрики изменился. Вернись в фабрику и открой календарь заново.')}});
  const config=await fetch('/api/factory-calendar',{cache:'no-store'}).then(r=>r.json());clientId=config.clientId||'';$('connect').disabled=!clientId;status(clientId?'Нажми «Подключить Google Calendar» и выбери свой Google-аккаунт.':'Связь подготовлена. Ожидается настройка Google OAuth.');
  const owner=userId,requestEpoch=epoch;let factory;try{factory=await factoryPull()}catch(e){factory={tasks:[]};$('result').textContent='Не удалось загрузить задачи. Можно создать свою встречу.'}if(owner!==userId||requestEpoch!==epoch)return;tasks=(factory.tasks||[]).filter(t=>t.status==='open').slice(0,200);for(const task of tasks){const option=document.createElement('option');option.value=task.client_id;option.textContent=task.title;$('task').append(option)}const selected=new URLSearchParams(location.search).get('task');if(tasks.some(t=>t.client_id===selected)){$('task').value=selected;$('task').onchange()}
 }catch(error){status(error.message)}}
 init();
})();
