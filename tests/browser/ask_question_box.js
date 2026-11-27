// The question box fits its text (operator, 2026-11-27: "Fix the query text
// box to a minimum size, and expand it automatically to fit the text; the
// current manual resize is not handy"): two lines at least, growing as text
// is typed or set (a restored question, a dictated one), up to 40% of the
// screen and then scrolling, with no resize handle. Both ways: CSS's own
// `field-sizing: content`, and the page's fallback for a browser without it.
// argv: DIR holding ask.html, the app's static dir.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC] = process.argv.slice(2);
const W = 360, H = 740;

(async () => {
  const h = await launch({ width: W, height: H });
  h.route(req => {
    const u = new URL(req.url);
    if (u.pathname === '/ask') return req.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(DIR + '/ask.html', 'utf8') });
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') });
    }
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  const out = {};
  for (const mode of ['css', 'fallback']) {
    await h.page.goto('http://louise.test/ask');
    if (mode === 'fallback') await h.page.evaluate(() => {
      // a browser without field-sizing: the property gone, and CSS.supports saying so
      document.getElementById('q').style.fieldSizing = 'fixed';
      const real = CSS.supports.bind(CSS);
      CSS.supports = (p, v) => (p === 'field-sizing' ? false : real(p, v));
      fitQuestion();
    });
    const m = () => h.page.evaluate(() => {
      const q = document.getElementById('q'), cs = getComputedStyle(q);
      return { h: Math.round(q.getBoundingClientRect().height), lh: parseFloat(cs.lineHeight),
               resize: cs.resize, scroll: q.scrollHeight > q.clientHeight + 1 };
    });
    const set = (text, typed) => h.page.evaluate(([t, typed]) => {
      const q = document.getElementById('q'); q.value = t;
      if (typed) q.dispatchEvent(new Event('input', { bubbles: true })); else fitQuestion();
    }, [text, typed]);
    const empty = await m();
    await set('One short line', true); const one = await m();
    await set(Array(6).fill('a line of the question that wraps on a phone screen').join(' '), true); const six = await m();
    await set(Array(80).fill('a very long question').join(' '), false); const huge = await m();
    await set('', true); const back = await m();
    out[mode] = { empty, one, six, huge, back };
  }
  const fails = [];
  const check = (ok, what, got) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${what}${ok ? '' : ' — ' + JSON.stringify(got)}`); if (!ok) fails.push(what); };
  for (const [mode, r] of Object.entries(out)) {
    check(r.empty.resize === 'none', `${mode}: no resize handle`, r.empty);
    check(r.empty.h >= 2 * r.empty.lh && r.empty.h < 4 * r.empty.lh, `${mode}: two lines at least, empty`, r.empty);
    check(r.one.h === r.empty.h, `${mode}: a short question keeps the minimum`, [r.empty, r.one]);
    check(r.six.h > r.one.h + 2 * r.one.lh && !r.six.scroll, `${mode}: a longer one grows to fit, no scrollbar`, r.six);
    check(r.huge.h <= Math.ceil(0.4 * H) + 1 && r.huge.scroll, `${mode}: no taller than 40% of the screen, then it scrolls`, r.huge);
    check(r.back.h === r.empty.h, `${mode}: emptied, back to the minimum`, [r.empty, r.back]);
  }
  console.log(fails.length ? `${fails.length} FAILED` : 'ALL PASSED');
  await h.close();
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
