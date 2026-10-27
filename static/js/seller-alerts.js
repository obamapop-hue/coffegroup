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
  const progress = panel.querySelector('[data-alert-progress]');
  const soundState = panel.querySelector('[data-sound-state]');
  const desktopState = panel.querySelector('[data-desktop-state]');
  const setup = panel.querySelector('[data-alert-setup]');
  const instructions = panel.querySelector('[data-alert-instructions]');
  const desktopStatus = panel.querySelector('[data-desktop-status]');
  const visual = document.querySelector('[data-queue-alert]');
  const title = document.title;
  let enabled = false, blocked = false, testing = false, playing = false, offline = false;
  // A saved preference is not proof that this document can play sound.
  let soundReady = false, soundError = '', desktopError = '', requestingPermission = false, attempted = false;
  let pending = new Set(), testTimer, playPromise, notice;
  try { enabled = sessionStorage.getItem(panel.dataset.storageKey) === 'enabled'; } catch { /* Optional preference. */ }
  const remember = () => { try { sessionStorage.setItem(panel.dataset.storageKey, 'enabled'); } catch { /* No persistence available. */ } };
  const permission = () => typeof window.Notification === 'function' && window.isSecureContext ? Notification.permission : 'unsupported';
  let lastPermission = permission();
  function browserSteps(setting) {
    const ua = navigator.userAgent;
    if (/Firefox\//.test(ua)) return [
      '주소창 왼쪽의 사이트 권한 아이콘을 눌러 알림 차단을 해제해 주세요.',
      '알림 활성화 버튼을 다시 누르고 브라우저의 알림 요청을 허용해 주세요.'
    ];
    if (/Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\//.test(ua)) return [
      'Safari의 설정에서 웹사이트 → 알림을 열어 영차 사이트를 허용해 주세요.',
      '소리도 재생되지 않으면 웹사이트 → 자동 재생에서 이 사이트의 자동 재생을 허용해 주세요.'
    ];
    if (/Chrome\/|Chromium\/|Edg\//.test(ua) && !/Android|iPhone|iPad/.test(ua)) return [
      '주소창 왼쪽의 사이트 정보 아이콘(조절 모양 또는 자물쇠)을 눌러주세요.',
      `‘${setting}’을 ‘허용’으로 바꿔주세요. 항목이 안 보이면 ‘사이트 설정’을 열어 ‘${setting}’을 ‘허용’으로 바꿔주세요.`
    ];
    return [
      `브라우저의 사이트 설정에서 현재 영차 사이트의 ‘${setting}’을 허용해 주세요.`,
      '휴대전화의 앱 안에서 연 화면은 PC 알림을 지원하지 않을 수 있어요. 판매자 PC에서 사이트를 열어주세요.'
    ];
  }
  function paint() {
    const count = pending.size;
    const state = permission();
    const soundOK = enabled && soundReady && !blocked;
    const desktopOK = state === 'granted' && !desktopError;
    const ready = soundOK && desktopOK;
    visual.hidden = count === 0;
    visual.textContent = count ? `수락 대기 주문 ${count}건 · 5분 안에 접수하거나 거절해 주세요.` : '';
    document.title = count ? `[${count}건] 새 주문 · 영차` : title;
    check.hidden = !ready;
    message.textContent = ready ? '알림 기능이 정상적으로 활성화 되었습니다.' : '알림 기능이 활성화되지 않았습니다. 알림 활성화 버튼을 눌러주세요.';
    progress.hidden = !attempted && !blocked;
    soundState.textContent = soundOK ? '재생 확인됨' : blocked ? '재생 실패' : enabled && testing ? '재생 확인 중' : '확인 필요';
    desktopState.textContent = desktopOK ? '허용됨' : state === 'denied' ? '차단됨' : state === 'unsupported' ? '지원되지 않음' : desktopError ? '설정 확인 필요' : requestingPermission ? '허용 대기 중' : '허용 필요';
    soundState.classList.toggle('is-ready', soundOK);
    desktopState.classList.toggle('is-ready', desktopOK);
    setup.hidden = ready || !attempted && !blocked;
    let steps = [];
    if (state === 'unsupported') {
      desktopStatus.textContent = '이 환경에서는 PC 화면 알림을 사용할 수 없어요. 판매자 PC의 최신 크롬 또는 엣지에서 HTTPS 주소로 열어주세요.';
    } else if (!desktopOK) {
      desktopStatus.textContent = state === 'denied' ? 'PC 알림이 차단되어 있어 허용 요청 창을 다시 띄울 수 없어요. 아래 순서로 허용해 주세요.'
        : desktopError || '브라우저가 표시한 알림 요청에서 ‘허용’을 눌러주세요. 요청이 보이지 않거나 닫았다면 아래 설정을 확인해 주세요.';
      steps = browserSteps('알림');
    } else if (blocked) {
      desktopStatus.textContent = soundError === 'media' ? '알림 소리 파일을 불러오지 못했어요. 인터넷 연결을 확인하고 알림 활성화를 눌러주세요. 소리 파일도 다시 불러와요.'
        : 'PC 화면 알림은 허용됐지만 소리를 재생하지 못했어요. 이 사이트의 소리 설정을 확인해 주세요.';
      steps = browserSteps('소리');
    } else {
      desktopStatus.textContent = 'PC 화면 알림은 허용됐어요. 알림 소리 재생을 확인하고 있어요.';
    }
    // Avoid rebuilding the guidance on every poll while someone is reading it.
    const text = steps.join('\n');
    if (instructions.dataset.steps !== text) {
      instructions.dataset.steps = text;
      instructions.replaceChildren(...steps.map(step => { const li = document.createElement('li'); li.textContent = step; return li; }));
    }
    instructions.hidden = !steps.length;
    if (blocked) detail.textContent = '소리를 재생하지 못했어요. 알림 활성화 버튼을 다시 누르고 PC 음량도 확인해 주세요.';
    else if (testing && playing) detail.textContent = '테스트 소리 재생 중이에요. 실제로 들리는지 확인해 주세요.';
    else if (count && playing) detail.textContent = `수락 대기 ${count}건 · 주문 알림 소리 재생 중`;
    else if (enabled && (testing || count)) detail.textContent = '소리 재생을 준비하고 있어요.';
    else detail.textContent = '';
    if (offline) detail.textContent += ' 주문 연결을 다시 확인하고 있어요.';
  }
  function endTestLater() {
    if (!testing) return;
    clearTimeout(testTimer);
    // Start the test duration after playback starts, not while the file is loading.
    testTimer = setTimeout(() => { testing = false; syncSound(); }, 1400);
  }
  function syncSound() {
    const wanted = enabled && !blocked && (testing || pending.size > 0);
    audio.loop = pending.size > 0;
    if (!wanted) {
      audio.pause(); audio.currentTime = 0; playing = false; paint(); return;
    }
    if (playPromise) { paint(); return; }
    if (!audio.paused) { playing = true; endTestLater(); paint(); return; }
    try { playPromise = Promise.resolve(audio.play()); }
    catch (error) { playPromise = Promise.reject(error); }
    playPromise.then(() => {
      playing = true;
      if (enabled && !blocked) { soundReady = true; remember(); }
      if (!enabled || blocked || !testing && pending.size === 0) { audio.pause(); audio.currentTime = 0; playing = false; }
      else endTestLater();
    }).catch(error => {
      blocked = true; soundReady = false; soundError = error.name === 'NotAllowedError' ? 'permission' : 'media';
      playing = false; testing = false; clearTimeout(testTimer);
    }).finally(() => { playPromise = null; paint(); });
  }
  function notifyDesktop(renotify) {
    if (!pending.size) { notice?.close(); notice = null; return; }
    if (permission() !== 'granted') return;
    try {
      notice?.close();
      notice = new Notification('영차 · 새 주문', {
        body:`수락 대기 주문 ${pending.size}건이 있어요. 판매자 페이지에서 확인해 주세요.`,
        tag:panel.dataset.storageKey, renotify, requireInteraction:true
      });
      notice.onclick = () => { window.focus(); document.getElementById('pending-title')?.scrollIntoView({block:'start'}); notice?.close(); };
    } catch { desktopError = 'PC 화면 알림을 표시하지 못했어요. 브라우저의 사이트 알림 설정을 확인해 주세요.'; }
  }
  function activateSound() {
    attempted = true; enabled = true; blocked = false; testing = true; clearTimeout(testTimer);
    if (audio.error || soundError === 'media') audio.load();
    soundError = '';
    syncSound();
  }
  enable.addEventListener('click', async () => {
    desktopError = '';
    activateSound();
    // Both calls start within the click. Retrying a pending request does not open another prompt.
    if (permission() === 'default' && !requestingPermission) {
      requestingPermission = true; paint();
      try { await Notification.requestPermission(); }
      catch { desktopError = 'PC 알림 허용 요청을 열지 못했어요. 브라우저의 사이트 알림 설정에서 허용해 주세요.'; }
      finally { requestingPermission = false; }
    }
    lastPermission = permission();
    notifyDesktop(true); paint();
  });
  test.addEventListener('click', activateSound);
  function recheck() {
    const next = permission();
    // Settings may have changed outside this tab; no new permission prompt is opened here.
    if (attempted && !requestingPermission && next === 'granted' && lastPermission !== 'granted') {
      desktopError = '';
      if (!soundReady || blocked) activateSound();
      notifyDesktop(true);
    }
    lastPermission = next;
    paint();
  }
  window.addEventListener('focus', recheck);
  window.addEventListener('pageshow', recheck);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) recheck(); });
  // Chrome reports permission changes even while the page stays open; other browsers use focus/poll fallback.
  try { navigator.permissions?.query({name:'notifications'}).then(result => result.addEventListener('change', recheck)).catch(() => {}); } catch { /* Optional browser API. */ }
  document.addEventListener('seller:queue', event => {
    const data = event.detail;
    const next = new Set(data.is_open ? data.pending.map(String) : []);
    const added = [...next].some(id => !pending.has(id));
    const changed = next.size !== pending.size || added;
    if (pending.size && !next.size) { testing = false; clearTimeout(testTimer); }
    pending = next; offline = false;
    if (changed) notifyDesktop(added);
    recheck(); syncSound();
  });
  document.addEventListener('seller:offline', () => { offline = true; paint(); });
  document.addEventListener('seller:stop', () => {
    pending.clear(); testing = false; clearTimeout(testTimer); notice?.close(); notice = null;
    syncSound();
  });
  document.addEventListener('submit', event => {
    if (new URL(event.target.action, location.href).pathname === '/accounts/logout/') {
      enabled = false; soundReady = false; attempted = false;
      try { sessionStorage.removeItem(panel.dataset.storageKey); } catch { /* Optional preference. */ }
      pending.clear(); testing = false; syncSound();
    }
  });
  audio.addEventListener('error', () => {
    blocked = true; soundReady = false; soundError = 'media'; testing = false; clearTimeout(testTimer); syncSound();
  });
  paint();
})();
