(() => {
  const choices = [
    {id:'wood',title:'Тёплое дерево',src:'images/added/previews/photo-12.jpg',full:'images/added/photo-12.jpg'},
    {id:'light',title:'Светлая и лаконичная',src:'images/added/previews/photo-17.jpg',full:'images/added/photo-17.jpg'},
    {id:'olive',title:'Оливковая классика',src:'images/added/previews/photo-34.jpg',full:'images/added/photo-34.jpg'}
  ];
  const selected = new Set();
  const host = document.getElementById('kitchenChoices');
  const result = document.getElementById('selectionResult');
  const status = document.getElementById('selectionStatus');
  const track = (name) => { if(typeof gtag==='function') gtag('event',name); };
  const params = new URLSearchParams(location.search);
  (params.get('pick') || '').split(',').forEach(id => {if(choices.some(c=>c.id===id)) selected.add(id);});
  function link() {
    const u=new URL(location.href); u.search=''; u.searchParams.set('pick',[...selected].join(','));u.hash='choose';return u.href;
  }
  function update() {
    host.querySelectorAll('button').forEach(b=>{b.setAttribute('aria-pressed',String(selected.has(b.dataset.id)));b.querySelector('small').textContent=selected.has(b.dataset.id)?'Выбрано ✓':'Выбрать ♡';});
    result.hidden=!selected.size;
    document.getElementById('selectionCount').textContent=selected.size ? `Вы выбрали: ${[...selected].map(id=>choices.find(c=>c.id===id).title).join(' · ')}` : 'Выберите один или несколько вариантов';
    document.getElementById('selectionLink').value=selected.size?link():'';
    status.textContent='';
    try { localStorage.setItem('maximum-kitchens',JSON.stringify([...selected])); } catch {}
  }
  choices.forEach(c=>{
    const b=document.createElement('button');b.type='button';b.dataset.id=c.id;b.className='choice-card';
    b.innerHTML=`<img src="${c.src}" alt="${c.title}" loading="lazy"><span>${c.title}</span><small>Выбрать <span aria-hidden="true">♡</span></small>`;
    b.onclick=()=>{selected.has(c.id)?selected.delete(c.id):selected.add(c.id);update();track('kitchen_choice');};host.append(b);
  });
  if(!params.has('pick')) {try{JSON.parse(localStorage.getItem('maximum-kitchens')||'[]').forEach(id=>{if(choices.some(c=>c.id===id))selected.add(id);});}catch{}}
  update();
  document.getElementById('copySelection').onclick=async()=>{
    try{await navigator.clipboard.writeText(link());status.textContent='Ссылка скопирована. Отправьте её близкому в MAX или ВК.';track('selection_copy');}
    catch{document.getElementById('selectionLink').select();status.textContent='Скопируйте выделенную ссылку.';}
  };
  document.getElementById('shareSelection').onclick=async()=>{
    if(navigator.share){try{await navigator.share({title:'Какую кухню выберем?',text:'Вот мои любимые варианты. Какой выберешь ты?',url:link()});track('selection_share');}catch(e){if(e.name!=='AbortError')status.textContent='Поделиться не удалось. Используйте кнопку копирования ссылки.';}}
    else document.getElementById('copySelection').click();
  };
  document.getElementById('downloadSelection').onclick=async()=>{
    const button=document.getElementById('downloadSelection');button.disabled=true;
    try{
      const items=choices.filter(c=>selected.has(c.id)); const canvas=document.createElement('canvas');canvas.width=1200;canvas.height=320+items.length*520;
      const x=canvas.getContext('2d');x.fillStyle='#0d2020';x.fillRect(0,0,canvas.width,canvas.height);x.fillStyle='#e6cf9e';x.font='bold 52px Georgia';x.fillText('Какую кухню выберем?',60,90);x.font='28px Arial';x.fillText('Моя подборка • МАКСимум мебель',60,145);
      for(let i=0;i<items.length;i++){
        const img=new Image();img.src=items[i].full || items[i].src;await img.decode();const top=190+i*520;
        const scale=Math.min(1080/img.width,420/img.height),w=img.width*scale,h=img.height*scale;
        x.drawImage(img,60+(1080-w)/2,top+(420-h)/2,w,h);
        x.font='bold 30px Arial';x.fillText(items[i].title,60,top+464);
      }
      x.font='26px Arial';x.fillText('Выбери свой вариант: portfolio-maxdizain.ru',60,canvas.height-55);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw Error();
      const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='моя-подборка-кухонь.png';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
      status.textContent='Карточка сохранена. Отправьте её вместе со ссылкой на подборку.';track('selection_card_download');
    }catch{status.textContent='Не удалось сохранить карточку. Поделитесь ссылкой — она сохраняет ваш выбор.';}finally{button.disabled=false;}
  };
  // Public Supabase anon JWT is safe to publish: the website_leads table has RLS and no anon grants.
  const leadsEndpoint='https://uhyaigqizvwtsbtmvkdr.supabase.co/functions/v1/website-leads';
  const leadsPublicKey="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVoeWFpZ3FpenZ3dHNidG12a2RyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODg0MjY5NDgsImV4cCI6MjEwNDAwMjk0OH0.Ctnvw9s9Azz8jWh4JPC7ry9HkrqEHBv8VJLi0fxMW20";
  const leadsForm=document.getElementById('contactForm');
  const leadsStartedAt=Date.now();
  leadsForm.addEventListener('submit',async e=>{
    e.preventDefault();
    const name=document.getElementById('nameInput').value.trim();
    const phone=document.getElementById('phoneInput').value.trim();
    const service=document.getElementById('serviceInput').value;
    const status=document.getElementById('contactStatus');
    const fallback=document.getElementById('formSuccess');
    fallback.classList.add('hidden');
    if(phone.replace(/\D/g,'').length < 10 || phone.replace(/\D/g,'').length > 15){
      status.textContent='Проверьте номер телефона с кодом страны.';return;
    }
    if(!document.getElementById('privacyConsent').checked){
      status.textContent='Для отправки необходимо согласие на обработку данных.';return;
    }
    const message=`Здравствуйте, Катерина! Меня зовут ${name}. Интересует: ${service}. Телефон: ${phone}.`+
      (selected.size?`\nМоя подборка: ${link()}`:'');
    const button=leadsForm.querySelector('button[type=submit]');
    const label=button.querySelector('span');
    button.disabled=true;label.textContent='Отправляем заявку…';status.textContent='';
    try{
      const qs=new URLSearchParams(location.search);
      const payload={
        name,phone,service,consent:true,
        honeypot:document.getElementById('websiteHoneypot').value,
        started_at:leadsStartedAt,source_page:location.pathname,
        utm_source:qs.get('utm_source')||'',utm_campaign:qs.get('utm_campaign')||''
      };
      const timer=new AbortController();
      const limit=setTimeout(()=>timer.abort(),10000);
      let response;
      try{
        response=await fetch(leadsEndpoint,{
          method:'POST',mode:'cors',signal:timer.signal,
          headers:{'Content-Type':'application/json','apikey':leadsPublicKey,'Authorization':'Bearer '+leadsPublicKey},
          body:JSON.stringify(payload)
        });
      }finally{clearTimeout(limit);}
      const data=await response.json().catch(()=>({}));
      if(!response.ok||!data.ok)throw Error(data.error||'server');
      status.textContent='✓ Заявка отправлена! Мы получили ваши контакты и свяжемся с вами.';
      leadsForm.reset();track('lead_submitted');
    }catch(err){
      document.getElementById('requestText').value=message;
      fallback.classList.remove('hidden');
      status.textContent='Не удалось подтвердить отправку. Чтобы не потерять обращение, скопируйте сообщение и отправьте в MAX или ВК.';
      track('lead_submit_failed');
    }finally{button.disabled=false;label.textContent='Отправить заявку';}
  });
  document.getElementById('copyRequest').onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('requestText').value);document.getElementById('contactStatus').textContent='Скопировано. Откройте MAX или ВК и отправьте сообщение.';}catch{document.getElementById('requestText').select();document.getElementById('contactStatus').textContent='Скопируйте выделенный текст.';}};
  document.querySelectorAll('[data-service]').forEach(a=>a.addEventListener('click',()=>{document.getElementById('serviceInput').value=a.dataset.service;track('service_interest');}));
})();
