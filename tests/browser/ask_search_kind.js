// The search's kind on the Ask page (docs/notes/fact-fiction-search.md;
// operator, 2026-11-27): the page asks auto, the chip's tap runs the same
// question again with the kind tapped — the search and the answer alike —
// and a new question is auto again.
//
//   node ask_search_kind.js DIR   — run by tests/test_ask_search_kind_browser.py
const fs = require('fs');
const { launch } = require('./harness');
const DIR = process.argv[2];
const file = f => fs.readFileSync(DIR + '/' + f, 'utf8');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(ok, what, detail) {
  console.log((ok ? 'PASS ' : 'FAIL ') + what + (detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 600) : ''));
  if (!ok) failures++;
}
const ev = (id, kind, data) => `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;
const started = [];
let n = 0;
(async () => {
  const h = await launch({ width: 1200, height: 900 });
  const page = h.page;
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  h.route(req => {
    const u = new URL(req.url);
    if (u.pathname === '/ask') return req.respond({ status: 200, contentType: 'text/html', body: file('ask.html') });
    if (u.pathname === '/ask/relay' && req.method === 'POST') {
      started.push(JSON.parse(req.postData));
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: 'j' + (++n) }) });
    }
    const m = u.pathname.match(/^\/ask\/relay\/([^/]+)\/events$/);
    if (m) {
      const body = m[1] === 'j' + n && started[n - 1].kind === 'answer'
        ? ev(1, 'delta', { text: 'An answer.' }) + ev(2, 'end', { state: 'done', reason: '' })
        : ev(1, 'result', { html: file('results.html'), search_id: 'sid' }) + ev(2, 'end', { state: 'done', reason: '' });
      return req.respond({ status: 200, contentType: 'text/event-stream', body: 'retry: 50\n\n' + body });
    }
    if (u.pathname.match(/^\/ask\/relay\/[^/]+$/))
      return req.respond({ status: 200, contentType: 'application/json', body: '{"state":"done"}' });
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  const until = async (fn, ms = 5000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await page.evaluate(fn)) return true; await sleep(30); }
    return false;
  };
  await page.goto('http://harness/ask');
  await page.evaluate(async () => { document.getElementById('q').value = 'Napoleon in Russia'; await search(); });
  check(started[0].search.kind === 'auto', '1. a question is searched auto', started[0].search);
  check(await until(() => !!document.querySelector('#report #search-kind')), '1. the chip is above the answer box');
  await page.evaluate(() => document.querySelector('#report .kind-tap[data-kind="literature"]').click());
  for (let t = 0; t < 150 && started.length < 2; t++) await sleep(30);
  check(started.length === 2, '2. a tap runs the question again', started.length);
  check(started[1] && started[1].search.kind === 'literature' && started[1].search.query === 'Napoleon in Russia',
        '2. with the kind tapped', started[1] && started[1].search);
  await until(() => !!document.querySelector('#report #search-kind'));
  await page.evaluate(async () => { await ask(); });
  const answer = started.find(b => b.kind === 'answer');
  check(answer && answer.answer.kind === 'literature', '3. the answer is searched with the same kind', answer && answer.answer);
  await page.evaluate(async () => { document.getElementById('q').value = 'whales'; await search(); });
  const last = started[started.length - 1];
  check(last.kind === 'search' && last.search.kind === 'auto', '4. a new question is auto again', last.search);
  check(errors.length === 0, 'no page errors', errors);
  await h.close();
  console.log(failures ? `${failures} FAILED` : 'ALL PASSED');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.log('FAIL crashed — ' + e.stack); process.exit(1); });
