// Architecture's probes of the phrases that cover the wait (2026-11-27), kept
// as written but for the second's harness, against voice.js alone:
// 1. a phrase list that answers late never takes the player from a sentence
//    (every sentence plays to its end);
// 2. a phrase played is reported used ONCE (a stale handler fired by the next
//    SILENT unlock reported it twice).
// argv: voice.js
const fs = require('fs'); const { chromium } = require('playwright');
const wav = ms => { const n = Math.round(16 * ms), b = Buffer.alloc(44 + 2 * n);
  b.write('RIFF', 0); b.writeUInt32LE(36 + 2 * n, 4); b.write('WAVE', 8); b.write('fmt ', 12); b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22); b.writeUInt32LE(16000, 24); b.writeUInt32LE(32000, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34); b.write('data', 36); b.writeUInt32LE(2 * n, 40); return b; };
const PAGE = '<textarea id="q"></textarea><div id="answer"></div><button id="speak">read</button><script src="/v.js"></script>';

async function harness(b, { fillersDelay, ready, speechMs, clipMs, used }) {
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
    if (u.pathname === '/v1/audio/speech') return r.fulfill({ contentType: 'audio/wav', body: wav(speechMs) });
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
  await b.close();
  console.log(fails ? `${fails} FAILED` : 'ALL PASSED');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
