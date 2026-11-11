// The Ask page's "save pdf" (operator, 2026-11-11): what the page sends to
// POST /ask/pdf is what the reader sees — the question as asked, the answer
// as shown, each entry's title and passage as shown with the published ones
// where the page translated them, the paper the Settings name.
// argv: DIR holding ask.html and results.html, the app's static dir.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC] = process.argv.slice(2);

(async () => {
  const h = await launch({ width: 1200, height: 900 });
  const posted = [];
  h.route(req => {
    const u = new URL(req.url);
    if (u.pathname === '/ask') return req.respond({ status: 200, contentType: 'text/html', body: fs.readFileSync(DIR + '/ask.html', 'utf8') });
    if (u.pathname === '/ask/pdf') { posted.push(req.postData); return req.respond({ status: 200, contentType: 'application/pdf', body: '%PDF-1.7 stub' }); }
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') });
    }
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  await h.page.goto('http://louise.test/ask');
  const results = fs.readFileSync(DIR + '/results.html', 'utf8');
  const before = await h.page.evaluate(html => {
    document.getElementById('results').innerHTML = html;
    // the second entry's title and passage as the page shows them translated
    const hit = document.querySelectorAll('#hits .hit')[1];
    const t = hit.querySelector('.doc-title'), s = hit.querySelector('.snip');
    t.dataset.orig = t.textContent; t.textContent = 'Lighthouse of Alexandria'; t.dataset.tl = 'en';
    s.dataset.orig = s.textContent; s.textContent = 'Commissioned by Ptolemy I.'; s.dataset.tl = 'en';
    relayState = { q: 'Who built the lighthouse?' };
    document.getElementById('q').value = 'something typed since';
    const hidden = document.getElementById('save-pdf').hidden;
    document.getElementById('answer-pane').style.display = 'block';
    setAnswer('Built around 280 BC [1][2].');
    finishAnswer(document.getElementById('answer'), 'en');
    return { hiddenBefore: hidden, hiddenAfter: document.getElementById('save-pdf').hidden };
  }, results);
  // the reader's choice in Settings (a closed drawer: set as a change would)
  await h.page.evaluate(() => { const p = document.getElementById('paper'); p.value = 'letter';
                                p.dispatchEvent(new Event('change')); });
  await h.page.click('#save-pdf');
  for (let i = 0; i < 100 && !posted.length; i++) await new Promise(r => setTimeout(r, 50));
  await new Promise(r => setTimeout(r, 200));
  const after = await h.page.evaluate(() => ({
    label: document.querySelector('#save-pdf .pdf-label').textContent,
    disabled: document.getElementById('save-pdf').disabled,
    note: document.getElementById('relay-note').textContent,
    stored: localStorage.getItem('louise-paper'),
  }));
  console.log(JSON.stringify({ ...before, ...after, posted: posted.map(p => JSON.parse(p)) }));
  await h.close();
})().catch(e => { console.error(e); process.exit(1); });
