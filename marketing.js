/* Anonymous interaction events. Contact values and message text are never sent. */
(function(){
  'use strict';
  function send(name, data) {
    // Only explicitly supplied, non-personal event fields belong here.
    var safe = {page_path: location.pathname};
    ['contact_channel', 'channel', 'origin', 'document_path', 'section'].forEach(function (key) {
      if (data && typeof data[key] === 'string') safe[key] = data[key];
    });
    // Analytics must never change the outcome of a form submission.
    try { if (typeof window.gtag === 'function') window.gtag('event', name, safe); } catch (_) {}
    try { if (typeof window.ym === 'function') window.ym(108660885, 'reachGoal', name, safe); } catch (_) {}
  }
  window.maximumTrack = send;
  document.addEventListener('click', function(event) {
    var a = event.target.closest && event.target.closest('a[href]');
    if(!a) return;
    var url;
    try { url = new URL(a.getAttribute('href'), location.href); } catch (_) { return; }
    var host = url.hostname.replace(/^www\./, '');
    if (url.protocol === 'tel:') send('contact_click', {contact_channel: 'phone'});
    else if (host === 'vk.me') send('contact_click', {contact_channel: 'vk_chat'});
    else if (host === 'max.ru' && url.pathname.indexOf('/u/') === 0) send('contact_click', {contact_channel: 'max_chat'});
    else if (host === 'max.ru') send('channel_open', {channel: 'max'});
    else if (host === 'vk.ru' || host === 'vk.com') send('channel_open', {channel: 'vk'});
    else if (host === 't.me' && /^\/proektkuhni\/?$/i.test(url.pathname)) send('channel_open', {channel: 'telegram'});
    else if (host === 't.me') send('telegram_link_open', {});
    if (url.origin !== location.origin) return;
    if (url.hash === '#contact') send('contact_section_open', {origin: location.pathname});
    if (url.pathname === '/viral-kitchen/') send('quiz_link_click', {origin: location.pathname});
    if (url.pathname === '/7-oshibok-pri-zakaze-kuhni/') send('checklist_link_click', {origin: location.pathname});
    if (/\.pdf$/i.test(url.pathname)) send('project_pdf_open', {document_path: url.pathname});
  }, {passive:true});
  if (/^\/projects\/[^/]+\/$/.test(location.pathname)) send('portfolio_open', {});
  if ('IntersectionObserver' in window) {
    var observer = new window.IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        send('technical_section_view', {section: entry.target.closest('section').id});
        observer.unobserve(entry.target);
      });
    }, {threshold: 0.5});
    document.querySelectorAll('#plans h2, #kitchen h2, #electrical h2').forEach(function (heading) {
      observer.observe(heading);
    });
  }
}());

