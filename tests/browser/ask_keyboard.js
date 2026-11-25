// The on-screen keyboard never covers the question (operator, on an Android
// phone, 2026-11-25): while #q has the focus it stays inside what the
// keyboard leaves, with room above it for the cut/copy/paste menu. A headless
// browser has no keyboard, so its two models are played: the one that RESIZES
// the page (interactive-widget=resizes-content: the viewport itself shrinks)
// and the one that lies OVER it (only window.visualViewport shrinks — stood
// in for by an object of the same shape).
// argv: DIR holding ask.html, the app's static dir.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC] = process.argv.slice(2);
const W = 360, H = 740, KEYBOARD = 340;          // a phone, and what a keyboard takes of it

(async () => {
  const h = await launch({ width: W, height: H });
  h.route(req => {
    const u = new URL(req.url);
    if (u.pathname === '/ask') return req.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(DIR + '/ask.html', 'utf8') });
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') })
    }
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  await h.page.goto('http://louise.test/ask');
  const settle = () => h.page.evaluate(() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 60))));
  const box = () => h.page.evaluate(() => {
    const r = document.getElementById('q').getBoundingClientRect();
    return { top: Math.round(r.top), bottom: Math.round(r.bottom), scrollY: Math.round(window.scrollY),
             focused: document.activeElement === document.getElementById('q') };
  });
  // an answer below the box and the page's own head above it: the box can sit anywhere on the screen
  const place = async y => {
    await h.page.evaluate(() => { document.activeElement && document.activeElement.blur(); });
    await h.resize(W, H);
    await h.page.evaluate(y => {
      delete window.visualViewport;                       // back to the browser's own
      for (const id of ['kb-above', 'kb-below']) { const e = document.getElementById(id); if (e) e.remove(); }
      const main = document.querySelector('main') || document.body;
      const above = document.createElement('div'); above.id = 'kb-above'; above.style.height = '900px';
      const below = document.createElement('div'); below.id = 'kb-below'; below.style.height = '3000px';
      main.prepend(above); main.append(below);
      window.scrollTo(0, 0);
      const r = document.getElementById('q').getBoundingClientRect();
      window.scrollTo(0, r.top - y);
    }, y);
    await settle();
  };
  const out = { meta: await h.page.evaluate(() => document.querySelector('meta[name="viewport"]').content),
                room: await h.page.evaluate(() => QUESTION_ROOM_PX), left: H - KEYBOARD };

  // 1. the keyboard RESIZES the page: the box, low on the screen, is tapped; the screen shrinks
  await place(560);
  out.resizeBefore = await box();
  await h.page.focus('#q'); await settle();
  out.resizeFocused = await box();                         // in view with room: not moved
  await h.resize(W, H - KEYBOARD); await settle();
  out.resizeAfter = await box();

  // 2. the keyboard lies OVER the page: only the visual viewport shrinks
  await place(560);
  await h.page.focus('#q'); await settle();
  out.overFocused = await box();
  out.overMoved = await h.page.evaluate(left => {
    Object.defineProperty(window, 'visualViewport', { configurable: true, value: { offsetTop: 0, height: left } });
    return keepQuestionInView();
  }, H - KEYBOARD);
  await settle();
  out.overAfter = await box();

  // 3. the box at the screen's very top: no room above it for the menu
  await place(4);
  await h.page.focus('#q'); await settle(); await new Promise(r => setTimeout(r, 450));
  out.topAfter = await box();

  // 4. the box not focused: a keyboard for another field moves nothing
  await place(560);
  const before = await box();
  await h.resize(W, H - KEYBOARD); await settle();
  out.unfocused = { before, after: await box() };

  console.log(JSON.stringify(out));
  await h.close();
})().catch(e => { console.error(e); process.exit(1); });
