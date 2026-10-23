// Companion to verify_phase7.py: real HTTP, CSRF, browser audio and 300-second deadline.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
let input=''; process.stdin.on('data',chunk=>input+=chunk);
process.stdin.on('end',()=>run(JSON.parse(input)).catch(error=>{console.error(error);process.exitCode=1;}));
async function run(cfg) {
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox','--autoplay-policy=document-user-activation-required']});
  let progress;
  try {
    const ctx = async(cookie, width=1440)=>{
      const context=await browser.newContext({viewport:{width,height:1000}});
      await context.addCookies([{name:'sessionid',value:cookie,domain:'localhost',path:'/'}]); return context;
    };
    const owner=await ctx(cfg.owner), seller=await ctx(cfg.seller), deadline=await ctx(cfg.timeoutSeller);
    const a=await owner.newPage(), b=await seller.newPage(), d=await deadline.newPage();
    const errors=[]; [a,b,d].forEach(p=>p.on('pageerror',error=>errors.push(String(error))));
    await a.goto(cfg.base+'/accounts/organizer/');
    await b.goto(cfg.base+'/accounts/seller/'); await d.goto(cfg.base+'/accounts/seller/');
    await d.locator('[data-alert-enable]').click();
    await d.waitForFunction(()=>{const x=document.querySelector('audio');return x.loop&&!x.paused;});
    const timeoutVerified = (async()=>{
      await d.locator(`[data-pending-order="${cfg.timeoutOrder}"]`).waitFor({state:'detached',timeout:320000});
      await d.waitForFunction(()=>document.querySelector('audio').paused);
      assert(Date.now()-cfg.sentAt>=300000);
      const state=await owner.request.get(cfg.base+`/orders/${cfg.timeoutOrder}/state/`);
      assert.equal((await state.json()).status,'rejected');
      const notices=await owner.request.get(cfg.base+'/orders/notifications/');
      assert((await notices.json()).notifications.some(n=>n.message.includes('5분 미응답')));
      console.log('Real 300-second timeout: rejected DB state, removed pending card, stopped loop and organizer notice verified');
    })();
    progress=setInterval(()=>console.log('Real deadline verification in progress: '+Math.max(0,Math.ceil((cfg.sentAt+300000-Date.now())/1000))+' seconds remain'),30000);
    const csrf=async page=>page.locator('[name=csrfmiddlewaretoken]').first().getAttribute('value');
    const post=async(context,path,data,page=a)=>{
      const response=await context.request.post(cfg.base+path,{form:{csrfmiddlewaretoken:await csrf(page),...data}});
      assert.equal(response.status(),200,await response.text()); return response;
    };
    const send=async name=>{const o=cfg.orders[name];await post(owner,`/orders/${o.id}/send/`,{confirmation_token:o.token});return o.id;};
    const cancel=async id=>post(owner,`/orders/${id}/cancel/`,{});
    const card=id=>b.locator(`[data-pending-order="${id}"]`);
    const looping=()=>b.waitForFunction(()=>{const x=document.querySelector('audio');return !x.paused&&x.loop&&x.currentTime>0;},{},{timeout:15000});
    await b.locator('[data-alert-test]').click();
    await b.waitForFunction(()=>!document.querySelector('audio').paused);
    await b.waitForFunction(()=>document.querySelector('audio').paused);
    const marker=await b.evaluate(()=>window.__verificationMarker=Math.random());
    const one=await send('first'), two=await send('second');
    await card(two).waitFor({timeout:16000}); await looping();
    const select=card(one).locator('select'); await select.selectOption({index:3});
    const chosen=await select.inputValue();
    await card(two).locator('summary').click(); await card(two).locator('input[name=reason]').fill('작성 중인 사유');
    const three=await send('third'); await card(three).waitFor({timeout:16000});
    assert.equal(await select.inputValue(),chosen);
    assert.equal(await card(two).locator('input[name=reason]').inputValue(),'작성 중인 사유');
    assert.equal(await b.evaluate(()=>window.__verificationMarker),marker);
    await new Promise(resolve=>setTimeout(resolve,6500)); await looping();
    await card(one).getByRole('button',{name:'주문 접수',exact:true}).click();
    await b.locator(`[data-accepted-order="${one}"]`).waitFor(); await looping();
    assert.equal(await card(two).locator('input[name=reason]').inputValue(),'작성 중인 사유');
    await card(two).locator('input[name=reason]').fill('테스트 품절');
    await card(two).getByRole('button',{name:'거절하기',exact:true}).click();
    await b.locator(`[data-history-order="${two}"]`).waitFor({state:'attached'}); await looping();
    await cancel(three); await card(three).waitFor({state:'detached',timeout:16000});
    await b.waitForFunction(()=>document.querySelector('audio').paused);
    console.log('Multiple pending orders, input preservation, live accept/reject/cancel and uninterrupted looping verified');
    const offline=await send('offline'); await card(offline).waitFor({timeout:16000}); await looping();
    await b.route('**/orders/seller/feed/',route=>route.abort('internetdisconnected'));
    await cancel(offline);
    await b.getByText('주문을 다시 확인하고 있어요. 연결이 복구되면 자동으로 갱신돼요.',{exact:true}).waitFor({timeout:16000});
    await b.unroute('**/orders/seller/feed/'); await b.evaluate(()=>dispatchEvent(new Event('online')));
    await card(offline).waitFor({state:'detached',timeout:16000}); await b.waitForFunction(()=>document.querySelector('audio').paused);
    const recover=await send('recover'); await card(recover).waitFor({timeout:16000});
    await b.reload(); await card(recover).waitFor();
    await b.waitForTimeout(1500);
    if(await b.locator('[data-alert-enable]').isVisible()) await b.locator('[data-alert-enable]').click();
    await looping(); await cancel(recover); await card(recover).waitFor({state:'detached',timeout:16000});
    await b.waitForFunction(()=>document.querySelector('audio').paused);
    console.log('Network recovery and reload with pending orders verified');
    const external=await send('external'); await card(external).waitFor({timeout:16000});
    await card(external).locator('select').selectOption({index:2});
    const when=await card(external).locator('select').inputValue();
    await post(seller,`/orders/seller/${external}/accept/`,{ready_at:when},b);
    await card(external).waitFor({state:'detached',timeout:16000});
    await b.waitForFunction(()=>document.querySelector('audio').paused);
    for(const width of [320,390,1440]){
      await b.setViewportSize({width,height:1000});
      assert(await b.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
      await b.screenshot({path:cfg.output+`/seller-${width}.png`,fullPage:true});
    }
    const logout=await send('logout'); await card(logout).waitFor({timeout:16000}); await looping();
    await post(seller,'/accounts/logout/',{},b);
    await b.getByText('판매자 로그인 상태를 확인한 뒤 새로고침해 주세요.',{exact:true}).waitFor({timeout:16000});
    await b.waitForFunction(()=>document.querySelector('audio').paused);
    console.log('External decisions remove dirty cards; mobile layout and logout stop verified');
    assert.deepEqual(errors,[]);
    await timeoutVerified;
    clearInterval(progress);
  } finally {clearInterval(progress);await browser.close();}
}
