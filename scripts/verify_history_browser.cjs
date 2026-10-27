const {chromium} = require('playwright');
const assert = require('node:assert/strict');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => run(JSON.parse(input)).catch(error => {console.error(error); process.exitCode = 1;}));

async function run(config) {
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium', args:['--no-sandbox']});
  try {
    for (const entry of config.cases) {
      const context = await browser.newContext({viewport:{width:entry.width,height:900}});
      await context.addCookies([{name:'sessionid',value:config.cookie,domain:'localhost',path:'/'}]);
      const page = await context.newPage();
      const errors = []; page.on('pageerror', error => errors.push(String(error)));
      await page.goto(config.base + '/accounts/operator/?tab=orders');
      const cafes = page.getByRole('navigation',{name:'카페별 주문서'});
      await cafes.getByRole('link',{name:entry.name,exact:true}).click();
      assert.equal(await page.locator('.operator-order-card').count(),20);
      assert.equal(await page.locator('.history-results-heading > span').innerText(),'총 24개');
      let card = page.locator('.operator-order-card').first();
      assert((await card.innerText()).includes(entry.member));
      assert((await card.innerText()).includes('총 2잔'));
      assert((await card.innerText()).includes('6000원'));
      await page.getByLabel('회원 이름(닉네임)',{exact:true}).fill(entry.member);
      await page.getByLabel('주문 날짜',{exact:true}).fill(config.date);
      await page.getByRole('button',{name:'조회',exact:true}).click();
      assert.equal(await page.locator('.history-results-heading > span').innerText(),'총 23개');
      assert.equal(new URL(page.url()).searchParams.get('cafe'),String(entry.cafe));
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path:config.output + '/listing-' + entry.width + '.png',fullPage:false});
      card = page.locator('.operator-order-card').first();
      await card.screenshot({path:config.output + '/card-' + entry.width + '.png'});
      assert.equal(await card.getAttribute('href'),'/orders/' + entry.room + '/');
      await card.click();
      await page.waitForURL('**/orders/' + entry.room + '/');
      await page.goBack();
      await page.getByRole('navigation',{name:'주문 이력 페이지'}).getByRole('link',{name:'다음',exact:true}).click();
      assert.equal(await page.locator('.operator-order-card').count(),3);
      assert.equal(await page.getByLabel('회원 이름(닉네임)',{exact:true}).inputValue(),entry.member);
      assert.equal(await page.getByLabel('주문 날짜',{exact:true}).inputValue(),config.date);
      for (const label of await page.locator('.operator-order-card h3').allTextContents()) assert.equal(label,entry.name);
      await cafes.getByRole('link',{name:entry.other_name,exact:true}).click();
      assert.equal(await page.locator('.operator-order-card').count(),0);
      await page.getByRole('link',{name:'검색 초기화',exact:true}).click();
      assert.equal(new URL(page.url()).searchParams.get('cafe'),String(entry.other_cafe));
      assert.equal(await page.locator('.operator-order-card').count(),1);
      const other = await page.locator('.operator-order-card').innerText();
      assert(other.includes(entry.other_member) && other.includes('총 3잔') && other.includes('12000원'));
      await page.goto(config.base + '/accounts/operator/?tab=orders&cafe=' + entry.cafe + '&date=invalid');
      assert.equal(await page.locator('.operator-order-card').count(),0);
      assert.equal(await cafes.getByRole('link',{name:entry.name,exact:true}).getAttribute('aria-current'),'page');
      assert(await page.locator('.history-order-search .errorlist').isVisible());
      assert.deepEqual(errors,[]);
      console.log(entry.width + 'px: cafe/member/date filtering, summary before opening, preserved pagination and invalid search passed');
      await context.close();
    }
  } finally { await browser.close(); }
}
