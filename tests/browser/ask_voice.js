// The Ask page's voice (app/voice.py, app/static/voice.js; operator,
// 2026-11-07), in a browser with Chromium's fake microphone fed a WAV: the
// microphone records, stops by itself once the speech is over, posts a WAV
// of 16-bit samples at 16 kHz, mono, and fills the question box without
// asking; the speaker asks /v1/audio/speech for each sentence of the answer,
// in order, and stops when tapped; no button for a voice the node does not
// offer; everything inside a phone's width. WHILE THE ANSWER STREAMS
// (operator, 2026-11-08; the stream written from here, a delta at a time):
// each sentence is asked for once the stream has completed it — the first
// before the stream ends — none twice, none half, played in order; a tap
// cancels what is in flight; a typed question waits for the speaker, a
// spoken one is read by itself; a refused playback is said, and a tap starts
// it; a translated answer is read in its own language from its first sentence.
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
const FRENCH = ['Dante Alighieri a écrit la Divine Comédie au début du quatorzième siècle [1].',
                'Elle est écrite en toscan, la langue devenue l\'italien [2].',
                'Pétrarque et Boccace l\'ont suivi.'];
// what the fake node says for a sentence: silence whose length names it
const KNOWN = [...SENTENCES, ...FRENCH];
const wavOf = n => {
  const b = Buffer.alloc(44 + 2 * n);
  b.write('RIFF', 0); b.writeUInt32LE(36 + 2 * n, 4); b.write('WAVE', 8); b.write('fmt ', 12);
  b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(16000, 24);
  b.writeUInt32LE(32000, 28); b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(2 * n, 40);
  return b;
};
const replyFor = input => wavOf(4000 + 10 * (KNOWN.indexOf(input) + 1));
const sentenceOf = size => KNOWN[(size - 44 - 8000) / 20 - 1];

(async () => {
  const h = await launch({ width: 1200, height: 900, args: [
    '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
    `--use-file-for-fake-audio-capture=${DIR}/voice.wav`, '--autoplay-policy=no-user-gesture-required'] });
  const page = h.page;
  const errors = [], posted = { relay: [], stt: 0, speech: [] };
  // speechDelay: every sentence's wait; slow: a sentence's own; answerJob:
  // 'live' is the answer the test writes into the page's stream itself
  let speechDelay = 0, slow = {}, answerJob = 'a1';
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
      const b = JSON.parse(req.postData); posted.speech.push(b);
      b.__reading = (req.headers || {})['louise-reading'] || null;
      const wait = b.input in slow ? slow[b.input] : speechDelay;
      if (wait) await sleep(wait);
      return req.respond({ status: 200, contentType: 'audio/wav',
                           body: KNOWN.includes(b.input) && answerJob === 'live' ? replyFor(b.input) : file('reply.wav', null) });
    }
    if (u.pathname === '/ask/relay' && req.method === 'POST') {
      const b = JSON.parse(req.postData); posted.relay.push(b);
      return req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: b.kind === 'search' ? 's1' : answerJob }) });
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

  // ---- while the answer streams (operator, 2026-11-08) ----------------------
  // In the page: the answer's stream is written from here (__live.emit); each
  // speech request is recorded with whether the stream had ended and how it
  // ended (answered, aborted); each sound played, by the sentence it speaks.
  await page.evaluate(() => {
    const Real = window.EventSource;
    window.__lives = 0; window.__live = null;
    window.EventSource = function (url) {
      if (!/\/ask\/relay\/live\//.test(url)) return new Real(url);
      const ls = {}; let id = 0;
      const es = { readyState: 1, onerror: null, close() { this.readyState = 2; },
                   addEventListener(k, f) { (ls[k] = ls[k] || []).push(f); },
                   emit(k, d) { if (this.readyState === 2) return;
                                id++; for (const f of ls[k] || []) f({ data: JSON.stringify(d), lastEventId: String(id) }); } };
      window.__live = es; window.__lives++; return es;
    };
    Object.assign(window.EventSource, { CONNECTING: 0, OPEN: 1, CLOSED: 2 });
    window.__speech = [];
    const f = window.fetch;
    window.fetch = (url, o) => {
      const p = f(url, o);
      if (String(url).includes('/v1/audio/speech')) {
        const b = JSON.parse(o.body), rec = { input: b.input, language: b.language, state: 'pending',
                                              ended: !window.__live || window.__live.readyState === 2 };
        __speech.push(rec);
        p.then(() => { rec.state = 'answered'; }, e => { rec.state = e && e.name === 'AbortError' ? 'aborted' : 'failed'; });
      }
      return p;
    };
    window.__sizes = {}; window.__played = [];
    const mk = URL.createObjectURL;
    URL.createObjectURL = b => { const u = mk(b); __sizes[u] = b.size; return u; };
    window.__play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      if (String(this.src).startsWith('blob:')) __played.push(__sizes[this.src]);
      return __play.call(this);
    };
    window.__toasts = [];
    const ts = toast; toast = function (m) { __toasts.push(m); return ts(m); };
  });
  answerJob = 'live';
  const emit = (k, d) => page.evaluate(o => __live.emit(o.k, o.d), { k, d });
  const reset = () => { posted.speech.length = 0; return page.evaluate(() => { __speech.length = 0; __played.length = 0; __toasts.length = 0; }); };
  const inputs = () => page.evaluate(() => __speech.map(s => s.input));
  const played = async () => (await page.evaluate(() => __played)).map(sentenceOf);
  const on = () => page.evaluate(() => document.getElementById('speak').classList.contains('on'));
  // a question asked; resolves once its answer's stream is open
  const asked = async how => {
    const n = await page.evaluate(() => __lives);
    await how();
    return until(k => __lives > k, 8000, n);
  };

  // 8. a text cut as it streams, at any split, is cut as it is whole
  const cuts = await page.evaluate(texts => {
    const bad = [];
    for (const t of texts) for (let n = 0; n < 300; n++) {
      const r = { at: 0, off: 0, lang: 'it' }, got = [];
      for (let i = 0; i < t.length;) { i = Math.min(t.length, i + 1 + Math.floor(Math.random() * 14)); got.push(...louiseVoice.cut(r, t.slice(0, i), false)); }
      got.push(...louiseVoice.cut(r, t, true), ...louiseVoice.cut(r, t, true));
      if (JSON.stringify(got) !== JSON.stringify(louiseVoice.sentences(t, 'it'))) bad.push(got);
    }
    return bad.slice(0, 2);
  }, [ANSWER, 'Breve. Anche breve. Poi una frase molto più lunga, abbastanza lunga.\n\nUn titolo\n'
              + 'L\'art. 3 del D.Lgs. 196/2003 lo dice [4]. Sì.\nE questa è la fine, davvero. Ok.']);
  check(cuts.length === 0, '8. a text cut as it streams is cut in the pieces it has whole', cuts);

  // 8. a typed question, its answer streamed: silent until the speaker is
  //    tapped; then each sentence asked for as soon as the stream completes it
  await reset();
  slow = { [SENTENCES[0]]: 700 };          // the first one's speech comes back last
  check(await asked(() => page.evaluate(() => { document.getElementById('q').value = 'Who wrote it?'; ask(); })),
        '8. the answer streams');
  await emit('delta', { text: SENTENCES[0] + ' It is wri' });
  check(await until(() => !document.getElementById('speak').hidden, 2000), '8. the speaker is there while the answer is written');
  await sleep(600);
  check(posted.speech.length === 0 && !(await on()), '8. a typed question: nothing is read by itself', posted.speech);
  await page.click('#speak');
  check(await until(() => document.getElementById('speak').classList.contains('on'), 2000), '8. a tap: reading');
  await sleep(500);
  check(posted.speech.length === 0, '8. a first sentence the next could still join is not asked for yet', posted.speech);
  await emit('delta', { text: 'tten in Tuscan, the language' });
  check(await until(() => __speech.length >= 1, 3000), '8. the first sentence is asked for once the next has begun');
  let sp = await page.evaluate(() => __speech);
  check(sp.length === 1 && sp[0].input === SENTENCES[0] && !sp[0].ended && sp[0].language === 'it',
        '8. exactly the first sentence, whole, before the stream ends', sp);
  await emit('delta', { text: ' that became Italian [2]. Petrarch and Boccaccio fol' });
  check(await until(() => __speech.length >= 2, 3000), '8. the second as soon as it is complete, the first still being made');
  const ph = await page.evaluate(() => {
    const b = document.getElementById('speak').getBoundingClientRect();
    return { right: b.right, scroll: document.documentElement.scrollWidth, label: document.getElementById('speak').textContent.trim() };
  });
  check(ph.right <= 360 && ph.scroll <= 360 && ph.label === 'stop', '8. reading, on a phone: the speaker inside the screen', ph);
  await emit('delta', { text: 'lowed him.' });
  await sleep(500);
  check(posted.speech.length === 2, '8. the last sentence waits for the stream\'s end: it could still grow', posted.speech.map(b => b.input));
  await emit('end', { state: 'done', reason: '' });
  check(await until(() => !document.getElementById('speak').classList.contains('on'), 15000), '8. the reading ends with the answer');
  sp = await page.evaluate(() => __speech);
  check(JSON.stringify(sp.map(s => s.input)) === JSON.stringify(SENTENCES) && sp.every(s => s.state === 'answered'),
        '8. every sentence asked for once, whole, in order', sp);
  check(JSON.stringify(await played()) === JSON.stringify(SENTENCES), '8. played in order, the first though its speech came last', await played());
  const order = posted.speech.map(b => b.__reading);
  check(order.every((h, i) => h && h.replace(/\/last$/, '').endsWith('/' + i) && h.split('/')[0] === order[0].split('/')[0])
        && order[order.length - 1].endsWith('/last') && order.slice(0, -1).every(h => !h.endsWith('/last')),
        '8. each sentence names its reading and its place (the node keeps the order: speech.Turns)', order);
  slow = {};

  // 9. tapped while the answer is still written: the fetches in flight are
  //    cancelled, and nothing more is asked for or played
  await reset(); speechDelay = 1500;
  await asked(() => page.evaluate(() => { ask(); }));
  await emit('delta', { text: SENTENCES.slice(0, 2).join(' ') + ' Petrarch and Boccaccio fol' });
  await page.click('#speak');
  check(await until(() => __speech.length === 2, 3000), '9. two sentences being made', await inputs());
  await page.click('#speak');
  check(await until(() => __speech.every(s => s.state === 'aborted'), 3000), '9. a tap cancels them', await page.evaluate(() => __speech));
  await emit('delta', { text: 'lowed him.' });
  await emit('end', { state: 'done', reason: '' });
  await sleep(2000);
  // operator, 2026-11-09: "keep the buffering available even when I stop/start
  // the speech" — the answer still arriving is made into speech, never played
  check(posted.speech.length > 2 && (await played()).length === 0 && !(await on()),
        '9. nothing is played, and the rest of the answer is still made into speech (buffered)',
        { posted: posted.speech.length, played: await played() });

  // 10. a new question stops the reading too
  await reset();
  await asked(() => page.evaluate(() => { ask(); }));
  await emit('delta', { text: SENTENCES[0] + ' It is written in Tuscan, the' });
  await page.click('#speak');
  check(await until(() => __speech.length === 1, 3000), '10. reading');
  await page.evaluate(() => { search(); });
  check(await until(() => __speech.every(s => s.state === 'aborted') && !document.getElementById('speak').classList.contains('on'), 3000),
        '10. a new question: what was being made is cancelled', await page.evaluate(() => __speech));
  await sleep(1800);
  check(posted.speech.length === 1 && (await played()).length === 0, '10. and nothing more is read', posted.speech.length);
  speechDelay = 0;

  // 11. a question the microphone wrote, then Ask: the answer is read by itself
  await reset();
  await page.evaluate(() => { document.getElementById('q').value = ''; });
  await page.click('#mic');
  check(await until(t => document.getElementById('q').value === t, 12000, TEXT), '11. the microphone writes the question');
  check(await asked(() => page.click('button.ask')), '11. Ask');
  await emit('delta', { text: SENTENCES[0] + ' It is written in Tuscan, the' });
  check(await until(() => __speech.length === 1, 3000), '11. the first sentence is asked for by itself, before the stream ends',
        await page.evaluate(() => __speech));
  check(await on(), '11. and reading is shown');
  await emit('delta', { text: ' language that became Italian [2]. ' + SENTENCES[2] });
  await emit('end', { state: 'done', reason: '' });
  check(await until(() => !document.getElementById('speak').classList.contains('on'), 15000), '11. read to the end');
  check(JSON.stringify(await inputs()) === JSON.stringify(SENTENCES) && JSON.stringify(await played()) === JSON.stringify(SENTENCES),
        '11. every sentence, in order', { asked: await inputs(), played: await played() });

  // 12. the browser refuses to play: the speaker stays, one line says so,
  //     nothing more is fetched, and a tap starts the reading
  await reset();
  await page.evaluate(() => {
    HTMLMediaElement.prototype.play = function () {
      if (String(this.src).startsWith('blob:')) return Promise.reject(new DOMException('no gesture', 'NotAllowedError'));
      return __play.call(this);
    };
  });
  await asked(() => page.evaluate(() => { ask(); }));     // the same spoken question, asked again
  await emit('delta', { text: SENTENCES[0] + ' It is written in Tuscan, the' });
  check(await until(() => __toasts.length > 0, 4000), '12. refused: it is said', await page.evaluate(() => __toasts));
  const t12 = await page.evaluate(() => __toasts);
  check(t12.length === 1 && /did not let the page play sound — tap the speaker/.test(t12[0]), '12. in one plain line', t12);
  check(await page.evaluate(() => !document.getElementById('speak').hidden && !document.getElementById('speak').classList.contains('on')),
        '12. the speaker is shown, ready for a tap');
  const n12 = posted.speech.length;
  await emit('delta', { text: ' language that became Italian [2]. ' + SENTENCES[2] });
  await emit('end', { state: 'done', reason: '' });
  await sleep(600);
  check(posted.speech.length > n12 && (await played()).length === 0,
        '12. meanwhile the rest is made into speech (buffered), and nothing is played',
        posted.speech.map(b => b.input));
  await page.evaluate(() => {
    HTMLMediaElement.prototype.play = function () {
      if (String(this.src).startsWith('blob:')) __played.push(__sizes[this.src]);
      return __play.call(this);
    };
  });
  await reset();
  await page.click('#speak');
  check(await until(() => !document.getElementById('speak').classList.contains('on'), 15000) && JSON.stringify(await played()) === JSON.stringify(SENTENCES),
        '12. a tap reads it, from the first sentence', await played());

  // 13. the answer shown again while it is read: the same text repeats
  //     nothing; a translation is read in its language, from its first sentence
  await reset(); speechDelay = 300;
  await asked(() => page.evaluate(() => { document.getElementById('q').value = 'Who wrote it?'; ask(); }));
  await emit('delta', { text: ANSWER });
  await emit('end', { state: 'done', reason: '' });
  await page.click('#speak');
  check(await until(() => __speech.length >= 1, 3000), '13. reading');
  await page.evaluate(() => showAnswer(answerRaw, answerLang));
  await sleep(150);
  const k = await page.evaluate(fr => { const k = __speech.length; showAnswer(fr, 'fr'); return k; }, FRENCH.join(' '));
  check(await until(() => !document.getElementById('speak').classList.contains('on'), 15000), '13. read to the end');
  sp = await page.evaluate(() => __speech);
  const before = sp.slice(0, k).map(s => s.input);
  check(before.length >= 1 && new Set(before).size === before.length && before.every((s, i) => s === SENTENCES[i]),
        '13. the same text shown again: nothing asked twice', before);
  check(JSON.stringify(sp.slice(k).map(s => s.input)) === JSON.stringify(FRENCH) && sp.slice(k).every(s => s.language === 'fr'),
        '13. the translation, in French, from its first sentence', sp.slice(k));
  const pl = await played();
  check(pl.slice(pl.indexOf(FRENCH[0])).join('|') === FRENCH.join('|') && pl.every(s => FRENCH.includes(s) || SENTENCES.includes(s)),
        '13. played: the translation, after what was read of the original', pl);
  speechDelay = 0; answerJob = 'a1';

  // 18. a production-shaped answer (München's measurement, 2026-11-08: every
  //     sentence cut at the stream's end): Italian, citations, paragraphs,
  //     ~90 deltas of a few words; tapped at the first word
  await reset(); answerJob = 'live';
  await asked(() => page.evaluate(() => { document.getElementById('q').value = 'Chi scrisse la Commedia?'; ask(); }));
  const LONG = 'La Divina Commedia fu scritta da Dante Alighieri tra il 1304 e il 1321 [1]. '
    + 'È considerata il capolavoro della letteratura italiana [2][3].\n\n'
    + 'Il poema si divide in tre cantiche: Inferno, Purgatorio e Paradiso [1]. '
    + 'Ognuna conta trentatré canti, più il proemio [4]. Dante vi narra un viaggio nell\'aldilà [2].\n'
    + 'G. Boccaccio la chiamò divina, e il nome rimase nelle edizioni successive [5].';
  const words = LONG.match(/\S+\s*/g);
  await emit('delta', { text: words[0] });
  await until(() => !document.getElementById('speak').hidden, 2000);
  await page.click('#speak');
  for (let i = 1; i < words.length; i++) { await emit('delta', { text: words[i] }); await sleep(25); }
  const lastWordAt = await page.evaluate(() => louiseVoice.timeline().last_word);
  await emit('end', { state: 'done', reason: '' });
  await until(() => __speech.length >= 4, 8000);
  const t18 = await page.evaluate(() => louiseVoice.timeline());
  // a long first sentence goes in two, cut at its last clause mark under 90
  const LONGFIRST = 'Il poema, composto in terzine incatenate di endecasillabi, si divide in tre cantiche e cento canti complessivi [1].';
  const pieces = await page.evaluate(t => { const r = []; return louiseVoice.firstPieces(t); }, LONGFIRST);
  check(pieces.length === 2 && pieces[0] === 'Il poema, composto in terzine incatenate di endecasillabi,' && pieces[0].length <= 90
        && pieces.join(' ') === LONGFIRST, '18. a long first sentence is read in two, at a clause mark', pieces);
  check(Array.isArray(t18.started) && t18.started.length === 1, '18. the reading\'s start is kept', t18.started);
  check(Array.isArray(t18.taps) && t18.taps.length === 1 && t18.taps[0] < lastWordAt && t18.started[0] >= t18.taps[0],
        '18. the tap is kept, beside the start it made', { taps: t18.taps, started: t18.started, lastWordAt });
  check(t18.sentences.length >= 4 && t18.sentences[0].cut < lastWordAt - 500,
        '18. a production-shaped stream is cut while it is written, not at its end',
        { lastWordAt, t18, text: await page.evaluate(() => document.getElementById('answer').textContent),
          lives: await page.evaluate(() => __lives), on: await on() });
  answerJob = 'a1';

  // 14. a node that offers no voice: no button, no script
  const scripts = [];
  page.on('request', r => { if (/voice\.js/.test(r.url())) scripts.push(r.url()); });
  await page.goto('http://localhost/ask?voice=off');
  check(await page.evaluate(() => !document.getElementById('mic') && !document.getElementById('speak') && !window.louiseVoice),
        '14. no voice set: no microphone, no speaker');
  check(scripts.length === 0, '14. and its script is not loaded', scripts);

  // 15. a page the browser would not open a microphone for (plain http, not localhost): no microphone
  await page.goto('http://louise.test/ask');
  check(await page.evaluate(() => !document.getElementById('mic') && !!document.getElementById('speak')),
        '15. not a secure context: the microphone is not shown, the speaker is');

  // 16. an initial or a title before a name ends no sentence (operator,
  //     2026-11-08: "G. Cesare" read with a long pause after "G.")
  const named = await page.evaluate(() => [
    louiseVoice.sentences('La Gallia fu conquistata da G. Cesare nel 58 a.C. Poi tornò a Roma da vincitore.', 'it'),
    louiseVoice.sentences('The Lord of the Rings was written by J. R. R. Tolkien in England. It was published much later, in three volumes.', 'en'),
    louiseVoice.sentences('Ne parla il Prof. Rossi nel suo libro sulla storia romana. Lo conferma anche il Dott. Bianchi, a lungo.', 'it')]);
  check(named[0][0].includes('G. Cesare'), '16. "G. Cesare" stays in one sentence', named[0]);
  check(named[1][0].includes('J. R. R. Tolkien') && named[1].length === 2, '16. "J. R. R. Tolkien" too', named[1]);
  check(named[2].length === 2 && named[2][0].includes('Prof. Rossi') && named[2][1].includes('Dott. Bianchi'),
        '16. a title before a name too', named[2]);
  // 17. the reading's times are kept, to measure its start and its pauses
  const tl = await page.evaluate(() => louiseVoice.timeline());
  check(tl && Array.isArray(tl.sentences) && 'first_word' in tl, '17. the reading\'s times are kept', tl);

  check(errors.length === 0, 'no page errors', errors);
  console.log(failures ? `${failures} FAILED` : 'ALL PASSED');
  await h.close();
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
