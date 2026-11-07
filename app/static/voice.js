// The Ask page's voice (app/voice.py; operator, 2026-11-07): a question
// spoken into the microphone, an answer read aloud. The page loads this only
// where the node sets one of the two models, and renders only the button of
// the voice it offers; every model and voice is the node's — nothing here
// names one, and nothing asks for one (CLAUDE.md RULE 2).
//
// IN. Tap the microphone to start, tap it again to stop. It also stops by
// itself after AUTO_STOP_MS of quiet once speech was heard — a plain energy
// check, enough to end a recording; what is speech is the node's VAD's to
// say (app/transcribe.py) — and after MAX_MS. WebAudio captures; the page
// downsamples to what the node's VAD and speech-to-text take — a WAV of
// 16-bit samples at 16 kHz, mono — and posts it to /v1/audio/transcriptions
// with the page's answer language. The text goes into the question box,
// which stays the reader's: nothing is asked until they ask. A browser that
// will not open a microphone for this page (plain http on the LAN: only an
// https page or localhost may) shows no microphone.
//
// OUT. The speaker on the answer reads it aloud sentence by sentence — the
// sentences as the browser's own Intl.Segmenter finds them (Unicode's
// UAX #29), a line at a time — each one /v1/audio/speech's, the next fetched
// while one plays, played in order. Tap it again to stop. The node strips
// the citations and chooses the voice (app/speech.py).
(function () {
  'use strict';
  const RATE = 16000, AUTO_STOP_MS = 1500, MAX_MS = 60000;
  // the energy check: loud is over FLOOR_MIN and over FLOOR_X times the
  // quietest window heard so far (the room's own level)
  const FLOOR_MIN = 0.012, FLOOR_X = 4;
  // a piece this short is read with the next (an "art." the segmenter cut off)
  const MIN_SENTENCE = 24;
  // a WAV of no samples: played in the tap, it lets the page play the rest (iOS)
  const SILENT = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=';
  const WORKLET = "class LouiseCapture extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array(2048);this.n=0}"
    + "process(i){const c=i[0]&&i[0][0];if(c){let k=0;while(k<c.length){const m=Math.min(c.length-k,this.b.length-this.n);"
    + "this.b.set(c.subarray(k,k+m),this.n);this.n+=m;k+=m;if(this.n===this.b.length){this.port.postMessage(this.b.slice(0));this.n=0}}}"
    + "return true}}registerProcessor('louise-capture',LouiseCapture)";
  const say = m => (typeof toast === 'function' ? toast(m) : console.warn(m));
  const pageLang = () => (typeof selectedLang === 'function' ? selectedLang() : null);

  // ---- the WAV the node takes -------------------------------------------
  // the recording as one buffer at `to` Hz: each output sample the mean of
  // the input samples it spans (a box filter before the decimation)
  function downsample(chunks, from, to) {
    let n = 0; for (const c of chunks) n += c.length;
    const all = new Float32Array(n); let o = 0;
    for (const c of chunks) { all.set(c, o); o += c.length; }
    if (from === to) return all;
    const ratio = from / to, out = new Float32Array(Math.floor(n / ratio));
    for (let i = 0; i < out.length; i++) {
      const a = Math.floor(i * ratio), b = Math.min(n, Math.max(a + 1, Math.floor((i + 1) * ratio)));
      let s = 0; for (let k = a; k < b; k++) s += all[k];
      out[i] = s / (b - a);
    }
    return out;
  }
  // RIFF/WAVE, PCM, mono, RATE Hz, 16 bits; a sample is round(x * 32768), clamped
  function wavBytes(x) {
    const buf = new ArrayBuffer(44 + 2 * x.length), v = new DataView(buf);
    const str = (at, s) => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); v.setUint32(4, 36 + 2 * x.length, true); str(8, 'WAVE');
    str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, RATE, true); v.setUint32(28, 2 * RATE, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    str(36, 'data'); v.setUint32(40, 2 * x.length, true);
    for (let i = 0; i < x.length; i++) v.setInt16(44 + 2 * i, Math.max(-32768, Math.min(32767, Math.round(x[i] * 32768))), true);
    return buf;
  }

  // ---- the microphone ---------------------------------------------------
  const mic = document.getElementById('mic'), status = document.getElementById('mic-status');
  const AC = window.AudioContext || window.webkitAudioContext;
  let rec = null, busy = false;

  function micState(kind, text) {
    if (!mic) return;
    mic.classList.toggle('rec', kind === 'rec');
    mic.setAttribute('aria-pressed', kind === 'rec' ? 'true' : 'false');
    mic.disabled = kind === 'busy';
    mic.title = kind === 'rec' ? 'stop recording' : 'speak the question';
    if (status) { status.textContent = text || ''; status.hidden = !text; }
  }
  function clock(ms) { const s = Math.floor(ms / 1000); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }

  function onSamples(r, buf) {
    if (rec !== r) return;
    r.chunks.push(buf);
    let e = 0; for (let i = 0; i < buf.length; i++) e += buf[i] * buf[i];
    const rms = Math.sqrt(e / (buf.length || 1)), ms = buf.length * 1000 / r.rate;
    const loud = rms > Math.max(FLOOR_MIN, FLOOR_X * (r.floor === null ? 0 : r.floor));
    r.floor = r.floor === null ? rms : Math.min(r.floor, rms);
    r.ms += ms;
    if (loud) { r.heard = true; r.quiet = 0; } else if (r.heard) r.quiet += ms;
    if ((r.heard && r.quiet >= AUTO_STOP_MS) || r.ms >= MAX_MS) stop();
  }

  async function start() {
    stopReading();                       // the microphone would hear the answer
    busy = true; micState('busy', 'opening the microphone…');
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      busy = false; micState('idle');
      say(e && e.name === 'NotAllowedError'
        ? 'the microphone is not allowed for this page — allow it in the browser to speak a question'
        : 'no microphone could be opened (' + ((e && (e.message || e.name)) || e) + ')');
      return;
    }
    const r = { stream, ctx: null, src: null, node: null, chunks: [], rate: 0,
                ms: 0, quiet: 0, heard: false, floor: null, tick: null };
    try {
      const ctx = r.ctx = new AC();
      r.src = ctx.createMediaStreamSource(stream); r.rate = ctx.sampleRate;
      if (ctx.audioWorklet && window.AudioWorkletNode) {
        const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
        try {
          await ctx.audioWorklet.addModule(url);
          r.node = new AudioWorkletNode(ctx, 'louise-capture', { numberOfOutputs: 1, outputChannelCount: [1] });
          r.node.port.onmessage = e => onSamples(r, e.data);
        } catch (e) { r.node = null; } finally { URL.revokeObjectURL(url); }
      }
      if (!r.node) {   // a browser without AudioWorklet
        r.node = ctx.createScriptProcessor(4096, 1, 1);
        r.node.onaudioprocess = e => onSamples(r, new Float32Array(e.inputBuffer.getChannelData(0)));
      }
      r.src.connect(r.node); r.node.connect(ctx.destination);   // silent: nothing is written to the output
      if (ctx.state === 'suspended') { try { await ctx.resume(); } catch (e) { /* it runs or it does not */ } }
    } catch (e) {
      release(r); busy = false; micState('idle');
      say('the microphone could not be recorded (' + ((e && (e.message || e.name)) || e) + ')');
      return;
    }
    rec = r; busy = false;
    const t0 = performance.now();
    const show = () => micState('rec', 'recording ' + clock(performance.now() - t0) + ' — tap the microphone to stop');
    show(); r.tick = setInterval(show, 250);
  }

  function release(r) {
    clearInterval(r.tick);
    try { if (r.src) r.src.disconnect(); if (r.node) r.node.disconnect(); } catch (e) { /* already */ }
    for (const t of r.stream.getTracks()) t.stop();
    if (r.ctx) r.ctx.close().catch(() => {});
  }

  async function stop() {
    const r = rec; if (!r) return;
    rec = null; release(r);
    const pcm = downsample(r.chunks, r.rate, RATE);
    if (!pcm.length) { micState('idle'); return; }
    busy = true; micState('busy', 'writing down what you said…');
    const fd = new FormData();
    fd.append('file', new Blob([wavBytes(pcm)], { type: 'audio/wav' }), 'question.wav');
    fd.append('response_format', 'json');
    const lang = pageLang(); if (lang) fd.append('language', lang);
    try {
      const resp = await fetch('/v1/audio/transcriptions', { method: 'POST', body: fd });
      const d = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((d.error && d.error.message) || d.detail || resp.statusText);
      const text = String(d.text || '').trim();
      if (!text) { say('no speech was heard — tap the microphone and speak again'); return; }
      const q = document.getElementById('q');
      q.value = q.value.trim() ? q.value.replace(/\s*$/, ' ') + text : text;
      q.focus(); q.setSelectionRange(q.value.length, q.value.length);
    } catch (e) {
      say('what you said could not be written down: ' + (e.message || e));
    } finally { busy = false; micState('idle'); }
  }

  if (mic) {
    if (window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && AC) {
      mic.hidden = false;
      mic.addEventListener('click', () => { if (busy) return; rec ? stop() : start(); });
    } else {
      mic.remove();
      console.info('Louise: no microphone on this page — a browser opens one only for an https page or localhost');
    }
  }

  // ---- the answer, read aloud -------------------------------------------
  const speak = document.getElementById('speak');
  let reading = null, answerLang = null;

  function sentences(text, lang) {
    const seg = window.Intl && Intl.Segmenter ? new Intl.Segmenter(lang || undefined, { granularity: 'sentence' }) : null;
    const out = [];
    for (const line of String(text || '').split(/\n+/)) {
      const t = line.replace(/\s+/g, ' ').trim(); if (!t) continue;
      const parts = seg ? [...seg.segment(t)].map(s => s.segment.trim())
                        : (t.match(/[^.!?…。！？]+(?:[.!?…。！？]+["'»”)\]]*\s*|$)/g) || [t]).map(s => s.trim());
      let carry = '';
      for (const p of parts.filter(Boolean)) {
        const s = carry ? carry + ' ' + p : p;
        if (s.length < MIN_SENTENCE) { carry = s; continue; }
        out.push(s); carry = '';
      }
      if (carry) { if (out.length && out[out.length - 1].length + carry.length < 400) out[out.length - 1] += ' ' + carry; else out.push(carry); }
    }
    return out;
  }

  function speakState(on) {
    if (!speak) return;
    speak.classList.toggle('on', on);
    speak.setAttribute('aria-pressed', on ? 'true' : 'false');
    speak.title = on ? 'stop reading' : 'read the answer aloud';
    const l = speak.querySelector('.speak-label'); if (l) l.textContent = on ? 'stop' : 'read aloud';
  }

  function stopReading() {
    const r = reading; if (!r) return;
    reading = null; r.ctl.abort();
    r.audio.pause(); r.audio.removeAttribute('src');
    if (r.wake) r.wake();
    for (const u of r.urls) URL.revokeObjectURL(u);
    speakState(false);
  }

  function play(r, url) {
    return new Promise(resolve => {
      const a = r.audio;
      const done = () => { a.onended = a.onerror = null; r.wake = null; resolve(true); };
      a.onended = a.onerror = done; r.wake = done;
      a.src = url;
      a.play().catch(e => { if (e && e.name === 'NotAllowedError') { r.blocked = true; } done(); });
    });
  }

  async function readAloud() {
    const box = document.getElementById('answer');
    const lang = answerLang, parts = sentences(box ? box.textContent : '', lang);
    if (!parts.length) return;
    const r = { ctl: new AbortController(), audio: new Audio(), urls: [], wake: null, blocked: false };
    reading = r; speakState(true);
    r.audio.src = SILENT; r.audio.play().catch(() => {});   // inside the tap
    const get = s => {
      const p = (async () => {
        const body = { input: s }; if (lang) body.language = lang;
        const resp = await fetch('/v1/audio/speech', { method: 'POST', signal: r.ctl.signal,
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        if (resp.status === 400) return null;          // nothing to say once its citations are gone
        if (!resp.ok) {
          const d = await resp.json().catch(() => ({}));
          throw new Error((d.error && d.error.message) || d.detail || resp.statusText);
        }
        const u = URL.createObjectURL(await resp.blob()); r.urls.push(u); return u;
      })();
      p.catch(() => {});                                // awaited below; never unhandled
      return p;
    };
    try {
      let next = get(parts[0]);
      for (let i = 0; i < parts.length; i++) {
        const url = await next;
        if (reading !== r) return;
        next = i + 1 < parts.length ? get(parts[i + 1]) : null;   // fetched while this one plays
        if (url) await play(r, url);
        if (reading !== r) return;
        if (r.blocked) { say('the browser did not let the page play sound — tap the speaker again'); return; }
      }
    } catch (e) {
      if (reading === r && !(e && e.name === 'AbortError')) say('the answer could not be read aloud: ' + (e.message || e));
    } finally { if (reading === r) stopReading(); }
  }

  if (speak) speak.addEventListener('click', () => { reading ? stopReading() : readAloud(); });

  // the page's hooks (ask.html): an answer is there to read, in `lang`
  // (null: as the model wrote it), or it is gone
  window.louiseVoice = {
    answerReady(lang) {
      answerLang = lang || null;
      const box = document.getElementById('answer');
      if (speak) speak.hidden = !(box && box.textContent.trim());
    },
    answerCleared() { stopReading(); if (speak) speak.hidden = true; },
    sentences, downsample, wavBytes,
  };
})();
