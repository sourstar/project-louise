// The Ask switch on a phone (operator, 2026-11-01): "Search only" and
// "Search + AI summary" on one row inside the screen, the font as on a
// desktop (12px), the words wrapped inside a choice only if they must.
// The one Quality / Speed group (operator, 2026-11-09; one switch 2026-11-12) is read on its own.
// argv: DIR holding ask.html, the app's static dir, the width.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC, WIDTH] = process.argv.slice(2);

(async () => {
  const h = await launch({ width: +WIDTH, height: 800 });
  h.route(req => {
    const u = new URL(req.url);
    if (u.pathname === '/ask') return req.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(DIR + '/ask.html', 'utf8') });
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') });
    }
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  await h.page.goto('http://louise.test/ask');
  const out = await h.page.evaluate(() => {
    const labels = [...document.querySelectorAll('.seg[aria-label="what Ask gives"] label')];
    const speed = [...document.querySelectorAll('.seg[aria-label="quality or speed"] label')];
    return {
      speedTexts: speed.map(l => l.textContent.trim()),
      speedBoxes: speed.map(l => { const r = l.getBoundingClientRect(); return { left: r.left, right: r.right }; }),
      speedChecked: (document.querySelector('input[name="speed"]:checked') || {}).value || null,
      groups: [...document.querySelectorAll('.ask-row .seg')].map(g => g.getAttribute('aria-label')),
      textInputs: document.querySelectorAll('input[name="textsearch"]').length,
      texts: labels.map(l => l.textContent.trim()),
      boxes: labels.map(l => { const r = l.getBoundingClientRect(); return { top: r.top, left: r.left, right: r.right, height: r.height }; }),
      fonts: labels.map(l => getComputedStyle(l).fontSize),
      width: document.documentElement.clientWidth,
      lineHeight: parseFloat(getComputedStyle(labels[0]).lineHeight) || 0,
    };
  });
  console.log(JSON.stringify(out));
  await h.close();
})().catch(e => { console.error(e); process.exit(1); });
