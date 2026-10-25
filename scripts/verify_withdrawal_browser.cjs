const {chromium} = require('playwright');
const assert = require('node:assert/strict');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => run(JSON.parse(input)).catch(error => {console.error(error); process.exitCode = 1;}));

async function run(config) {
  const browser = await chromium.launch({executablePath: '/usr/bin/chromium', args: ['--no-sandbox']});
  const contexts = [];
  async function pageFor(cookie, width) {
    const context = await browser.newContext({viewport: {width, height: 900}});
    contexts.push(context);
    await context.addCookies([{name: 'sessionid', value: cookie, domain: 'localhost', path: '/'}]);
    return context.newPage();
  }
  try {
    for (const entry of config.cases) {
      const unverified = await pageFor(entry.unverified, entry.width);
      await unverified.goto(config.base + '/accounts/withdraw/');
      assert.equal(await unverified.getByRole('button', {name: '카카오로 본인 확인', exact: true}).count(), 1);
      assert.equal(await unverified.getByRole('button', {name: '회원 탈퇴하기', exact: true}).count(), 0);
      const page = await pageFor(entry.verified, entry.width);
      const errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      await page.goto(config.base + '/accounts/withdraw/');
      const checkbox = page.getByRole('checkbox');
      await checkbox.check();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path: config.output + '/confirmation-' + entry.width + '.png', fullPage: true});
      await Promise.all([page.waitForURL('**/accounts/withdraw/done/'), page.getByRole('button', {name: '회원 탈퇴하기', exact: true}).click()]);
      assert.equal(await page.getByRole('button', {name: '로그아웃', exact: true}).count(), 0);
      assert.deepEqual(errors, []);
      const admin = await pageFor(config.operator, entry.width);
      await admin.goto(config.base + '/accounts/privacy-preview/');
      await admin.getByText('운영자 확인용 · 아직 공개하지 않은 초안', {exact: true}).waitFor();
      assert(await admin.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      console.log(entry.width + 'px: fresh-proof gate, real CSRF confirmation, fake member deletion/logout and private draft verified');
    }
  } finally {
    await Promise.all(contexts.map(context => context.close()));
    await browser.close();
  }
}
