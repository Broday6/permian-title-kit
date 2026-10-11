const { chromium } = require(require('child_process').execSync('npm root -g').toString().trim() + '/playwright');
(async () => {
  const browser = await chromium.launch();
  const file = 'file://' + process.cwd() + '/permian-title-playbook.html';
  const errors = [];
  for (const [name, vp, scheme] of [['desktop', { width: 1280, height: 900 }, 'light'], ['phone', { width: 400, height: 860 }, 'dark']]) {
    const page = await browser.newPage({ viewport: vp, colorScheme: scheme });
    page.on('pageerror', e => errors.push(name + ': ' + e.message));
    page.on('console', m => { if (m.type() === 'error') errors.push(name + ' console: ' + m.text()); });
    await page.goto(file, { waitUntil: 'load' });
    await page.waitForTimeout(800);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    console.log(name, 'horizontal overflow px:', overflow, 'calc:', await page.textContent('#c-out .big').catch(() => 'n/a'), 'rows:', await page.textContent('#county-count'));
    await page.screenshot({ path: `shot-${name}.png` });
    await page.locator('#math').screenshot({ path: `shot-${name}-calc.png` });
    await page.close();
  }
  console.log('errors:', errors.filter(e => !/fonts\.g/.test(e)));
  await browser.close();
})();
