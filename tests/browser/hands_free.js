// Hands-free on the page (docs/notes/hands-free-voice.md step 1): the switch
// keeps the microphone open, the energy gate cuts each utterance and posts it
// to /ask/voice/heard, ONE in flight (a newer one replaces a waiting one), an
// addressed question is asked, a dropped one is not, the name alone opens the
// continuation window, and a tap on the microphone turns it off; a long press
// on it turns hands-free on and off, and a tap stays one spoken question.
// argv: voice.js utterances.wav — Chromium's fake microphone loops the WAV.
const fs = require('fs'); const { chromium } = require('playwright');
const wav = ms => { const n = Math.round(16 * ms), b = Buffer.alloc(44 + 2 * n);
  b.write('RIFF', 0); b.writeUInt32LE(36 + 2 * n, 4); b.write('WAVE', 8); b.write('fmt ', 12); b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(2 * n, 40); return b; };
const PAGE = '<textarea id="q"></textarea><div id="answer"></div><button id="mic">mic</button>'
  + '<div id="mic-status"></div><label id="hands-free-choice" hidden><input type="checkbox" id="hands-free"></label>'
  + '<button id="speak">read</button><script>window.asked=[];window.go=()=>{asked.push(document.getElementById("q").value)}</script>'
  + '<script src="/v.js"></script>';
let fails = 0;
const check = (ok, what, d) => { console.log((ok ? 'PASS ' : 'FAIL ') + what + (d !== undefined ? ' — ' + JSON.stringify(d).slice(0, 500) : '')); fails += !ok; };
(async () => {
  const b = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
                                           '--use-file-for-fake-audio-capture=' + process.argv[3], '--autoplay-policy=no-user-gesture-required'] });
  const ctx = await b.newContext(); await ctx.grantPermissions(['microphone'], { origin: 'http://localhost' });
  const p = await ctx.newPage();
  const posts = []; let live = 0, most = 0, script = [];
  await p.route('**/*', async r => {
    const u = new URL(r.request().url());
    if (u.pathname === '/') return r.fulfill({ contentType: 'text/html', body: PAGE });
    if (u.pathname === '/v.js') return r.fulfill({ contentType: 'application/javascript', body: fs.readFileSync(process.argv[2], 'utf8') });
    if (u.pathname === '/ask/voice/heard') {
      live++; most = Math.max(most, live);
      const body = r.request().postDataBuffer() || Buffer.alloc(0);
      posts.push({ at: Date.now(), bytes: body.length, follow: body.includes('name="follow"'), riff: body.includes('RIFF') });
      const next = script.shift() || { wait: 0, answer: { addressed: false, command: null, text: null, dropped: 'not addressed' } };
      await new Promise(ok => setTimeout(ok, next.wait));
      live--;
      try { return await r.fulfill({ contentType: 'application/json', body: JSON.stringify(next.answer) }); }
      catch (e) { return; }                     // the page gave up on it (its bound)
    }
    if (u.pathname === '/ask/voices') return r.fulfill({ contentType: 'application/json', body: '{"voices":[],"default":null,"account":null}' });
    if (u.pathname === '/ask/voice/name') return r.fulfill({ contentType: 'application/json',
      body: JSON.stringify({ wake_name: 'Philippe', forms: ['philippe', 'philip', 'filippo'], mine: false }) });
    if (u.pathname === '/v1/audio/speech') return r.fulfill({ contentType: 'audio/wav', body: wav(3000) });
    return r.fulfill({ status: 204, body: '' });
  });
  await p.goto('http://localhost/');
  check(await p.evaluate(() => !document.getElementById('hands-free-choice').hidden), '1. the switch is shown beside the microphone');

  // 2. an addressed question is asked; the name alone opens the continuation window
  script = [{ wait: 0, answer: { addressed: true, command: 'listen', text: null } },
            { wait: 0, answer: { addressed: true, command: null, text: 'chi era Garibaldi?' } }];
  await p.evaluate(() => { const b = document.getElementById('hands-free'); b.checked = true; b.dispatchEvent(new Event('change')); });
  for (let t = 0; t < 200 && posts.length < 2; t++) await p.waitForTimeout(100);
  check(posts.length >= 2 && posts.every(x => x.riff), '2. each utterance goes to the node as a WAV', posts);
  check(posts[1] && posts[1].follow === true && posts[0].follow === false, '2. the name alone: the next one goes without it', posts.slice(0, 2));
  await p.waitForTimeout(300);
  check(JSON.stringify(await p.evaluate(() => asked)) === JSON.stringify(['chi era Garibaldi?']), '2. the question is asked', await p.evaluate(() => asked));

  // 3. one clip in flight: the node slow, utterances keep coming
  const before = posts.length; most = 0;
  script = Array.from({ length: 20 }, () => ({ wait: 5000, answer: { addressed: false, command: null, text: null, dropped: 'not addressed' } }));
  await p.waitForTimeout(12000);
  check(most === 1, '3. never more than one utterance in flight', { most, posts: posts.length - before });
  check(posts.length - before <= 3, '3. the ones cut meanwhile do not queue up', posts.length - before);
  check(JSON.stringify(await p.evaluate(() => asked)) === JSON.stringify(['chi era Garibaldi?']), '3. nothing unaddressed is asked');

  // 3b. a node that hangs: the clip in flight is bounded, the next one goes
  //     (architecture's read of 535b2f68, 3)
  await p.waitForTimeout(6000);                      // the slow ones of 3 answered
  await p.evaluate(() => louiseVoice.handsFree.setClipMax(2000));
  const hung = posts.length;
  script = Array.from({ length: 20 }, () => ({ wait: 60000, answer: { addressed: false, command: null, text: null } }));
  await p.waitForTimeout(10000);
  check(posts.length - hung >= 2, '3b. a hung node holds no clip past its bound: the next one goes', posts.length - hung);
  script = [];

  // 6. MUTED while a sentence naming it is read (operator, 2026-11-27: "every
  //    time in the speech there is that name the mic is muted"), and only then
  check(await p.evaluate(() => louiseVoice.handsFree.mentionsName('Louis Philippe abdicated.')
                         && louiseVoice.handsFree.mentionsName('Filippo II.') && !louiseVoice.handsFree.mentionsName('Louis abdicated.')),
        '6. the name is found in a sentence by its forms');
  const readAndWatch = async text => {
    await p.evaluate(t => {
      window.__mute = []; window.__heard = [];
      const f = window.fetch;
      window.fetch = (url, o) => { if (String(url).includes('/ask/voice/heard')) __heard.push(performance.now()); return f(url, o); };
      window.__poll = setInterval(() => { if (louiseVoice.handsFree.wake().muted) __mute.push(performance.now()); }, 50);
      louiseVoice.asking();
      document.getElementById('answer').textContent = t;
      louiseVoice.answerReady('en'); document.getElementById('speak').click();
    }, text);
    await p.waitForTimeout(7000);
    return p.evaluate(() => { clearInterval(__poll); document.getElementById('speak').click(); return { mute: __mute, heard: __heard }; });
  };
  script = Array.from({ length: 20 }, () => ({ wait: 0, answer: { addressed: false, command: null, text: null } }));
  const named = await readAndWatch('King Louis Philippe abdicated in eighteen forty eight, and left for England.');
  const span = named.mute.length ? named.mute[named.mute.length - 1] - named.mute[0] : 0;
  check(span >= 2500, '6. muted while the sentence that names it plays (3 s)', { span, samples: named.mute.length });
  const inside = named.heard.filter(t => named.mute.length && t > named.mute[0] + 100 && t < named.mute[named.mute.length - 1]);
  check(inside.length === 0, '6. no utterance heard while muted', { inside, heard: named.heard.length });
  const plain = await readAndWatch('King Louis abdicated in eighteen forty eight, and left for England.');
  check(plain.mute.length === 0, '6. a sentence without the name: never muted', plain.mute.length);

  // 4. a tap on the microphone turns hands-free off and closes it
  await p.evaluate(() => document.getElementById('mic').click());
  check(await p.evaluate(() => !louiseVoice.handsFree.on() && !document.getElementById('hands-free').checked),
        '4. a tap on the microphone turns it off');

  // 5. A LONG PRESS turns hands-free on and off (operator, 2026-11-27: "can you
  //    recognize a long pressure on the mic button?"); a tap stays a tap
  const box = await p.locator('#mic').boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  const hold = async (ms, moveBy = 0) => {
    await p.mouse.move(cx, cy); await p.mouse.down();
    if (moveBy) { await p.waitForTimeout(100); await p.mouse.move(cx + moveBy, cy); }
    await p.waitForTimeout(ms); await p.mouse.up(); await p.waitForTimeout(200);
  };
  const state = () => p.evaluate(() => ({ on: louiseVoice.handsFree.on(), box: document.getElementById('hands-free').checked,
                                          rec: document.getElementById('mic').classList.contains('rec') }));
  await hold(900);
  let st = await state();
  check(st.on && st.box && !st.rec, '5. a long press turns hands-free on, and records no question', st);
  await hold(900);
  st = await state();
  check(!st.on && !st.box, '5. a second long press turns it off', st);
  await hold(900, 40);
  st = await state();
  check(!st.on && !st.box, '5. a press that slides off the button does nothing', st);
  check(await p.evaluate(() => /hold: hands-free/.test(document.getElementById('mic').title)), '5. the button says so');
  await hold(80);
  st = await state();
  check(!st.on && !st.box && st.rec, '5. a tap is still one spoken question', st);
  await b.close();
  console.log(fails ? `${fails} FAILED` : 'ALL PASSED');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
