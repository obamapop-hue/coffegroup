const {chromium}=require('playwright');const assert=require('node:assert/strict');
let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>run(JSON.parse(input)).catch(e=>{console.error(e);process.exitCode=1}));
async function run(cfg){const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [320,390,1440]){
const guest=await browser.newContext({viewport:{width,height:900}}),stranger=await browser.newContext(),admin=await browser.newContext({viewport:{width,height:900}});
await admin.addCookies([{name:'sessionid',value:cfg.operator,domain:'localhost',path:'/'}]);
const a=await guest.newPage(),b=await admin.newPage(),c=await stranger.newPage();const errors=[];[a,b,c].forEach(p=>p.on('pageerror',e=>errors.push(String(e))));
const subject='주문 문의 '+width;const body='다른 사람에게 보이면 안 되는 문의 <script>bad()</script>';
await a.goto(cfg.base+'/help/');await a.getByLabel('제목:',{exact:true}).fill(subject);await a.getByLabel('내용:',{exact:true}).fill(body);
await Promise.all([a.waitForURL(/\/help\/\d+\/$/),a.getByRole('button',{name:'문의 접수',exact:true}).click()]);
const url=a.url();await a.getByText(body,{exact:true}).waitFor();
const forbidden=await c.goto(url);assert.equal(forbidden.status(),404);assert(!(await c.content()).includes(body));
await b.goto(cfg.base+'/help/manage/');await b.getByRole('link',{name:new RegExp(subject)}).click();
await b.getByLabel('내용:',{exact:true}).fill('영차에서 답변했어요.');
await b.getByRole('button',{name:'답변 등록',exact:true}).click();await b.getByText('영차에서 답변했어요.',{exact:true}).waitFor();
await a.reload();await a.getByText('영차에서 답변했어요.',{exact:true}).waitFor();
await a.getByLabel('내용:',{exact:true}).fill('추가로 확인해 주세요.');await a.getByRole('button',{name:'추가 문의 등록',exact:true}).click();await a.getByText('추가로 확인해 주세요.',{exact:true}).waitFor();
assert((await a.getByText('답변 대기',{exact:true}).count())===1);
for(const page of [a,b])assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
await a.screenshot({path:cfg.output+'/conversation-'+width+'.png',fullPage:true});
assert.deepEqual(errors,[]);await guest.close();await stranger.close();await admin.close();console.log(width+'px: anonymous support, private access, real CSRF reply/follow-up and no horizontal overflow verified');
}}finally{await browser.close();}}
