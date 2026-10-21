(() => {
  'use strict';
  const board = document.querySelector('[data-seller-dashboard]');
  if (!board) return;
  const update = board.querySelector('[data-seller-update]');
  const feedback = board.querySelector('[data-seller-feedback]');
  let busy = false, stopped = false, submitting = false, timer;
  const show = text => { feedback.textContent = text; update.hidden = false; };
  board.querySelectorAll('[data-seller-decision]').forEach(form => {
    const dirty = () => { form.dataset.dirty = 'true'; };
    form.addEventListener('input', dirty);
    form.addEventListener('change', dirty);
    form.addEventListener('submit', event => {
      if (submitting) { event.preventDefault(); return; }
      submitting = true;
      form.querySelector('button[type="submit"]').disabled = true;
    });
  });
  window.addEventListener('pageshow', () => {
    submitting = false;
    stopped = false;
    board.querySelectorAll('[data-seller-decision] button').forEach(button => { button.disabled = false; });
    clearTimeout(timer); timer = setTimeout(check, 1000);
  });
  async function check() {
    if (busy || stopped || submitting) return;
    clearTimeout(timer); busy = true;
    try {
      const response = await fetch(board.dataset.feedUrl, {credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}});
      if (response.redirected || [401,403,404].includes(response.status)) {
        stopped = true; show('판매자 로그인 상태를 확인한 뒤 새로고침해 주세요.'); return;
      }
      if (!response.ok) throw new Error('orders unavailable');
      const data = await response.json();
      if (data.version !== board.dataset.version) {
        const pending = new Set(data.pending.map(String));
        board.querySelectorAll('[data-pending-order]').forEach(card => {
          if (!pending.has(card.dataset.pendingOrder)) card.remove();
        });
        const count = board.querySelector('[data-pending-count]');
        count.textContent = String(data.pending.length);
        const editing = board.querySelector('[data-seller-decision][data-dirty="true"]') || document.activeElement?.closest('[data-seller-decision]');
        if (!editing) { window.location.reload(); return; }
        show('새 주문 또는 처리 상태가 바뀌었어요. 입력을 마친 뒤 새로고침해 주세요.');
      } else { update.hidden = true; }
    } catch { show('주문을 다시 확인하고 있어요. 연결이 복구되면 자동으로 갱신돼요.'); }
    finally { busy = false; if (!stopped && !submitting) timer = setTimeout(check, 5000); }
  }
  timer = setTimeout(check, 1000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
})();
