// Real seller HTML and Chromium audio; permission/failure branches use a simulated Notification API.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => run(JSON.parse(input)).catch(error => { console.error(error); process.exitCode = 1; }));
async function run(cfg) {
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium', args:['--no-sandbox', '--autoplay-policy=document-user-activation-required']});
  const inactive = '알림 기능이 활성화되지 않았습니다. 알림 활성화 버튼을 눌러주세요.';
  const active = '알림 기능이 정상적으로 활성화 되었습니다.';
  const errors = [];
  async function open(options = {}) {
    const ctx = await browser.newContext({viewport:{width:390, height:900}, userAgent:options.ua || 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'});
    await ctx.addCookies([{name:'sessionid', value:cfg.timeoutSeller, domain:'localhost', path:'/'}]);
    await ctx.addInitScript(options => {
      window.__notificationCalls = [];
      window.__permissionPrompts = 0;
      window.__trustedPrompts = [];
      window.__blockAudio = !!options.blockAudio;
      window.__noticeError = false;
      window.__permissionStatus = new EventTarget();
      Object.defineProperty(navigator, 'permissions', {value:{query:async () => window.__permissionStatus}, configurable:true});
      class FakeNotification {
        static permission = options.permission || 'default';
        static async requestPermission() {
          window.__permissionPrompts++;
          window.__trustedPrompts.push(navigator.userActivation.isActive);
          if (options.permissionError) throw new Error('Permission request failed');
          if (options.deferPermission) await new Promise(resolve => { window.__resolvePermission = resolve; });
          return FakeNotification.permission = options.result || 'granted';
        }
        constructor(title, opts) {
          if (window.__noticeError) throw new Error('Desktop display unavailable');
          this.title = title; this.opts = opts; this.closed = false;
          window.__notificationCalls.push(this);
        }
        close() { this.closed = true; }
      }
      if (options.unsupported) delete window.Notification;
      else Object.defineProperty(window, 'Notification', {value:FakeNotification, configurable:true});
      const play = HTMLMediaElement.prototype.play;
      const load = HTMLMediaElement.prototype.load;
      window.__audioLoads = 0;
      HTMLMediaElement.prototype.load = function() { window.__audioLoads++; return load.call(this); };
      HTMLMediaElement.prototype.play = function() {
        if (window.__blockAudio) return Promise.reject(new DOMException('blocked', 'NotAllowedError'));
        const actual = play.call(this);
        if (options.deferAudio && !window.__resolveAudio) return actual.then(() => new Promise(resolve => { window.__resolveAudio = resolve; }));
        return actual;
      };
      if (options.saved) sessionStorage.setItem(options.saved, 'enabled');
    }, options);
    const p = await ctx.newPage();
    p.on('pageerror', error => errors.push(String(error)));
    // Exercise the alert component independently; the full phase7 check covers live polling/actions.
    await p.route('**/js/seller-orders.js', route => route.fulfill({contentType:'application/javascript', body:''}));
    await p.goto(cfg.base + '/accounts/seller/');
    await p.locator('[data-alert-enable]').waitFor();
    assert.equal(await p.getByRole('button', {name:'알림 활성화', exact:true}).count(), 1);
    assert.equal(await p.locator('[data-desktop-enable]').count(), 0);
    assert.equal(await p.evaluate(() => window.__permissionPrompts), 0);
    assert(await p.locator('[data-alert-setup]').isHidden());
    await state(p, false);
    return {ctx, p};
  }
  async function state(p, ready) {
    await p.waitForFunction(text => document.querySelector('[data-alert-message]').textContent === text, ready ? active : inactive);
    assert.equal(await p.locator('[data-alert-check]').isVisible(), ready);
    if (ready) assert.equal(await p.locator('[data-alert-check]').evaluate(x => getComputedStyle(x).color), 'rgb(22, 128, 60)');
  }
  const queue = (p, ids) => p.evaluate(ids => document.dispatchEvent(new CustomEvent('seller:queue', {detail:{pending:ids, is_open:true}})), ids);
  const start = p => p.getByRole('button', {name:'알림 활성화', exact:true}).click();
  const looping = p => p.waitForFunction(() => { const a = document.querySelector('audio'); return !a.paused && a.loop && a.currentTime > 0; });
  try {
    // One click requests permission even if audio fails; retry recovers without another permission prompt.
    let {ctx, p} = await open({blockAudio:true});
    await queue(p, [1, 2]); await start(p);
    await p.locator('[data-alert-detail]').filter({hasText:'소리를 재생하지 못했어요.'}).waitFor();
    await state(p, false);
    assert(await p.locator('[data-alert-setup]').isVisible());
    assert.equal(await p.locator('[data-sound-state]').textContent(), '재생 실패');
    assert.equal(await p.locator('[data-desktop-state]').textContent(), '허용됨');
    assert.equal(await p.evaluate(() => window.__permissionPrompts), 1);
    assert.deepEqual(await p.evaluate(() => window.__trustedPrompts), [true]);
    assert(await p.locator('audio').evaluate(x => x.paused));
    await p.evaluate(() => { window.__blockAudio = false; }); await start(p);
    await state(p, true); await looping(p);
    assert(await p.locator('[data-alert-setup]').isHidden());
    assert.equal(await p.evaluate(() => window.__permissionPrompts), 1);
    await queue(p, [2]); await looping(p);
    await p.evaluate(() => document.dispatchEvent(new Event('seller:offline'))); await state(p, true);
    await queue(p, []);
    await p.waitForFunction(() => document.querySelector('audio').paused);
    assert(await p.evaluate(() => window.__notificationCalls.every(n => n.closed)));
    await state(p, true);
    // Revocation is reflected on return to the page; sound continues as fallback.
    await p.evaluate(() => { Notification.permission = 'denied'; dispatchEvent(new Event('focus')); });
    await state(p, false); await queue(p, [3]); await looping(p);
    await p.evaluate(() => { Notification.permission = 'granted'; dispatchEvent(new Event('focus')); });
    await state(p, true);
    await p.evaluate(() => document.querySelector('audio').dispatchEvent(new Event('error')));
    await state(p, false); assert(await p.locator('audio').evaluate(x => x.paused));
    await start(p); await state(p, true); await looping(p);
    assert.equal(await p.evaluate(() => window.__audioLoads), 1);
    // Permission changes outside the page recover automatically, without another click or prompt.
    await p.evaluate(() => {
      window.__blockAudio = true; Notification.permission = 'denied';
      window.__permissionStatus.dispatchEvent(new Event('change'));
      document.querySelector('audio').dispatchEvent(new Event('error'));
    });
    await state(p, false);
    assert.match(await p.locator('[data-alert-instructions]').textContent(), /사이트 정보 아이콘/);
    await p.evaluate(() => {
      window.__blockAudio = false; Notification.permission = 'granted';
      window.__permissionStatus.dispatchEvent(new Event('change'));
    });
    await state(p, true); await looping(p);
    await p.evaluate(() => { window.__noticeError = true; }); await queue(p, [3, 4]);
    await state(p, false);
    await p.evaluate(() => { window.__noticeError = false; }); await start(p); await state(p, true);
    await p.evaluate(() => document.dispatchEvent(new Event('seller:stop')));
    await p.waitForFunction(() => document.querySelector('audio').paused);
    // A saved preference plus granted permission is insufficient after reload until playback succeeds.
    await p.reload();
    await p.evaluate(() => { window.__blockAudio = false; Notification.permission = 'granted'; dispatchEvent(new Event('focus')); });
    await state(p, false);
    await queue(p, [9]);
    // Depending on Chromium autoplay policy, reload either starts playback or requires a click.
    if (await p.locator('[data-alert-check]').isHidden()) await start(p);
    await state(p, true); await looping(p);
    // Logout invalidates readiness and stops sound without changing the real test account.
    await p.evaluate(() => {
      const form = document.createElement('form'); form.action = '/accounts/logout/';
      form.addEventListener('submit', e => e.preventDefault()); document.body.append(form);
      form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
    });
    await state(p, false); assert(await p.locator('audio').evaluate(x => x.paused));
    await ctx.close();
    console.log('Audio rejection/retry, combined permission request, queue lifecycle, revocation, media/display errors, reload and logout verified');

    for (const options of [{result:'denied'}, {result:'default'}, {permission:'denied'}, {unsupported:true}, {permissionError:true}]) {
      ({ctx, p} = await open(options)); await start(p);
      await p.waitForFunction(() => !document.querySelector('[data-alert-enable]').disabled);
      await p.waitForFunction(() => !!sessionStorage.getItem(document.querySelector('[data-seller-alerts]').dataset.storageKey));
      await state(p, false);
      const prompts = await p.evaluate(() => window.__permissionPrompts);
      assert.equal(prompts, options.unsupported || options.permission === 'denied' ? 0 : 1);
      assert(await p.locator('[data-desktop-status]').textContent());
      assert(await p.locator('[data-alert-setup]').isVisible());
      assert.equal(await p.locator('[data-sound-state]').textContent(), '재생 확인됨');
      if (options.result === 'denied' || options.permission === 'denied') {
        assert.equal(await p.locator('[data-desktop-state]').textContent(), '차단됨');
        assert.match(await p.locator('[data-alert-instructions]').textContent(), /사이트 설정/);
      }
      await ctx.close();
    }
    ({ctx, p} = await open({deferPermission:true}));
    await start(p);
    await p.waitForFunction(() => !!window.__resolvePermission);
    await state(p, false);
    assert(await p.locator('[data-alert-setup]').isVisible());
    assert.equal(await p.locator('[data-desktop-state]').textContent(), '허용 대기 중');
    await start(p);
    assert.equal(await p.evaluate(() => window.__permissionPrompts), 1);
    await p.evaluate(() => window.__resolvePermission()); await state(p, true);
    await p.waitForFunction(() => document.querySelector('audio').paused);
    await state(p, true); await ctx.close();
    ({ctx, p} = await open({permission:'granted', deferAudio:true}));
    await start(p); await p.waitForFunction(() => !!window.__resolveAudio);
    await state(p, false);
    await p.waitForTimeout(1700);
    assert.equal(await p.locator('[data-sound-state]').textContent(), '재생 확인 중');
    await p.evaluate(() => window.__resolveAudio()); await state(p, true);
    assert.equal(await p.evaluate(() => window.__permissionPrompts), 0);
    for (const width of [320, 390, 1440]) {
      await p.setViewportSize({width, height:1000});
      assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (cfg.output) await p.screenshot({path:cfg.output + `/alert-active-${width}.png`, fullPage:true});
    }
    await ctx.close();
    for (const [ua, expected] of [
      ['Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0', /사이트 권한 아이콘/],
      ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15', /Safari의 설정/]
    ]) {
      ({ctx, p} = await open({ua, permission:'denied'})); await start(p);
      assert.match(await p.locator('[data-alert-instructions]').textContent(), expected);
      await ctx.close();
    }
    // Sound test alone must not request desktop permission or claim full readiness.
    ({ctx, p} = await open());
    await p.getByRole('button', {name:'소리 테스트', exact:true}).click();
    await p.waitForFunction(() => !document.querySelector('audio').paused); await state(p, false);
    assert.equal(await p.evaluate(() => window.__permissionPrompts), 0);
    if (cfg.output) await p.screenshot({path:cfg.output + '/alert-inactive-390.png', fullPage:true});
    await ctx.close();
    // Exercise Chromium's real permission state/change events as well as the failure simulations above.
    ctx = await browser.newContext({viewport:{width:390, height:1000}});
    await ctx.addCookies([{name:'sessionid', value:cfg.timeoutSeller, domain:'localhost', path:'/'}]);
    await ctx.grantPermissions(['notifications'], {origin:cfg.base});
    p = await ctx.newPage();
    p.on('pageerror', error => errors.push(String(error)));
    await p.route('**/js/seller-orders.js', route => route.fulfill({contentType:'application/javascript', body:''}));
    await p.goto(cfg.base + '/accounts/seller/');
    assert.equal(await p.evaluate(() => Notification.permission), 'granted');
    await state(p, false); await start(p); await state(p, true);
    await ctx.clearPermissions();
    await ctx.grantPermissions([], {origin:cfg.base});
    await state(p, false);
    assert.equal(await p.locator('[data-desktop-state]').textContent(), '차단됨');
    for (const width of [320, 390, 1440]) {
      await p.setViewportSize({width, height:1000});
      assert(await p.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      if (cfg.output) await p.screenshot({path:cfg.output + `/alert-settings-${width}.png`, fullPage:true});
    }
    await ctx.grantPermissions(['notifications'], {origin:cfg.base});
    await state(p, true);
    assert(await p.locator('[data-alert-setup]').isHidden());
    await ctx.close();
    assert.deepEqual(errors, []);
    console.log('Guided setup, slow playback, real Chromium permission changes, permission denial/pending/failure, browser-specific guidance and 320/390/1440px layouts verified');
  } finally { await browser.close(); }
}
