// Architecture's probes of the phrases that cover the wait (2026-11-27), kept
// as written but for the second's harness, against voice.js alone:
// 1. a phrase list that answers late never takes the player from a sentence
//    (every sentence plays to its end);
// 2. a phrase played is reported used ONCE (a stale handler fired by the next
//    SILENT unlock reported it twice);
// 3. a phrase PAUSED past cueBound, then resumed (architecture's read of
//    2643e7d8, 5; its probe, rebuilt on 2643e7d8: gaps [2000, 2001, 2000] ms
//    against [0, 0, 0]): the sentences follow at once, none that ended is
//    played again, and the phrase cut is not reported used.
// argv: voice.js
const fs = require('fs'); const { chromium } = require('playwright');
const wav = ms => { const n = Math.round(16 * ms), b = Buffer.alloc(44 + 2 * n);
  b.write('RIFF', 0); b.writeUInt32LE(36 + 2 * n, 4); b.write('WAVE', 8); b.write('fmt ', 12); b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(2 * n, 40); return b; };
const PAGE = '<textarea id="q"></textarea><div id="answer"></div><button id="speak">read</button><script src="/v.js"></script>';

async function harness(b, { fillersDelay, ready, speechMs, clipMs, used, speechDelay = 0 }) {
  const p = await b.newPage();
  await p.route('**/*', async r => {
    const u = new URL(r.request().url());
    if (u.pathname === '/') return r.fulfill({ contentType: 'text/html', body: PAGE });
    if (u.pathname === '/v.js') return r.fulfill({ contentType: 'application/javascript', body: fs.readFileSync(process.argv[2], 'utf8') });
    if (u.pathname === '/ask/voices') return r.fulfill({ contentType: 'application/json', body: '{"voices":[],"default":null,"account":null}' });
    if (u.pathname === '/ask/voice/fillers/used') { used.push(JSON.parse(r.request().postData()).ids);
      return r.fulfill({ contentType: 'application/json', body: '{"removed":1}' }); }
    if (u.pathname === '/ask/voice/fillers') { await new Promise(ok => setTimeout(ok, fillersDelay));
      return r.fulfill({ contentType: 'application/json', body: JSON.stringify({ lang: 'en', voice: 'v', ready }) }); }
    if (u.pathname.startsWith('/clip/')) return r.fulfill({ contentType: 'audio/wav', body: wav(clipMs) });
    if (u.pathname === '/v1/audio/speech') { if (speechDelay) await new Promise(ok => setTimeout(ok, speechDelay));
      return r.fulfill({ contentType: 'audio/wav', body: wav(speechMs) }); }
    return r.fulfill({ status: 204, body: '' });
  });
  await p.goto('http://localhost/');
  return p;
}

(async () => {
  const b = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
  let fails = 0;
  // 1. late_short_phrase
  {
    const p = await harness(b, { fillersDelay: 1500, ready: [{ id: 'x1', kind: 'short', url: '/clip/x1' }],
                                 speechMs: 1200, clipMs: 400, used: [] });
    await p.evaluate(() => {
      louiseVoice.asking(); louiseVoice.cueQuestion();
      document.getElementById('answer').textContent = 'The first sentence of the answer is here. The second sentence of the answer follows it. The third one ends it.';
      louiseVoice.answerGrew('en'); document.getElementById('speak').click();
      setTimeout(() => louiseVoice.answerReady('en'), 100);
    });
    await p.waitForTimeout(9000);
    const t = await p.evaluate(() => ({ s: louiseVoice.timeline().sentences.map(x => [x.play, x.end]), cues: louiseVoice.cues(), label: document.getElementById('speak').title }));
    const ok = t.s.length > 0 && t.s.every(([a, e]) => a != null && e != null);
    console.log((ok ? 'PASS' : 'FAIL') + ' every sentence plays to its end when the phrase list is late — ' + JSON.stringify(t));
    fails += !ok;
    await p.close();
  }
  // 2. phrase_used_twice
  {
    const used = [];
    const p = await harness(b, { fillersDelay: 0, ready: [{ id: 'x1', kind: 'short', url: '/clip/x1' }, { id: 'x2', kind: 'long', url: '/clip/x2' }],
                                 speechMs: 200, clipMs: 300, used });
    await p.evaluate(() => louiseVoice.cueQuestion());
    await p.waitForTimeout(1500);
    await p.evaluate(() => { document.getElementById('answer').textContent = 'The answer is written here in full, and it ends.'; louiseVoice.answerReady('en'); });
    await p.evaluate(() => document.getElementById('speak').click());
    await p.waitForTimeout(3000);
    const x1 = used.flat().filter(id => id === 'x1').length;
    const ok = x1 === 1;
    console.log((ok ? 'PASS' : 'FAIL') + ' a phrase played is reported used once — ' + JSON.stringify(used));
    fails += !ok;
    await p.close();
  }
  // 3. paused past the bound, resumed (architecture's sequence and timings:
  //    the tap's unlock before the phrase exists, the phrase list 200 ms late,
  //    speech 800 ms late, a 4 s phrase, 1 s sentences)
  {
    const used = [];
    const p = await harness(b, { fillersDelay: 200, ready: [{ id: 'x1', kind: 'short', url: '/clip/x1' }],
                                 speechMs: 1000, clipMs: 4000, used, speechDelay: 800 });
    await p.evaluate(() => {
      window.__ev = []; const T = () => Math.round(performance.now());
      const A = HTMLMediaElement.prototype, play = A.play;
      A.play = function () {
        if (!this.__w) { this.__w = true; this.addEventListener('ended', () => __ev.push([T(), 'ended', String(this.src)])); }
        __ev.push([T(), 'play', String(this.src)]); return play.call(this);
      };
      louiseVoice.asking(); louiseVoice.cueQuestion();
      document.getElementById('answer').textContent = 'The first sentence of the answer is here. The second sentence of the answer follows it. The third one ends it.';
      louiseVoice.answerGrew('en'); document.getElementById('speak').click();
      setTimeout(() => louiseVoice.answerReady('en'), 100);
    });
    await p.waitForTimeout(1500);
    await p.evaluate(() => { __ev.push([Math.round(performance.now()), 'tap:pause']); document.getElementById('speak').click(); });
    await p.waitForTimeout(8000);                                           // past cueBound (4 s + 1 s)
    await p.evaluate(() => { __ev.push([Math.round(performance.now()), 'tap:resume']); document.getElementById('speak').click(); });
    await p.waitForTimeout(12000);
    const t = await p.evaluate(() => ({ s: louiseVoice.timeline().sentences.map(x => [x.play, x.end]), ev: __ev }));
    await p.close();
    const gaps = []; for (let i = 1; i < t.s.length; i++) if (t.s[i][0] != null && t.s[i - 1][1] != null) gaps.push(Math.round(t.s[i][0] - t.s[i - 1][1]));
    const phrasePlayed = t.ev.some(e => e[1] === 'play' && e[2].includes('/clip/'));
    // a sentence (blob) played again after it ENDED, and the phrase played
    // again at the resume through the stale cue.audio (2643e7d8: x1, 1 ms
    // before sentence 0 took the player)
    const ended = new Set(), replays = [];
    const resumeAt = (t.ev.find(e => e[1] === 'tap:resume') || [Infinity])[0];
    for (const [at, k, src] of t.ev) {
      if (!src) continue;
      if (src.includes('/clip/') && k === 'play' && at >= resumeAt) replays.push(src);
      if (!src.startsWith('blob:')) continue;
      if (k === 'ended') ended.add(src); else if (k === 'play' && ended.has(src)) replays.push(src);
    }
    const okGaps = phrasePlayed && t.s.length >= 3 && gaps.length === t.s.length - 1 && gaps.every(g => g < 50);
    const okUsed = !used.flat().includes('x1');
    console.log((okGaps ? 'PASS' : 'FAIL') + ' a phrase paused past its bound, resumed: the sentences follow at once — '
                + JSON.stringify({ gaps, phrasePlayed, s: t.s }));
    console.log((!replays.length ? 'PASS' : 'FAIL') + ' and nothing is played again at the resume: no sentence that ended, not the phrase — ' + JSON.stringify(replays));
    console.log((okUsed ? 'PASS' : 'FAIL') + ' and the phrase cut is not reported used — ' + JSON.stringify(used));
    fails += !okGaps + !!replays.length + !okUsed;
  }
  await b.close();
  console.log(fails ? `${fails} FAILED` : 'ALL PASSED');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
