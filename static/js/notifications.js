(() => {
  'use strict';
  const panel = document.querySelector('[data-order-notifications]');
  if (!panel) return;
  const list = panel.querySelector('[data-notification-list]');
  const feedback = panel.querySelector('[data-notification-feedback]');
  let timer, busy = false, stopped = false;
  const cards = new Map();
  async function dismiss(notice, button) {
    button.disabled = true;
    try {
      const response = await fetch(notice.read_url, {
        method:'POST', credentials:'same-origin', cache:'no-store',
        headers:{'X-CSRFToken':document.querySelector('[name=csrfmiddlewaretoken]').value, Accept:'application/json'}
      });
      if (!response.ok || response.redirected) throw new Error('read failed');
      cards.get(notice.id)?.remove(); cards.delete(notice.id);
      panel.hidden = cards.size === 0; feedback.textContent = '';
      check();
    } catch { feedback.textContent = '알림을 확인 처리하지 못했어요. 다시 눌러 주세요.'; }
    finally { button.disabled = false; }
  }
  function render(notices) {
    const ids = new Set(notices.map(notice => notice.id));
    for (const [id, card] of cards) {
      if (!ids.has(id)) { card.remove(); cards.delete(id); }
    }
    for (const notice of notices) {
      if (cards.has(notice.id)) continue;
      const card = document.createElement('div'); card.className = 'order-notification';
      const text = document.createElement('p'); text.textContent = notice.message;
      const link = document.createElement('a'); link.href = notice.order_url; link.textContent = '주문서 보기';
      const button = document.createElement('button'); button.type = 'button'; button.textContent = '확인';
      button.addEventListener('click', () => dismiss(notice, button));
      card.append(text, link, button); list.append(card); cards.set(notice.id, card);
    }
    panel.hidden = cards.size === 0;
  }
  async function check() {
    if (busy || stopped) return;
    clearTimeout(timer); busy = true;
    try {
      const response = await fetch(panel.dataset.url, {credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}});
      if (response.redirected || [401,403].includes(response.status)) { stopped = true; return; }
      if (!response.ok) throw new Error('notifications unavailable');
      const data = await response.json(); render(data.notifications); feedback.textContent = '';
    } catch { /* Keep unread notices visible and retry after a network interruption. */ }
    finally { busy = false; if (!stopped) timer = setTimeout(check, 5000); }
  }
  check();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  window.addEventListener('pageshow', () => { stopped = false; check(); });
})();
