(() => {
  'use strict';
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });
})();
(() => {
  'use strict';
  const button = document.querySelector('[data-copy-share-link]');
  const link = document.getElementById('share-link');
  const status = document.querySelector('[data-share-copy-status]');
  if (!button || !link || !status) return;
  button.hidden = false;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      try {
        if (!navigator.clipboard || !window.isSecureContext) throw new Error('clipboard unavailable');
        await navigator.clipboard.writeText(link.value);
      } catch {
        const buffer = document.createElement('textarea');
        buffer.value = link.value;
        buffer.className = 'clipboard-buffer';
        buffer.setAttribute('aria-hidden', 'true');
        buffer.tabIndex = -1;
        document.body.appendChild(buffer);
        try {
          buffer.focus({preventScroll:true}); buffer.select(); buffer.setSelectionRange(0, buffer.value.length);
          if (!document.execCommand('copy')) throw new Error('copy unavailable');
        } finally { buffer.remove(); }
      }
      status.textContent = '참여 링크를 복사했어요.';
    } catch {
      status.textContent = '복사하지 못했어요. 다시 눌러 주세요.';
    } finally { button.disabled = false; button.focus({preventScroll:true}); }
  });
})();
(() => {
  'use strict';
  const form = document.querySelector('[data-final-confirm]');
  if (form) {
    const button = form.querySelector('button[type="submit"]');
    let sending = false;
    form.addEventListener('submit', event => {
      if (sending) { event.preventDefault(); return; }
      sending = true; button.disabled = true; button.textContent = '전송 중…';
    });
    window.addEventListener('pageshow', () => { sending = false; button.disabled = false; button.textContent = '주문서 전송'; });
  }
  const panel = document.querySelector('[data-order-status]');
  if (!panel) return;
  const feedback = panel.querySelector('[data-status-feedback]');
  let timer, busy = false, stopped = false;
  async function check() {
    if (busy || stopped) return;
    clearTimeout(timer); busy = true;
    try {
      const response = await fetch(panel.dataset.statusUrl, {credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}});
      if (response.redirected || [401,403].includes(response.status)) {
        stopped = true; feedback.textContent = '로그인 상태를 확인한 뒤 새로고침해 주세요.'; return;
      }
      if (!response.ok) throw new Error('status unavailable');
      const data = await response.json();
      if (data.status !== 'pending') { window.location.reload(); return; }
      feedback.textContent = '';
    } catch { feedback.textContent = '상태를 다시 확인하고 있어요. 연결이 복구되면 자동 갱신돼요.'; }
    finally { busy = false; if (!stopped) timer = setTimeout(check, 5000); }
  }
  timer = setTimeout(check, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
})();
