(() => {
  'use strict';
  const dialog = document.getElementById('imageViewer');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  let previous;
  document.querySelectorAll('a[data-gallery]').forEach(link => {
    link.addEventListener('click', event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      previous = link;
      const img = dialog.querySelector('img');
      img.src = link.href;
      img.alt = link.querySelector('img').alt;
      dialog.querySelector('p').textContent = img.alt;
      dialog.querySelector('a').href = link.href;
      dialog.showModal();
      document.documentElement.classList.add('no-scroll');
    });
  });
  dialog.querySelector('button').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => {
    document.documentElement.classList.remove('no-scroll');
    dialog.querySelector('img').removeAttribute('src');
    if (previous) previous.focus();
  });
})();
