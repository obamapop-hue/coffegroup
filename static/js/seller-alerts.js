(() => {
  'use strict';
  const panel = document.querySelector('[data-seller-alerts]');
  if (!panel) return;
  const audio = panel.querySelector('[data-order-audio]');
  const message = panel.querySelector('[data-alert-message]');
  const check = panel.querySelector('[data-alert-check]');
  const detail = panel.querySelector('[data-alert-detail]');
  const enable = panel.querySelector('[data-alert-enable]');
  const test = panel.querySelector('[data-alert-test]');
  const desktopStatus = panel.querySelector('[data-desktop-status]');
  const visual = document.querySelector('[data-queue-alert]');
  const title = document.title;
  let enabled = false, blocked = false, testing = false, playing = false, offline = false;
  // A saved preference is not proof that this document can play sound.
  let soundReady = false, desktopError = '', requestingPermission = false;
  let pending = new Set(), testTimer, playPromise, notice;
  try { enabled = sessionStorage.getItem(panel.dataset.storageKey) === 'enabled'; } catch { /* Optional preference. */ }
  const remember = () => { try { sessionStorage.setItem(panel.dataset.storageKey, 'enabled'); } catch { /* No persistence available. */ } };
  const desktopSupported = () => 'Notification' in window && window.isSecureContext;
  function paint() {
    const count = pending.size;
    visual.hidden = count === 0;
    visual.textContent = count ? `수락 대기 주문 ${count}건 · 5분 안에 접수하거나 거절해 주세요.` : '';
    document.title = count ? `[${count}건] 새 주문 · 영차` : title;
    const ready = enabled && soundReady && !blocked && desktopSupported() && Notification.permission === 'granted' && !desktopError;
    check.hidden = !ready;
    message.textContent = ready ? '알림 기능이 정상적으로 활성화 되었습니다.' : '알림 기능이 활성화되지 않았습니다. 알림 활성화 버튼을 눌러주세요.';
    enable.disabled = requestingPermission;
    if (blocked) detail.textContent = '소리를 재생하지 못했어요. 알림 활성화 버튼을 다시 누르고 PC 음량도 확인해 주세요.';
    else if (testing && playing) detail.textContent = '테스트 소리 재생 중이에요. 실제로 들리는지 확인해 주세요.';
    else if (count && playing) detail.textContent = `수락 대기 ${count}건 · 주문 알림 소리 재생 중`;
    else if (enabled && (testing || count)) detail.textContent = '소리 재생을 준비하고 있어요.';
    else detail.textContent = '';
    if (offline) detail.textContent += ' 주문 연결을 다시 확인하고 있어요.';
    desktopStatus.textContent = !desktopSupported() ? '이 브라우저는 PC 화면 알림을 지원하지 않아요. 사이트 화면과 소리로 알려드려요.'
      : desktopError || (Notification.permission === 'denied' ? 'PC 화면 알림이 차단돼 있어요. 브라우저 사이트 설정에서 알림을 허용한 뒤 알림 활성화 버튼을 눌러주세요.'
        : Notification.permission === 'granted' ? '' : '알림 활성화 버튼을 누른 뒤 브라우저의 알림 요청을 허용해 주세요.');
  }
  function syncSound() {
    const wanted = enabled && !blocked && (testing || pending.size > 0);
    audio.loop = pending.size > 0;
    if (!wanted) {
      audio.pause(); audio.currentTime = 0; playing = false; paint(); return;
    }
    if (!audio.paused) { playing = true; paint(); return; }
    if (playPromise) return;
    try { playPromise = Promise.resolve(audio.play()); }
    catch (error) { playPromise = Promise.reject(error); }
    playPromise.then(() => {
      playing = true;
      if (!blocked) { soundReady = true; remember(); }
      if (!enabled || blocked || !testing && pending.size === 0) { audio.pause(); audio.currentTime = 0; playing = false; }
    }).catch(() => {
      blocked = true; soundReady = false; playing = false; testing = false; clearTimeout(testTimer);
    }).finally(() => { playPromise = null; paint(); });
  }
  function notifyDesktop(renotify) {
    if (!pending.size) { notice?.close(); notice = null; return; }
    if (!desktopSupported() || Notification.permission !== 'granted') return;
    try {
      notice?.close();
      notice = new Notification('영차 · 새 주문', {
        body:`수락 대기 주문 ${pending.size}건이 있어요. 판매자 페이지에서 확인해 주세요.`,
        tag:panel.dataset.storageKey, renotify, requireInteraction:true
      });
      notice.onclick = () => { window.focus(); document.getElementById('pending-title')?.scrollIntoView({block:'start'}); notice?.close(); };
    } catch { desktopError = 'PC 화면 알림을 표시하지 못했어요. 브라우저의 알림 설정을 확인해 주세요.'; }
  }
  function activateSound() {
    enabled = true; blocked = false; testing = true; clearTimeout(testTimer);
    syncSound();
    testTimer = setTimeout(() => { testing = false; syncSound(); }, 1400);
  }
  enable.addEventListener('click', async () => {
    desktopError = '';
    activateSound();
    // Both calls must start within the click, before awaiting either result.
    if (desktopSupported() && Notification.permission === 'default') {
      requestingPermission = true; paint();
      try { await Notification.requestPermission(); }
      catch { desktopError = '브라우저에서 PC 알림 설정을 확인해 주세요.'; }
      finally { requestingPermission = false; }
    }
    notifyDesktop(true); paint();
  });
  test.addEventListener('click', activateSound);
  window.addEventListener('focus', paint);
  window.addEventListener('pageshow', paint);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) paint(); });
  document.addEventListener('seller:queue', event => {
    const data = event.detail;
    const next = new Set(data.is_open ? data.pending.map(String) : []);
    const added = [...next].some(id => !pending.has(id));
    const changed = next.size !== pending.size || added;
    if (pending.size && !next.size) { testing = false; clearTimeout(testTimer); }
    pending = next; offline = false;
    if (changed) notifyDesktop(added);
    syncSound();
  });
  document.addEventListener('seller:offline', () => { offline = true; paint(); });
  document.addEventListener('seller:stop', () => {
    pending.clear(); testing = false; clearTimeout(testTimer); notice?.close(); notice = null;
    syncSound();
  });
  document.addEventListener('submit', event => {
    if (new URL(event.target.action, location.href).pathname === '/accounts/logout/') {
      enabled = false; soundReady = false;
      try { sessionStorage.removeItem(panel.dataset.storageKey); } catch { /* Optional preference. */ }
      pending.clear(); testing = false; syncSound();
    }
  });
  audio.addEventListener('error', () => {
    blocked = true; soundReady = false; testing = false; clearTimeout(testTimer); syncSound();
  });
  paint();
})();
