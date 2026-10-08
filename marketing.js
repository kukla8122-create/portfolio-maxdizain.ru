/* МАКСимум мебель: анонимные события интереса к услугам. Телефоны, имена, тексты заявок не передаются. */
(function(){
  'use strict';
  function send(name, data) {
    if (typeof window.gtag !== 'function') return;
    window.gtag('event', name, Object.assign({page_path: location.pathname}, data));
  }
  document.addEventListener('click', function(event) {
    var a = event.target.closest('a[href]');
    if(!a) return;
    var href = a.getAttribute('href') || '';
    var channel = href.indexOf('tel:')===0 ? 'phone'
      : href.indexOf('max.ru/')!==-1 ? 'max'
      : href.indexOf('vk.me/')!==-1 ? 'vk_message'
      : null;
    if (channel) send('contact_intent', {channel:channel});
    if (href==='#contact' || href==='/#contact') send('contact_section_open', {origin:location.pathname});
    if (href.indexOf('/viral-kitchen/')!==-1) send('quiz_link_click', {origin:location.pathname});
    if (href.indexOf('/7-oshibok-pri-zakaze-kuhni/')!==-1) send('checklist_link_click', {origin:location.pathname});
  }, {passive:true});
}());
