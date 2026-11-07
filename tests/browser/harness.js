// A browser for the Ask page's tests: puppeteer-core where the dev image has
// it, Playwright otherwise (this is what tests/browser_support.py finds).
// Every request the page makes goes to `route(handler)`; the handler gets
// { url, method, postData, headers, raw, respond({status, contentType, body}) } and
// `raw` is the object `onFailed` reports when the page aborts a request.
// `args`: more Chromium flags (a fake microphone, the autoplay policy);
// `resize(width, height)` changes the viewport.
const fs = require('fs');

async function launch({ width, height, args = [] }) {
  const exe = process.env.PUPPETEER_EXECUTABLE_PATH || process.env.CHROMIUM
    || ['/usr/bin/chromium', '/usr/bin/chromium-browser'].find(p => fs.existsSync(p));
  let puppeteer = null;
  try { puppeteer = require('puppeteer-core'); } catch (e) { /* Playwright below */ }
  if (puppeteer) {
    const browser = await puppeteer.launch({ executablePath: exe, args: ['--no-sandbox', '--disable-gpu', ...args],
                                             headless: /headless-shell/.test(exe || '') ? 'shell' : true });
    const page = await browser.newPage();
    await page.setViewport({ width, height });
    await page.setRequestInterception(true);
    return {
      page,
      route: handler => page.on('request', r => handler({
        url: r.url(), method: r.method(), postData: r.postData(), headers: r.headers(), raw: r,
        respond: o => r.respond(o) })),
      onFailed: fn => page.on('requestfailed', fn),
      select: (sel, v) => page.select(sel, v),
      resize: (w, h) => page.setViewport({ width: w, height: h }),
      close: () => browser.close(),
    };
  }
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ ...(exe ? { executablePath: exe } : {}), args });
  const page = await browser.newPage({ viewport: { width, height } });
  return {
    page,
    route: handler => page.route('**/*', route => handler({
      url: route.request().url(), method: route.request().method(), postData: route.request().postData(),
      headers: route.request().headers(), raw: route.request(),
      respond: o => route.fulfill({ status: o.status, contentType: o.contentType, body: o.body }) })),
    onFailed: fn => page.on('requestfailed', fn),
    select: (sel, v) => page.selectOption(sel, v),
    resize: (w, h) => page.setViewportSize({ width: w, height: h }),
    close: () => browser.close(),
  };
}

module.exports = { launch };
