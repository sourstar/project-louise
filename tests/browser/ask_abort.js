// The Ask button is Abort while a question is being answered (operator,
// 2026-11-25: "while a query is in processing I want the Ask button to switch
// to Abort to be able to abort the query in case I change my mind or I want
// to change the query"), in a browser.
//
//   node ask_abort.js DIR   — run by tests/test_ask_abort_browser.py
//
// DIR holds ask.html and results.html, rendered by the app itself. The node
// is faked: a job "still running" answers each of its event requests with a
// status and no end, so the page keeps asking; a start (POST) can be HELD,
// to abort a question whose job the node has not named yet. One PASS/FAIL
// line per check.
const fs = require('fs');
const { launch } = require('./harness');
const DIR = process.argv[2];
const file = f => fs.readFileSync(DIR + '/' + f, 'utf8');
const N = +file('n.txt');
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(ok, what, detail) {
  console.log((ok ? 'PASS ' : 'FAIL ') + what + (detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 600) : ''));
  if (!ok) failures++;
}
const ev = (id, kind, data) => (id ? `id: ${id}\n` : '') + `event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;
const RESULT = () => ({ html: file('results.html'), search_id: 'sid' });
const RUNNING = ev(null, 'status', { state: 'running' });
const DONE = ev(9, 'end', { state: 'done', reason: '' });

const jobs = {};
const started = [];          // POST /ask/relay bodies
const eventReqs = [];        // ids, one per event request
const deleted = [];          // DELETE /ask/relay/<id>
let nextIds = [];
let held = null;             // a start the node has not answered yet
let holdStart = false;
function script(id, bodies) { jobs[id] = { bodies, served: 0 }; }

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
      const answer = () => req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: nextIds.shift() }) });
      if (holdStart) { held = answer; return; }
      return answer();
    }
    let m = u.pathname.match(/^\/ask\/relay\/([^/]+)\/events$/);
    if (m) {
      const job = jobs[m[1]];
      eventReqs.push(m[1]);
      if (!job) return req.respond({ status: 404, contentType: 'application/json', body: '{"detail":"gone"}' });
      const body = job.bodies[Math.min(job.served++, job.bodies.length - 1)];
      return req.respond({ status: 200, contentType: 'text/event-stream', body: 'retry: 50\n\n' + body });
    }
    m = u.pathname.match(/^\/ask\/relay\/([^/]+)$/);
    if (m && req.method === 'DELETE') {
      deleted.push(m[1]);
      return req.respond({ status: 200, contentType: 'application/json', body: '{"cancelled":true}' });
    }
    if (m && req.method === 'GET')
      return req.respond(jobs[m[1]]
        ? { status: 200, contentType: 'application/json', body: '{"state":"running"}' }
        : { status: 404, contentType: 'application/json', body: '{"detail":"gone"}' });
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  const until = async (fn, arg, ms = 5000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await page.evaluate(fn, arg)) return true; await sleep(30); }
    return false;
  };
  const button = () => page.evaluate(() => { const b = document.getElementById('ask-btn');
                                              return { text: b.textContent, abort: b.classList.contains('abort') }; });
  const how = v => page.evaluate(v => { document.querySelector('input[name="how"][value="' + v + '"]').checked = true; }, v);
  const type = q => page.evaluate(q => { document.getElementById('q').value = q; }, q);
  const quiet = async id => { const n = eventReqs.filter(i => i === id).length; await sleep(1300);
                              return eventReqs.filter(i => i === id).length === n; };

  await page.goto('http://harness/ask');
  check((await button()).text === 'Ask', '0. the button says Ask on a page with no question', await button());

  // 1. a search still running: the button is Abort; pressed, the node is told
  //    to cancel it, nothing more of it is asked, and the page says so
  await how('search'); await type('whales');
  nextIds = ['s1']; script('s1', [RUNNING]);
  await page.click('#ask-btn');
  check(await until(() => document.getElementById('ask-btn').textContent === 'Abort'), '1. the button says Abort while the search runs', await button());
  check(await until(() => true) && (await button()).abort, '1. and looks it (class abort)');
  await until(n => n > 0, 0); await sleep(100);
  check(eventReqs.includes('s1'), '1. the page listens to the search', eventReqs);
  await page.click('#ask-btn');
  check(await until(() => document.getElementById('ask-btn').textContent === 'Ask'), '1. pressed, the button says Ask again', await button());
  check(!(await button()).abort, '1. and looks it');
  check(deleted.join() === 's1', '1. the node is told to cancel the search (DELETE)', deleted);
  check(await page.evaluate(() => /aborted/.test(document.getElementById('results').textContent)), '1. the page says aborted',
        await page.evaluate(() => document.getElementById('results').textContent));
  check(await page.evaluate(() => document.activeElement && document.activeElement.id === 'q'), '1. the cursor is in the question');
  check(await page.evaluate(() => document.getElementById('q').value === 'whales'), '1. and the question is still there');
  check(await quiet('s1'), '1. nothing more of the search is asked of the node');
  check(await page.evaluate(() => sessionStorage.getItem('ask.relay') === null), '1. a reload would pick nothing up');
  check(started.length === 1, '1. one job was started', started.length);

  // 2. an answer cut short: the hits stay, the words written stay, marked;
  //    the search, finished, is not cancelled again
  await how('summary'); await type('whales rising');
  nextIds = ['s2', 'a2'];
  script('s2', [ev(1, 'result', RESULT()) + ev(2, 'end', { state: 'done', reason: '' })]);
  script('a2', [RUNNING + ev(1, 'delta', { text: 'The whale ' }), RUNNING]);
  await page.click('#ask-btn');
  check(await until(() => /The whale/.test(document.getElementById('answer').textContent)), '2. the answer is being written',
        await page.evaluate(() => document.getElementById('answer').textContent));
  check((await button()).text === 'Abort', '2. the button says Abort while it is', await button());
  await page.click('#ask-btn');
  check(await until(() => document.getElementById('ask-btn').textContent === 'Ask'), '2. pressed, it says Ask');
  check(deleted.join() === 's1,a2', '2. the answer is cancelled on the node, the finished search is not', deleted);
  const ans = await page.evaluate(() => document.getElementById('answer').textContent);
  check(/^The whale/.test(ans) && /aborted — the answer stops here/.test(ans), '2. what was written stays, marked as cut short', ans);
  check(await page.evaluate(n => document.querySelectorAll('#hits .hit').length === n, N), '2. the hits stay');
  check(await quiet('a2'), '2. nothing more of the answer is asked of the node');
  check(await page.evaluate(() => document.activeElement && document.activeElement.id === 'q'), '2. the cursor is in the question');

  // 3. aborted before the node named the job: it is cancelled when named,
  //    and never listened to
  await how('search'); await type('too soon');
  nextIds = ['s3']; script('s3', [RUNNING]); holdStart = true;
  await page.click('#ask-btn');
  const t0 = Date.now(); while (!held && Date.now() - t0 < 5000) await sleep(20);
  check(!!held && (await button()).text === 'Abort', '3. Abort from the press, before the node answers the start', await button());
  await page.click('#ask-btn');
  check((await button()).text === 'Ask' && deleted.length === 2, '3. pressed: Ask again, nothing to cancel yet', deleted);
  holdStart = false; held(); held = null;
  const t1 = Date.now(); while (!deleted.includes('s3') && Date.now() - t1 < 5000) await sleep(20);
  check(deleted.includes('s3'), '3. the job is cancelled when the node names it', deleted);
  await sleep(600);
  check(!eventReqs.includes('s3'), '3. and never listened to', eventReqs);
  check((await button()).text === 'Ask', '3. the button stays Ask', await button());
  check(await page.evaluate(() => /aborted/.test(document.getElementById('results').textContent)
                                   && document.getElementById('relay-note').style.display === 'none'),
        '3. the page says aborted, and nothing else');

  // 4. a question that ends by itself gives the button back, with no cancel
  await type('whales'); nextIds = ['s4'];
  script('s4', [ev(1, 'result', RESULT()) + ev(2, 'end', { state: 'done', reason: '' })]);
  const before = deleted.length;
  await page.click('#ask-btn');
  check(await until(n => document.querySelectorAll('#hits .hit').length === n
                         && document.getElementById('ask-btn').textContent === 'Ask', N), '4. a finished search: Ask again', await button());
  check(deleted.length === before, '4. and nothing was cancelled', deleted);

  // 5. a NEW question asked over one in flight (Ctrl+Enter) replaces it: the
  //    button is the new question's, and its end gives Ask back
  await type('first'); nextIds = ['s5', 's6']; script('s5', [RUNNING]);
  script('s6', [RUNNING, ev(1, 'result', RESULT()) + ev(2, 'end', { state: 'done', reason: '' })]);
  await page.click('#ask-btn');
  await until(() => document.getElementById('ask-btn').textContent === 'Abort');
  const w0 = Date.now(); while (!eventReqs.includes('s5') && Date.now() - w0 < 5000) await sleep(20);
  await type('second');
  await page.focus('#q'); await page.keyboard.down('Control'); await page.keyboard.press('Enter'); await page.keyboard.up('Control');
  check((await button()).text === 'Abort', '5. a new question over a running one: still Abort', await button());
  check(await until(() => document.getElementById('ask-btn').textContent === 'Ask', null, 8000), '5. its end gives Ask back', await button());
  const second = started[started.length - 1];
  check(second.search.query === 'second' && second.replaces.join() === 's5', '5. the node was told it replaces the first', second);

  // 6. a reload in mid-question: the page picks it up, and the button aborts it
  await type('picked up'); nextIds = ['s7']; script('s7', [RUNNING]);
  await page.click('#ask-btn');
  const w1 = Date.now(); while (!eventReqs.includes('s7') && Date.now() - w1 < 5000) await sleep(20);
  await page.reload();
  check(await until(() => document.getElementById('ask-btn').textContent === 'Abort'), '6. after a reload the button says Abort', await button());
  await page.click('#ask-btn');
  const w2 = Date.now(); while (!deleted.includes('s7') && Date.now() - w2 < 5000) await sleep(20);
  check(deleted.includes('s7') && (await button()).text === 'Ask', '6. and aborts the question picked up', deleted);
  check(await quiet('s7'), '6. nothing more of it is asked');

  check(errors.length === 0, 'no page errors', errors);
  await h.close();
  console.log(failures ? `${failures} FAILED` : 'ALL PASSED');
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
