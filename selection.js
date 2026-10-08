(() => {
  const choices = [
    {id:'wood',title:'Тёплое дерево',src:'images/added/photo-12.jpg'},
    {id:'light',title:'Светлая и лаконичная',src:'images/added/photo-17.jpg'},
    {id:'olive',title:'Оливковая классика',src:'images/added/photo-34.jpg'}
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
        const img=new Image();img.src=items[i].src;await img.decode();const top=190+i*520;
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
  document.getElementById('contactForm').addEventListener('submit',e=>{
    e.preventDefault();const name=document.getElementById('nameInput').value.trim(),phone=document.getElementById('phoneInput').value.trim();
    if(phone.replace(/\D/g,'').length!==11){document.getElementById('contactStatus').textContent='Укажите телефон полностью: 11 цифр с кодом страны.';return;}
    const service=document.getElementById('serviceInput').value;
    const text=`Здравствуйте, Катерина! Меня зовут ${name}. Интересует: ${service}. Телефон: ${phone}.`+(selected.size?`\nМоя подборка: ${link()}`:'');
    document.getElementById('requestText').value=text;document.getElementById('formSuccess').classList.remove('hidden');document.getElementById('contactStatus').textContent='Сообщение готово. Скопируйте его и отправьте в выбранный чат.';track('request_prepared');
  });
  document.getElementById('copyRequest').onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('requestText').value);document.getElementById('contactStatus').textContent='Скопировано. Откройте MAX или ВК и отправьте сообщение.';}catch{document.getElementById('requestText').select();document.getElementById('contactStatus').textContent='Скопируйте выделенный текст.';}};
  document.querySelectorAll('[data-service]').forEach(a=>a.addEventListener('click',()=>{document.getElementById('serviceInput').value=a.dataset.service;track('service_interest');}));
})();
