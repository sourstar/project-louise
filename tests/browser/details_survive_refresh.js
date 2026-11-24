// A drawer the reader opened stays open while its panel refreshes (operator,
// 2026-11-02: the verbose text behind one closed line, `details.more`).
//
//   node details_survive_refresh.js BASE_URL DRAWER_ID  — run by tests/test_details_survive_refresh_browser.py
//
// The System tab's GPU panel is swapped in by htmx every 5 s; its drawers
// carry `data-keep-open` and an id, and system.html opens again after a swap
// what the reader had open (2026-11-11; before, `hx-preserve`, which froze
// their contents). Opens the drawer named, waits for two refreshes, and
// checks it is still open and still one element. Prints PASS/FAIL; exit 0
// on PASS.
// Uses puppeteer-core where the dev image has it, Playwright otherwise.
const fs = require('fs');
const [BASE, DRAWER] = process.argv.slice(2);
const SUMMARY = `#${DRAWER} > summary`;
const TOKEN = process.env.ADMIN_TOKEN || '';
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function open() {
  const exe = process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROMIUM
    || ['/usr/bin/chromium', '/usr/bin/chromium-browser'].find(p => fs.existsSync(p));
  try {
    const puppeteer = require('puppeteer-core');
    const browser = await puppeteer.launch({ executablePath: exe, args: ['--no-sandbox', '--disable-gpu'],
                                             headless: /headless-shell/.test(exe || '') ? 'shell' : true });
    const page = await browser.newPage();
    if (TOKEN) await page.setCookie({ name: 'admin_token', value: TOKEN, url: BASE });
    return { browser, page };
  } catch (e) {
    const { chromium } = require('playwright');
    const browser = await chromium.launch(exe ? { executablePath: exe } : {});
    const ctx = await browser.newContext();
    if (TOKEN) await ctx.addCookies([{ name: 'admin_token', value: TOKEN, url: BASE }]);
    return { browser, page: await ctx.newPage() };
  }
}

(async () => {
  const { browser, page } = await open();
  let swaps = 0;
  page.on('response', r => { if (r.url().endsWith('/admin/system/gpu')) swaps++; });
  await page.goto(BASE + '/admin/system', { waitUntil: 'load' });
  await page.waitForSelector(SUMMARY);
  await page.click(SUMMARY);
  const opened = await page.evaluate(id => document.getElementById(id).open, DRAWER);
  const from = swaps;
  for (let i = 0; i < 30 && swaps - from < 2; i++) await sleep(500);
  await sleep(300);                                    // the swap after the answer
  const r = await page.evaluate(id => ({
    open: document.getElementById(id).open,
    count: document.querySelectorAll('#' + id).length }), DRAWER);
  await browser.close();
  const ok = opened && swaps - from >= 2 && r.open && r.count === 1;
  console.log(`${ok ? 'PASS' : 'FAIL'} opened=${opened} refreshes=${swaps - from} still open=${r.open} copies=${r.count}`);
  process.exit(ok ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
