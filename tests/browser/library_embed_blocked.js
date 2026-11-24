// The embed-mode dialog's OK closes it at once (2026-11-22, legi_F: set to
// "all p2p", the dialog said what blocks it, and OK left it open for as long
// as the Library took to load again).
//
//   node library_embed_blocked.js DIR STATIC  — run by tests/test_library_embed_blocked_browser.py
//
// DIR holds library.html as the app rendered it. Every request is answered
// here (tests/browser/harness.js): the embed-mode POST says the mode is
// blocked. The Library's reload (`reloadLibrary`) is stood in for and
// counted, since a real one destroys the page being read. Prints one JSON
// line: the dialog before OK, and whether it is still shown after OK.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC] = process.argv.slice(2);
const BASE = 'http://louise.test';
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const h = await launch({ width: 1100, height: 900 });
  const posts = [];
  h.route(async req => {
    const u = new URL(req.url);
    if (req.method === 'POST') {
      posts.push({ path: u.pathname, body: req.postData || '' });
      return req.respond({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, blocked: ['mode'], note: 'The node is in "download/seeding only" mode' }) });
    }
    if (u.pathname === '/admin/library') {
      return req.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(DIR + '/library.html', 'utf8') });
    }
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') });
    }
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  const page = h.page;
  const shown = () => page.evaluate(() => getComputedStyle(document.getElementById('modal-backdrop')).display !== 'none');
  await page.goto(BASE + '/admin/library', { waitUntil: 'load' });
  await sleep(200);
  await page.evaluate(() => {
    window.__reloads = 0;
    window.reloadLibrary = () => { window.__reloads += 1; };
    setEmbedMode('gutenberg', 'all', true);
  });
  for (let i = 0; i < 40 && !(await shown()); i++) await sleep(100);
  const out = { posts: posts.map(p => p.path), before: await shown(),
                text: await page.evaluate(() => document.getElementById('modal-backdrop').innerText.replace(/\s+/g, ' ').trim()) };
  await page.click('#modal-backdrop .actions button.primary');
  await sleep(100);
  out.reloads = await page.evaluate(() => window.__reloads);
  out.afterOk = await shown();
  await h.close();
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
