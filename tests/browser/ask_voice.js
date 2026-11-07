// The Ask page's voice (app/voice.py, app/static/voice.js; operator,
// 2026-11-07), in a browser with Chromium's fake microphone fed a WAV: the
// microphone records, stops by itself once the speech is over, posts a WAV
// of 16-bit samples at 16 kHz, mono, and fills the question box without
// asking; the speaker asks /v1/audio/speech for each sentence of the answer,
// in order, and stops when tapped; no button for a voice the node does not
// offer; everything inside a phone's width.
//
//   node ask_voice.js DIR STATIC   — run by tests/test_ask_voice_browser.py
//
// DIR holds ask.html (voice on), ask_off.html (none set), results.html,
// reply.wav (what the fake node speaks) and voice.wav (what the fake
// microphone hears: a tone, then silence, looped). One PASS/FAIL line per check.
const fs = require('fs');
const { launch } = require('./harness');
const [DIR, STATIC] = process.argv.slice(2);
const file = (f, enc = 'utf8') => fs.readFileSync(DIR + '/' + f, enc);
const sleep = ms => new Promise(r => setTimeout(r, ms));
let failures = 0;
function check(ok, what, detail) {
  console.log((ok ? 'PASS ' : 'FAIL ') + what + (detail !== undefined ? ' — ' + JSON.stringify(detail).slice(0, 600) : ''));
  if (!ok) failures++;
}
const ev = (id, kind, data) => `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;
const SENTENCES = ['Dante Alighieri wrote the Divine Comedy early in the fourteenth century [1].',
                   'It is written in Tuscan, the language that became Italian [2].',
                   'Petrarch and Boccaccio followed him.'];
const ANSWER = SENTENCES.join(' ');
const TEXT = 'Who wrote the Divine Comedy?';

(async () => {
  const h = await launch({ width: 1200, height: 900, args: [
    '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${DIR}/voice.wav`, '--autoplay-policy=no-user-gesture-required'] });
  const page = h.page;
  const errors = [], posted = { relay: [], stt: 0, speech: [] };
  let speechDelay = 0;
  page.on('pageerror', e => errors.push(e.message));
  h.route(async req => {
    const u = new URL(req.url);
    if (u.pathname === '/ask')
      return req.respond({ status: 200, contentType: 'text/html', body: file(u.searchParams.get('voice') === 'off' ? 'ask_off.html' : 'ask.html') });
    if (u.pathname.startsWith('/static/')) {
      const f = STATIC + '/' + u.pathname.slice(8);
      if (fs.existsSync(f)) return req.respond({ status: 200, contentType: f.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(f, 'utf8') });
      return req.respond({ status: 404, contentType: 'text/plain', body: '' });
    }
    if (u.pathname === '/v1/audio/transcriptions' && req.method === 'POST') {
      posted.stt++;
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ text: ' ' + TEXT }) });
    }
    if (u.pathname === '/v1/audio/speech' && req.method === 'POST') {
      posted.speech.push(JSON.parse(req.postData));
      if (speechDelay) await sleep(speechDelay);
      return req.respond({ status: 200, contentType: 'audio/wav', body: file('reply.wav', null) });
    }
    if (u.pathname === '/ask/relay' && req.method === 'POST') {
      const b = JSON.parse(req.postData); posted.relay.push(b);
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: b.kind === 'search' ? 's1' : 'a1' }) });
    }
    if (u.pathname === '/ask/relay/s1/events')
      return req.respond({ status: 200, contentType: 'text/event-stream',
        body: ev(1, 'result', { html: file('results.html'), search_id: 'sid' }) + ev(2, 'end', { state: 'done', reason: '' }) });
    if (u.pathname === '/ask/relay/a1/events')
      return req.respond({ status: 200, contentType: 'text/event-stream',
        body: ev(1, 'delta', { text: ANSWER.slice(0, 40) }) + ev(2, 'delta', { text: ANSWER.slice(40) }) + ev(3, 'end', { state: 'done', reason: '' }) });
    return req.respond({ status: 200, contentType: 'text/plain', body: '' });
  });
  const until = async (fn, ms = 8000, arg) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await page.evaluate(fn, arg)) return true; await sleep(40); }
    return false;
  };
  // what the page posts, read in the page: the very bytes of the WAV
  const spy = () => page.evaluate(() => {
    window.__posts = [];
    const f = window.fetch;
    window.fetch = async (url, o) => {
      if (String(url).includes('/v1/audio/transcriptions')) {
        const fields = {};
        for (const [k, v] of o.body.entries()) fields[k] = typeof v === 'string' ? v : null;
        const bytes = new Uint8Array(await o.body.get('file').arrayBuffer());
        __posts.push({ fields, bytes: Array.from(bytes), type: o.body.get('file').type });
      }
      return f(url, o);
    };
  });
  // the WAV as the node's VAD reads it (app/transcribe.py pcm16_mono_16k)
  const wav = b => {
    const v = new DataView(Uint8Array.from(b).buffer), s = (o, n) => String.fromCharCode(...b.slice(o, o + n));
    const n = v.getUint32(40, true) / 2;
    let peak = 0; for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(v.getInt16(44 + 2 * i, true)));
    return { riff: s(0, 4), wave: s(8, 4), fmt: s(12, 4), pcm: v.getUint16(20, true), channels: v.getUint16(22, true),
             rate: v.getUint32(24, true), bits: v.getUint16(34, true), data: s(36, 4), size: v.getUint32(4, true),
             samples: n, length: b.length, peak };
  };

  await page.goto('http://localhost/ask');      // a secure context: the browser opens a microphone here
  await spy();

  // 1. the buttons of the voices the node offers
  check(await page.evaluate(() => !document.getElementById('mic').hidden), '1. the microphone is shown');
  check(await page.evaluate(() => document.getElementById('speak').hidden), '1. the speaker waits for an answer');

  // 2. tap, speak, quiet: it stops by itself, posts the WAV, fills the box
  await page.click('#mic');
  check(await until(() => document.getElementById('mic').classList.contains('rec')
                       && /recording/.test(document.getElementById('mic-status').textContent), 4000),
        '2. recording is shown plainly', await page.evaluate(() => document.getElementById('mic-status').textContent));
  check(await until(() => __posts.length === 1, 12000), '2. it stops by itself after the speech, and posts');
  check(await until(() => document.getElementById('q').value === 'Who wrote the Divine Comedy?', 4000),
        '2. the question box holds the text', await page.evaluate(() => document.getElementById('q').value));
  const p1 = (await page.evaluate(() => __posts))[0];
  const w1 = wav(p1.bytes);
  check(w1.riff === 'RIFF' && w1.wave === 'WAVE' && w1.fmt === 'fmt ' && w1.data === 'data'
        && w1.size === w1.length - 8 && w1.length === 44 + 2 * w1.samples, '2. a RIFF WAVE file, whole', w1);
  check(w1.pcm === 1 && w1.channels === 1 && w1.rate === 16000 && w1.bits === 16, '2. 16-bit PCM, 16 kHz, mono', w1);
  check(w1.samples > 16000 * 1.5 && w1.samples < 16000 * 12, '2. the recording, speech and the quiet after it', w1.samples);
  check(w1.peak > 3000, '2. the speech is in it', w1.peak);
  check(p1.type === 'audio/wav' && p1.fields.response_format === 'json' && !('model' in p1.fields)
        && !('language' in p1.fields), '2. no model named, and no language while results show in their own', p1.fields);
  check(posted.relay.length === 0, '2. nothing asked: the reader asks', posted.relay.length);
  check(await page.evaluate(() => document.getElementById('mic-status').hidden && !document.getElementById('mic').classList.contains('rec')),
        '2. the microphone is idle again');

  // 3. tap and tap again: stopped by hand, in the page's answer language, the text added
  await h.select('#lang', 'it');
  await page.click('#mic');
  await until(() => document.getElementById('mic').classList.contains('rec'), 4000);
  await sleep(700);
  await page.click('#mic');
  check(await until(() => __posts.length === 2, 4000), '3. a tap stops it, and it posts');
  const p2 = (await page.evaluate(() => __posts))[1];
  check(p2.fields.language === 'it', '3. the page\'s answer language goes with it', p2.fields);
  check(wav(p2.bytes).samples < 16000 * 1.5, '3. as long as it was held', wav(p2.bytes).samples);
  check(await until(() => document.getElementById('q').value === 'Who wrote the Divine Comedy? Who wrote the Divine Comedy?', 4000),
        '3. the text is added to what the box held', await page.evaluate(() => document.getElementById('q').value));

  // 4. an answer: the speaker reads it sentence by sentence, in order
  await page.evaluate(async () => { document.getElementById('q').value = 'Who wrote the Divine Comedy?'; await ask(); });
  check(await until(() => !document.getElementById('speak').hidden, 4000), '4. the speaker is shown once the answer is there');
  await page.click('#speak');
  check(await until(() => document.getElementById('speak').classList.contains('on'), 2000), '4. reading is shown');
  check(await until(() => !document.getElementById('speak').classList.contains('on'), 15000), '4. and ends with the answer');
  check(JSON.stringify(posted.speech.map(b => b.input)) === JSON.stringify(SENTENCES),
        '4. one request per sentence, in order', posted.speech.map(b => b.input));
  check(posted.speech.every(b => b.language === 'it' && !('model' in b) && !('voice' in b)),
        '4. the node\'s model and voice: the page names neither', posted.speech);

  // 5. tapped again while reading: it stops, and asks nothing more
  posted.speech.length = 0; speechDelay = 400;
  await page.click('#speak');
  check(await until(() => document.getElementById('speak').classList.contains('on'), 2000), '5. reading again');
  await sleep(150);
  await page.click('#speak');
  await sleep(1500);
  check(posted.speech.length <= 2, '5. a tap stops it: no request after', posted.speech.length);
  check(await page.evaluate(() => !document.getElementById('speak').classList.contains('on')), '5. and says so');
  speechDelay = 0;

  // 6. a new question takes the speaker away
  await page.evaluate(async () => { await search(); });
  check(await page.evaluate(() => document.getElementById('speak').hidden), '6. a new search: nothing to read');

  // 7. a phone's width: the box, the microphone and the speaker inside the screen
  await h.resize(360, 780);
  await page.evaluate(async () => { await ask(); });
  await until(() => !document.getElementById('speak').hidden, 4000);
  const m = await page.evaluate(() => {
    const r = id => { const b = document.getElementById(id).getBoundingClientRect(); return { left: b.left, right: b.right, top: b.top, width: b.width }; };
    return { q: r('q'), mic: r('mic'), speak: r('speak'), scroll: document.documentElement.scrollWidth, width: window.innerWidth };
  });
  check(m.mic.right <= 360 && m.speak.right <= 360 && m.scroll <= 360, '7. nothing past a 360 px screen', m);
  check(m.q.right <= m.mic.left && Math.abs(m.q.top - m.mic.top) < 2 && m.q.width > 200,
        '7. the microphone beside the box, the box still wide', m);
  check(m.mic.width >= 40, '7. a finger-sized microphone', m.mic.width);

  // 8. a node that offers no voice: no button, no script
  const scripts = [];
  page.on('request', r => { if (/voice\.js/.test(r.url())) scripts.push(r.url()); });
  await page.goto('http://localhost/ask?voice=off');
  check(await page.evaluate(() => !document.getElementById('mic') && !document.getElementById('speak') && !window.louiseVoice),
        '8. no voice set: no microphone, no speaker');
  check(scripts.length === 0, '8. and its script is not loaded', scripts);

  // 9. a page the browser would not open a microphone for (plain http, not localhost): no microphone
  await page.goto('http://louise.test/ask');
  check(await page.evaluate(() => !document.getElementById('mic') && !!document.getElementById('speak')),
        '9. not a secure context: the microphone is not shown, the speaker is');

  check(errors.length === 0, 'no page errors', errors);
  console.log(failures ? `${failures} FAILED` : 'ALL PASSED');
  await h.close();
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
