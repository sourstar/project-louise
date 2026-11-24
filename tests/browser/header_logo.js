// The title on the logo's middle (operator, 2026-11-16: "Align the title
// "Project Louise" to the center of the logo; it looks currently aligned to
// the top"), and the menu clear of the logo, at a desktop, a narrow and a
// phone width.
//
//   node header_logo.js FILE_URL   — run by tests/test_header_logo_browser.py
//
// Prints one PASS/FAIL line per width; exit 0 only when all pass. Uses
// puppeteer-core where the dev image has it, Playwright otherwise.
const fs = require('fs');
const [URL] = process.argv.slice(2);

async function open(width) {
  const exe = process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROMIUM
    || ['/usr/bin/chromium', '/usr/bin/chromium-browser'].find(p => fs.existsSync(p));
  try {
    const puppeteer = require('puppeteer-core');
    const browser = await puppeteer.launch({ executablePath: exe, args: ['--no-sandbox', '--disable-gpu'],
                                             headless: /headless-shell/.test(exe || '') ? 'shell' : true });
    const page = await browser.newPage();
    await page.setViewport({ width, height: 600 });
    return { browser, page };
  } catch (e) {
    const { chromium } = require('playwright');
    const browser = await chromium.launch(exe ? { executablePath: exe } : {});
    return { browser, page: await browser.newPage({ viewport: { width, height: 600 } }) };
  }
}

(async () => {
  let failed = 0;
  for (const width of [1280, 820, 390]) {
    const { browser, page } = await open(width);
    await page.goto(URL, { waitUntil: 'load' });
    const r = await page.evaluate(() => {
      const logo = document.querySelector('nav a.logo img').getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(document.querySelector('nav .brand'));
      const text = range.getBoundingClientRect();
      const menu = document.querySelector('nav .menu a').getBoundingClientRect();
      return { logoMid: logo.top + logo.height / 2, textMid: text.top + text.height / 2,
               logoBottom: logo.bottom, menuTop: menu.top, logoLeft: logo.left };
    });
    const ok = Math.abs(r.logoMid - r.textMid) <= 1 && r.menuTop - r.logoBottom >= 4 && r.logoLeft <= 16;
    if (!ok) failed++;
    console.log(`${ok ? 'PASS' : 'FAIL'} ${width}px ${JSON.stringify(r)}`);
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
})().catch(e => { console.log('FAIL', e && e.stack || e); process.exit(1); });
