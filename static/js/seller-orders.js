(() => {
  'use strict';
  const board = document.querySelector('[data-seller-dashboard]');
  if (!board) return;
  const actionFeedback = document.querySelector('[data-seller-action-feedback]');
  let busy = false, stopped = false, submitting = false, timer, generation = 0;
  const show = text => {
    const box = board.querySelector('[data-seller-update]');
    box.querySelector('[data-seller-feedback]').textContent = text; box.hidden = false;
  };
  const queue = data => document.dispatchEvent(new CustomEvent('seller:queue', {detail:data}));
  const stop = () => {
    stopped = true; generation++; clearTimeout(timer);
    document.dispatchEvent(new Event('seller:stop'));
  };
  const feedback = text => { actionFeedback.textContent = text; actionFeedback.hidden = !text; };
  function merge(next, submittedCard = null) {
    const focus = document.activeElement;
    const preserved = [];
    board.querySelectorAll('[data-pending-order], [data-accepted-order]').forEach(card => {
      if (card === submittedCard || !card.querySelector('[data-dirty="true"]') && !card.contains(focus)) return;
      const attr = card.hasAttribute('data-pending-order') ? 'data-pending-order' : 'data-accepted-order';
      const fresh = next.querySelector(`[${attr}="${card.getAttribute(attr)}"]`);
      if (fresh) { fresh.replaceWith(card); preserved.push(card); }
    });
    board.replaceChildren(...next.childNodes);
    board.dataset.version = next.dataset.version;
    board.dataset.open = next.dataset.open;
    if (focus && preserved.some(card => card.contains(focus))) focus.focus({preventScroll:true});
    queue({pending:[...board.querySelectorAll('[data-pending-order]')].map(card => Number(card.dataset.pendingOrder)), is_open:board.dataset.open === 'true'});
  }
  async function refresh(submittedCard = null, response = null, expected = generation) {
    response ||= await fetch(board.dataset.dashboardUrl + window.location.search, {credentials:'same-origin', cache:'no-store', headers:{Accept:'text/html'}});
    const html = new DOMParser().parseFromString(await response.text(), 'text/html');
    if (stopped || expected !== generation) return;
    const next = html.querySelector('[data-seller-dashboard]');
    if (!next) {
      stop(); show('판매자 로그인 상태를 확인한 뒤 새로고침해 주세요.'); return;
    }
    if (!response.ok && response.status !== 400) throw new Error('dashboard unavailable');
    const business = html.querySelector('[data-seller-business]');
    if (business) document.querySelector('[data-seller-business]').replaceWith(business);
    merge(next, submittedCard);
    const messages = [...html.querySelectorAll('.flash-messages p')].map(node => node.textContent);
    if (messages.length) feedback(messages.join(' '));
  }
  board.addEventListener('input', event => {
    const form = event.target.closest('[data-seller-decision]'); if (form) form.dataset.dirty = 'true';
  });
  board.addEventListener('change', event => {
    const form = event.target.closest('[data-seller-decision]'); if (form) form.dataset.dirty = 'true';
  });
  board.addEventListener('submit', async event => {
    const form = event.target.closest('[data-seller-decision]');
    if (!form) return;
    event.preventDefault();
    if (submitting || stopped) return;
    submitting = true; generation++;
    const button = form.querySelector('button[type="submit"]'); button.disabled = true;
    const card = form.closest('[data-pending-order], [data-accepted-order]');
    try {
      const response = await fetch(form.action, {method:'POST', body:new FormData(form), credentials:'same-origin', cache:'no-store', headers:{Accept:'text/html'}});
      await refresh(card, response);
    } catch { feedback('처리 결과를 확인하지 못했어요. 자동으로 다시 확인할게요.'); }
    finally { submitting = false; button.disabled = false; check(); }
  });
  async function check() {
    if (busy || stopped || submitting) return;
    clearTimeout(timer); busy = true;
    const expected = generation;
    try {
      const response = await fetch(board.dataset.feedUrl, {credentials:'same-origin', cache:'no-store', headers:{Accept:'application/json'}});
      if (response.redirected || [401,403,404].includes(response.status)) {
        stop(); show('판매자 로그인 상태를 확인한 뒤 새로고침해 주세요.'); return;
      }
      if (!response.ok) throw new Error('orders unavailable');
      const data = await response.json();
      // A POST may have started while this feed was in flight; keep its form intact.
      if (submitting || stopped || expected !== generation) return;
      if (!Array.isArray(data.pending) || typeof data.is_open !== 'boolean') throw new Error('invalid feed');
      queue(data);
      const pending = new Set(data.pending.map(String));
      board.querySelectorAll('[data-pending-order]').forEach(card => {
        if (!pending.has(card.dataset.pendingOrder)) card.remove();
      });
      board.querySelector('[data-pending-count]').textContent = String(pending.size);
      if (data.version !== board.dataset.version) await refresh();
      else board.querySelector('[data-seller-update]').hidden = true;
    } catch {
      show('주문을 다시 확인하고 있어요. 연결이 복구되면 자동으로 갱신돼요.');
      document.dispatchEvent(new Event('seller:offline'));
    } finally { busy = false; if (!stopped) timer = setTimeout(check, 5000); }
  }
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', () => { stopped = false; check(); });
  window.addEventListener('online', check);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
  check();
})();
