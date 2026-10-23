(() => {
  'use strict';
  const panel = document.querySelector('[data-seller-alerts]');
  if (!panel) return;
  const audio = panel.querySelector('[data-order-audio]');
  const status = panel.querySelector('[data-alert-status]');
  const enable = panel.querySelector('[data-alert-enable]');
  const test = panel.querySelector('[data-alert-test]');
  const desktop = panel.querySelector('[data-desktop-enable]');
  const desktopStatus = panel.querySelector('[data-desktop-status]');
  const visual = document.querySelector('[data-queue-alert]');
  const title = document.title;
  let enabled = false, blocked = false, testing = false, playing = false, offline = false;
  let pending = new Set(), testTimer, playPromise, notice;
  try { enabled = sessionStorage.getItem(panel.dataset.storageKey) === 'enabled'; } catch { /* Optional preference. */ }
  const remember = () => { try { sessionStorage.setItem(panel.dataset.storageKey, 'enabled'); } catch { /* No persistence available. */ } };
  function paint() {
    const count = pending.size;
    visual.hidden = count === 0;
    visual.textContent = count ? `수락 대기 주문 ${count}건 · 5분 안에 접수하거나 거절해 주세요.` : '';
    document.title = count ? `[${count}건] 새 주문 · 영차` : title;
    enable.hidden = enabled && !blocked;
    enable.textContent = blocked ? '주문 알림 다시 켜기' : '주문 알림 켜기';
    if (blocked) status.textContent = '소리를 재생하지 못했어요. 주문 알림 다시 켜기를 누르고 PC 음량도 확인해 주세요.';
    else if (!enabled) status.textContent = '주문 알림을 켜 주세요. 소리가 들리는지 먼저 확인해 주세요.';
    else if (testing && playing) status.textContent = '테스트 소리 재생 중이에요. 실제로 들리는지 확인해 주세요.';
    else if (count && playing) status.textContent = `수락 대기 ${count}건 · 주문 알림 소리 재생 중`;
    else if (count) status.textContent = `수락 대기 ${count}건 · 소리 재생을 준비하고 있어요.`;
    else status.textContent = '주문 알림 사용 중 · 수락 대기 주문이 없어요.';
    if (offline) status.textContent += ' 주문 연결을 다시 확인하고 있어요.';
  }
  function syncSound() {
    const wanted = enabled && !blocked && (testing || pending.size > 0);
    audio.loop = pending.size > 0;
    if (!wanted) {
      audio.pause(); audio.currentTime = 0; playing = false; paint(); return;
    }
    if (!audio.paused) { playing = true; paint(); return; }
    if (playPromise) return;
    playPromise = audio.play();
    Promise.resolve(playPromise).then(() => {
      playing = true; remember();
      if (!enabled || blocked || !testing && pending.size === 0) { audio.pause(); audio.currentTime = 0; playing = false; }
    }).catch(() => {
      blocked = true; playing = false; testing = false; clearTimeout(testTimer);
    }).finally(() => { playPromise = null; paint(); });
  }
  function desktopState() {
    if (!('Notification' in window) || !window.isSecureContext) {
      desktop.hidden = true; desktopStatus.textContent = '이 브라우저는 PC 화면 알림을 지원하지 않아요. 사이트 화면과 소리로 알려드려요.'; return;
    }
    desktop.hidden = Notification.permission === 'granted';
    desktop.disabled = Notification.permission === 'denied';
    desktopStatus.textContent = Notification.permission === 'granted' ? 'PC 화면 알림이 허용됐어요.' : Notification.permission === 'denied' ? 'PC 화면 알림이 차단돼 있어요. 브라우저 사이트 설정에서 허용할 수 있어요.' : 'PC 화면 알림은 허용한 경우에만 표시돼요.';
  }
  function notifyDesktop(renotify) {
    if (!pending.size) { notice?.close(); notice = null; return; }
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      notice?.close();
      notice = new Notification('영차 · 새 주문', {
        body:`수락 대기 주문 ${pending.size}건이 있어요. 판매자 페이지에서 확인해 주세요.`,
        tag:panel.dataset.storageKey, renotify, requireInteraction:true
      });
      notice.onclick = () => { window.focus(); document.getElementById('pending-title')?.scrollIntoView({block:'start'}); notice?.close(); };
    } catch { desktopStatus.textContent = 'PC 화면 알림을 표시하지 못했어요. 사이트 화면과 소리를 확인해 주세요.'; }
  }
  function activate() {
    enabled = true; blocked = false; testing = true; clearTimeout(testTimer);
    syncSound();
    testTimer = setTimeout(() => { testing = false; syncSound(); }, 1400);
  }
  enable.addEventListener('click', activate);
  test.addEventListener('click', activate);
  desktop.addEventListener('click', async () => {
    try { await Notification.requestPermission(); desktopState(); notifyDesktop(true); }
    catch { desktopStatus.textContent = '브라우저에서 PC 알림 설정을 확인해 주세요.'; }
  });
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
      enabled = false; try { sessionStorage.removeItem(panel.dataset.storageKey); } catch { /* Optional preference. */ }
      pending.clear(); testing = false; syncSound();
    }
  });
  audio.addEventListener('error', () => { blocked = true; playing = false; paint(); });
  desktopState(); paint();
})();
