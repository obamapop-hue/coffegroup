const {chromium}=require('playwright');const assert=require('node:assert/strict');let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>run(JSON.parse(input)).catch(e=>{console.error(e);process.exitCode=1}));
async function run(cfg){const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--autoplay-policy=document-user-activation-required']});try{
const ctx=await browser.newContext({viewport:{width:390,height:900}});await ctx.addCookies([{name:'sessionid',value:cfg.timeoutSeller,domain:'localhost',path:'/'}]);
await ctx.addInitScript(()=>{
 window.__notificationCalls=[]; window.__permissionPrompts=0;window.__blockAudio=true;
 class FakeNotification{static permission='default';static async requestPermission(){window.__permissionPrompts++;return FakeNotification.permission='granted';}constructor(title,opts){this.title=title;this.opts=opts;this.closed=false;window.__notificationCalls.push(this)}close(){this.closed=true}}
 Object.defineProperty(window,'Notification',{value:FakeNotification,configurable:true});
 const play=HTMLMediaElement.prototype.play;HTMLMediaElement.prototype.play=function(){return window.__blockAudio?Promise.reject(new DOMException('blocked','NotAllowedError')):play.call(this)};
});
const p=await ctx.newPage();const errors=[];p.on('pageerror',e=>errors.push(String(e)));await p.goto(cfg.base+'/accounts/seller/');
await p.locator('[data-queue-alert]').waitFor();assert.equal(await p.evaluate(()=>window.__permissionPrompts),0);
await p.locator('[data-alert-enable]').click();await p.getByRole('button',{name:'주문 알림 다시 켜기',exact:true}).waitFor();assert(await p.locator('audio').evaluate(x=>x.paused));
await p.evaluate(()=>window.__blockAudio=false);await p.getByRole('button',{name:'주문 알림 다시 켜기',exact:true}).click();await p.waitForFunction(()=>!document.querySelector('audio').paused&&document.querySelector('audio').loop);
await p.locator('[data-desktop-enable]').click();await p.waitForFunction(()=>window.__notificationCalls.length===1);assert.equal(await p.evaluate(()=>window.__permissionPrompts),1);
await p.evaluate(()=>document.dispatchEvent(new CustomEvent('seller:queue',{detail:{pending:[],is_open:true}})));
await p.waitForFunction(()=>document.querySelector('audio').paused);assert.equal(await p.evaluate(()=>window.__notificationCalls[0].closed),true);
await p.evaluate(()=>{Notification.permission='denied';document.dispatchEvent(new CustomEvent('seller:queue',{detail:{pending:[1234],is_open:true}}));});
assert.equal(await p.evaluate(()=>window.__notificationCalls.length),1);await p.waitForFunction(()=>!document.querySelector('audio').paused);
await p.evaluate(()=>document.dispatchEvent(new Event('seller:stop')));await p.waitForFunction(()=>document.querySelector('audio').paused);assert.deepEqual(errors,[]);
console.log('Blocked autoplay/re-enable; explicit desktop permission; notice lifecycle/denial fallback verified in browser (notification API simulated)');
}finally{await browser.close()}}
