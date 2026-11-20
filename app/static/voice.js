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
// with the page's answer language. The text goes into the question box —
// added to a question typed and not yet asked, else in place of the last —
// and is ASKED at once, as Ask asks it (operator, 2026-11-11: "click to
// activate and automatically ask at the end of the speech, then
// automatically tts of the answer"; it was the reader's to ask until then). A browser that
// will not open a microphone for this page (plain http on the LAN: only an
// https page or localhost may) shows no microphone.
//
// OUT. The speaker on the answer reads it aloud sentence by sentence — the
// sentences as the browser's own Intl.Segmenter finds them (Unicode's
// UAX #29), a line at a time — each one /v1/audio/speech's, played in order.
// WHILE THE ANSWER IS WRITTEN (operator, 2026-11-08): a sentence goes to the
// node as soon as the stream has completed it, so the reading starts with the
// first and keeps up; the last is read when the stream ends. A question the
// microphone wrote has its answer read by itself; a typed one waits for the
// speaker. Tap it to stop: nothing more is fetched or played. The node strips
// the citations and chooses the voice (app/speech.py) — the reader's, where
// Settings › voice chose one (with the account, else in this browser).
(function () {
  'use strict';
  const RATE = 16000, AUTO_STOP_MS = 1500, MAX_MS = 60000;
  // THE PAGE'S ACTIVITY (operator, 2026-11-09; docs/notes/ask-card-workflow.md
  // spec 6): the microphone and audio playing keep the question open, so the
  // node resumes its corpus work only after five minutes with neither — one
  // ping a minute at most
  const ACTIVE_EVERY_MS = 60000;
  let pinged = 0;
  function active() {
    const now = Date.now(); if (now - pinged < ACTIVE_EVERY_MS) return; pinged = now;
    fetch('/ask/active', { method: 'POST' }).catch(() => {});
  }
  // the energy check: loud is over FLOOR_MIN and over FLOOR_X times the
  // quietest window heard so far (the room's own level)
  const FLOOR_MIN = 0.012, FLOOR_X = 4;
  // A MICROPHONE THAT GIVES NOTHING IS SAID (operator, 2026-11-11: "when the
  // phone is connected to the car the mic of louise does not work"): no
  // sound at all after NO_SOUND_MS (the audio never started), or nothing but
  // digital silence after SILENT_MS (a microphone that is not on), ends the
  // recording with a plain line; a quiet one that is not silence goes to the
  // node after SILENT_MS, whose VAD says what is speech. Each recording's
  // outcome goes to the node's log (POST /ask/voice/report) — what the
  // browser opened, at what rate, what came — the one way to see a car's.
  const NO_SOUND_MS = 3000, SILENT_MS = 8000, DIGITAL_SILENCE = 1e-4;
  // a piece this short is read with the next (an "art." the segmenter cut off)
  const MIN_SENTENCE = 24;
  // A PIECE ENDING IN AN INITIAL OR A TITLE ends no sentence (operator,
  // 2026-11-08: "G. Cesare" read with a long pause after "G."): Unicode's
  // segmenter breaks after any full stop before a capital (UAX #29, SB11),
  // and ICU's abbreviation list (`ss=standard`) is not offered by
  // Intl.Segmenter. One letter, or a title before a name, is read with the
  // next piece; the node drops its stop (app/speech.py `_STOP_BEFORE_NAME`).
  const TITLES = new Set(('dr mr mrs ms prof st jr sr sig sigg dott ing avv arch geom rag on '
                          + 'mme mlle mgr pr hr fr frl sra srta').split(' '));
  const endsInName = p => {
    const m = /(?:^|[\s(«"'“‘])([\p{L}]{1,5})\.$/u.exec(p);
    return !!m && ((m[1].length === 1 && m[1] === m[1].toUpperCase() && m[1] !== m[1].toLowerCase())
                   || TITLES.has(m[1].toLowerCase()));
  };
  // sentences being synthesized at once (pump)
  const IN_FLIGHT = 2;
  // THE FIRST SOUND (München, 2026-11-08: OmniVoice takes 1.5-2x a clip's
  // length to make it, 12-33 s for a first sentence of 9-17 s): a first
  // sentence longer than FIRST_MAX is read in two, cut at its last clause
  // mark (, ; :) between FIRST_MIN and FIRST_MAX characters
  const FIRST_MAX = 90, FIRST_MIN = 30;
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
  // questionOn: a question of the page is being searched or answered
  let rec = null, busy = false, questionOn = false;

  // THE MICROPHONE'S THREE LOOKS (operator, 2026-11-12: "After speaking, the
  // button remains red; it shall instead turn grey while the query is
  // processed, then again of the base color when I can enter a new query"):
  // red while it records; grey and inert while what was said is written
  // down AND while the question runs (ask.html `go`), 'wait'; its base
  // colour when a new question can be spoken.
  function micState(kind, text) {
    if (!mic) return;
    mic.classList.toggle('rec', kind === 'rec');
    mic.setAttribute('aria-pressed', kind === 'rec' ? 'true' : 'false');
    mic.disabled = kind === 'busy' || kind === 'wait';
    mic.title = kind === 'rec' ? 'stop recording'
      : kind === 'wait' ? 'the microphone comes back once the question is answered' : 'speak the question';
    if (status) { status.textContent = text || ''; status.hidden = !text; }
  }
  // at rest: grey while a question runs, else ready
  function rest() { micState(questionOn ? 'wait' : 'idle'); }
  function clock(ms) { const s = Math.floor(ms / 1000); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }

  function onSamples(r, buf) {
    if (rec !== r || r.mode === 'recorder') return;
    r.chunks.push(buf);
    hear(r, buf, r.rate);
  }
  // the energy check on a window of samples, for either way of recording;
  // true once it ended the recording
  function hear(r, buf, rate) {
    r.frames++;
    let e = 0; for (let i = 0; i < buf.length; i++) { e += buf[i] * buf[i]; const a = Math.abs(buf[i]); if (a > r.peak) r.peak = a; }
    const rms = Math.sqrt(e / (buf.length || 1)), ms = buf.length * 1000 / rate;
    const loud = rms > Math.max(FLOOR_MIN, FLOOR_X * (r.floor === null ? 0 : r.floor));
    r.floor = r.floor === null ? rms : Math.min(r.floor, rms);
    r.ms += ms;
    if (loud) { r.heard = true; r.quiet = 0; } else if (r.heard) r.quiet += ms;
    if (!r.heard && r.ms >= SILENT_MS) {
      if (r.peak < DIGITAL_SILENCE) { fail(r, 'silent', 'the microphone sends only silence — ' + CAR_HINT); return true; }
      r.why = 'quiet'; stop(); return true;    // not silence: the node's VAD hears what is speech
    }
    if ((r.heard && r.quiet >= AUTO_STOP_MS) || r.ms >= MAX_MS) { r.why = r.heard ? 'speech' : 'longest'; stop(); return true; }
    return false;
  }

  // THE SECOND WAY TO RECORD (operator, 2026-11-11, the car: "Mic: no sound
  // at all"): WebAudio is clocked by the AUDIO OUTPUT — a car that takes the
  // phone's audio for its Bluetooth, or a route that stalls, leaves the
  // context running no callback, and not one sample comes. MediaRecorder
  // records the microphone's track itself, whatever the output does; what it
  // recorded is decoded by an OfflineAudioContext (no device either) at RATE,
  // the end of speech found by the same energy check every RECORDER_SLICE_MS
  // on what came since, and the same WAV goes to the node.
  const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
  const RECORDER_SLICE_MS = 500, WINDOW = 2048;
  function viaRecorder(r) {
    if (!window.MediaRecorder || !OAC || !r.stream.getAudioTracks().some(t => t.readyState === 'live')) return false;
    let mr; try { mr = new MediaRecorder(r.stream); } catch (e) { return false; }
    try { if (r.src) r.src.disconnect(); if (r.node) r.node.disconnect(); } catch (e) { /* already */ }
    r.mode = 'recorder'; r.mr = mr; r.parts = []; r.done = 0; r.decodeFails = 0; r.ctxWas = r.ctx ? r.ctx.state : null;
    mr.ondataavailable = e => { if (e.data && e.data.size) { r.parts.push(e.data); if (rec === r) listen(r); } };
    try { mr.start(RECORDER_SLICE_MS); } catch (e) { r.mode = null; return false; }
    clearTimeout(r.watch);
    r.watch = setTimeout(() => {
      if (rec === r && !r.parts.length) fail(r, 'no sound', 'the microphone gives the page no sound — ' + CAR_HINT);
    }, NO_SOUND_MS);
    return true;
  }
  async function decoded(r) {
    const blob = new Blob(r.parts, { type: r.mr.mimeType || 'audio/webm' });
    const ab = await new OAC(1, RATE, RATE).decodeAudioData(await blob.arrayBuffer());
    return ab.getChannelData(0);           // resampled to RATE by the decoder (Web Audio's decodeAudioData)
  }
  async function listen(r) {
    if (r.decoding) return;
    r.decoding = true;
    try {
      const pcm = await decoded(r);
      while (rec === r && r.done + WINDOW <= pcm.length) {
        const w = pcm.subarray(r.done, r.done + WINDOW); r.done += WINDOW;
        if (hear(r, w, RATE)) break;
      }
    } catch (e) { r.decodeFails++; }      // a piece not yet decodable: the next slice, or the tap
    finally { r.decoding = false; }
  }
  // what the recorder holds once it has stopped
  async function recorded(r) {
    if (r.mr.state !== 'inactive') {
      await new Promise(done => { r.mr.addEventListener('stop', done, { once: true });
                                  try { r.mr.stop(); } catch (e) { done(); } });
    }
    try { return r.parts.length ? await decoded(r) : new Float32Array(0); } catch (e) { return new Float32Array(0); }
  }
  const CAR_HINT = 'on a car\'s or a headset\'s Bluetooth the phone may keep the microphone for it: '
                 + 'disconnect it, or type the question (this was sent to the node to look into)';

  // what the browser gave: the device, its settings, the audio's rate and state
  function micInfo(r) {
    const t = r.stream && r.stream.getAudioTracks()[0];
    let set = {}; try { set = t && t.getSettings ? t.getSettings() : {}; } catch (e) { /* none */ }
    return { label: t ? t.label : null, ready: t ? t.readyState : null, muted: t ? t.muted : null, mutes: r.mutes,
             rate: set.sampleRate ?? null, channels: set.channelCount ?? null, ec: set.echoCancellation ?? null,
             ns: set.noiseSuppression ?? null, agc: set.autoGainControl ?? null,
             ctx_rate: r.ctx ? r.ctx.sampleRate : null, ctx_state: r.ctx ? r.ctx.state : null,
             frames: r.frames, ms: Math.round(r.ms), peak: Math.round(r.peak * 1e5) / 1e5, heard: r.heard,
             mode: r.mode || 'webaudio', ctx_was: r.ctxWas ?? null, parts: r.parts ? r.parts.length : null,
             decode_fails: r.decodeFails ?? null, mime: r.mr ? r.mr.mimeType : null,
             inputs: r.inputs, ua: navigator.userAgent };
  }
  function report(outcome, info) {
    try {
      fetch('/ask/voice/report', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json' },
                                   body: JSON.stringify({ outcome, ...info }) }).catch(() => {});
    } catch (e) { /* the report is never in the way */ }
  }
  // the recording ends without a question: said, released, reported
  function fail(r, outcome, text) {
    if (rec !== r) return;
    const info = micInfo(r);
    rec = null; release(r); rest(); say(text);
    report(outcome, info);
  }

  async function start() {
    stopReading(); auto = false;         // the microphone would hear the answer
    unlock();                            // inside the tap: the answer may be read later
    busy = true; micState('busy', 'opening the microphone…'); active();
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    } catch (e) {
      busy = false; rest();
      report('not opened: ' + ((e && (e.name || e.message)) || e), { ua: navigator.userAgent });
      say(e && e.name === 'NotAllowedError'
        ? 'the microphone is not allowed for this page — allow it in the browser to speak a question'
        : 'no microphone could be opened (' + ((e && (e.message || e.name)) || e) + ')');
      return;
    }
    const r = { stream, ctx: null, src: null, node: null, chunks: [], rate: 0,
                ms: 0, quiet: 0, heard: false, floor: null, tick: null,
                frames: 0, peak: 0, mutes: 0, why: null, watch: null, inputs: null, max: null,
                mode: null, mr: null, parts: null };
    const track = stream.getAudioTracks()[0];
    if (track) {
      track.addEventListener('mute', () => { r.mutes++; });
      track.addEventListener('ended', () => fail(r, 'ended', 'the microphone was taken away from the page — ' + CAR_HINT));
    }
    navigator.mediaDevices.enumerateDevices().then(ds => {
      r.inputs = ds.filter(d => d.kind === 'audioinput').map(d => d.label || '?');
    }).catch(() => {});
    try {
      // THE AUDIO AT THE MICROPHONE'S OWN RATE: a Bluetooth car or headset
      // gives 16 or 8 kHz, and a browser that cannot join two rates
      // (Firefox: "different sample-rate is currently not supported") then
      // records nothing; the page's downsample takes any rate to RATE
      let ctx = null;
      const want = track && track.getSettings ? track.getSettings().sampleRate : null;
      if (want) { try { ctx = new AC({ sampleRate: want }); } catch (e) { ctx = null; } }
      ctx = r.ctx = ctx || new AC();
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
      const info = micInfo(r);
      release(r); busy = false; rest();
      say('the microphone could not be recorded (' + ((e && (e.message || e.name)) || e) + ')');
      report('failed: ' + ((e && (e.name || e.message)) || e), info);
      return;
    }
    rec = r; busy = false;
    // no sound at all: the audio is woken once, then recorded the second way
    // (viaRecorder), and only where that cannot start is the reader told
    r.watch = setTimeout(() => {
      if (rec !== r || r.frames) return;
      if (r.ctx && r.ctx.state !== 'running') r.ctx.resume().catch(() => {});
      r.watch = setTimeout(() => {
        if (rec !== r || r.frames) return;
        if (!viaRecorder(r)) fail(r, 'no sound', 'the microphone gives the page no sound — ' + CAR_HINT);
      }, NO_SOUND_MS / 2);
    }, NO_SOUND_MS);
    // the longest recording, whatever comes or does not
    r.max = setTimeout(() => { if (rec === r) { r.why = 'longest'; stop(); } }, MAX_MS + 2000);
    const t0 = performance.now();
    const show = () => micState('rec', 'recording ' + clock(performance.now() - t0) + ' — tap the microphone to stop');
    show(); r.tick = setInterval(show, 250);
  }

  function release(r) {
    clearInterval(r.tick); clearTimeout(r.watch); clearTimeout(r.max);
    if (r.mr && r.mr.state !== 'inactive') { try { r.mr.stop(); } catch (e) { /* already */ } }
    try { if (r.src) r.src.disconnect(); if (r.node) r.node.disconnect(); } catch (e) { /* already */ }
    for (const t of r.stream.getTracks()) t.stop();
    if (r.ctx) r.ctx.close().catch(() => {});
  }

  async function stop() {
    const r = rec; if (!r) return;
    let toAsk = false;
    const why = r.why || 'tap';
    rec = null;
    let pcm;
    if (r.mode === 'recorder') {
      busy = true; micState('busy', 'writing down what you said…');
      clearInterval(r.tick); clearTimeout(r.watch); clearTimeout(r.max);
      pcm = await recorded(r);
      release(r);
    } else { release(r); pcm = downsample(r.chunks, r.rate, RATE); }
    const info = micInfo(r);
    if (!pcm.length) {
      rest(); say('no sound came from the microphone — ' + CAR_HINT);
      report('no sound', info);
      return;
    }
    busy = true; micState('busy', 'writing down what you said…');
    const fd = new FormData();
    fd.append('file', new Blob([wavBytes(pcm)], { type: 'audio/wav' }), 'question.wav');
    fd.append('response_format', 'json');
    const lang = pageLang(); if (lang) fd.append('language', lang);
    try {
      const resp = await fetch('/v1/audio/transcriptions', { method: 'POST', body: fd });
      const d = await resp.json().catch(() => ({}));
      info.stop = why; info.vad = resp.headers.get('Louise-VAD');
      if (!resp.ok) throw new Error((d.error && d.error.message) || d.detail || resp.statusText);
      const text = String(d.text || '').trim();
      if (!text) {
        say('no speech was heard — tap the microphone and speak again');
        report('no speech', info);
        return;
      }
      report('heard', { ...info, chars: text.length });
      // a question typed and not yet asked is added to; the one already
      // asked is replaced by the new one
      const q = document.getElementById('q'), prior = q.value.trim();
      q.value = prior && prior !== lastAsked ? q.value.replace(/\s*$/, ' ') + text : text;
      dictated = q.value.trim();         // asked as it is, a spoken question (asking)
      // NOT FOCUSED (operator, 2026-11-12: "Hitting the button to speak makes
      // the keyboard appear; wrong, it shall appear only by clicking in the
      // text box"): a phone opens its keyboard on focus, and the spoken
      // question is asked by itself — the box only shows it
      toAsk = true;
    } catch (e) {
      say('what you said could not be written down: ' + (e.message || e));
      report('not written down: ' + (e.message || e), info);
    } finally { busy = false; rest(); }
    // HANDS-FREE (operator, 2026-11-11: "click to activate and automatically
    // ask at the end of the speech, then automatically tts of the answer"):
    // the spoken question is asked as Ask would ask it — the page's switch
    // decides search or answer — and its answer is read by itself (asking)
    if (toAsk && typeof window.go === 'function') window.go();
  }

  if (mic) {
    if (window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && AC) {
      mic.hidden = false;
      mic.addEventListener('click', () => { if (busy || (questionOn && !rec)) return; rec ? stop() : start(); });
    } else {
      mic.remove();
      console.info('Louise: no microphone on this page — a browser opens one only for an https page or localhost');
    }
  }

  // ---- the answer, read aloud -------------------------------------------
  const speak = document.getElementById('speak');
  // reading: the one in progress; answerLang: the language of the text shown
  // (null: as the model wrote it); writing: the answer is streaming; auto:
  // the next answer is read by itself; dictated: what the microphone left in
  // the box; player: the page's one audio element
  let reading = null, answerLang = null, writing = false, auto = false, dictated = null, player = null;
  // the question the page last searched or asked: a new spoken one replaces it
  let lastAsked = null;
  // THE ANSWER MADE INTO SPEECH AS IT ARRIVES (operator, 2026-11-09: "keep
  // processing the answer as it arrives (TTS buffering)"): a reading that
  // is not played yet — its sentences asked for as the stream completes
  // them — taken over by the tap, which then plays what is already made.
  // The node keeps every sentence made (app/speech.py, the fast drive), so a
  // stop and a start again ask for nothing twice.
  let ahead = null;
  const live = r => reading === r || ahead === r;
  // THE READING'S TIMES (2026-11-08: "it takes a long time to start and it
  // takes long pauses" — measured before anything is changed): ms since Ask
  // was pressed — the answer's first and last word, and per sentence when it
  // was cut, sent, received, played and ended, with the node's own synthesis
  // time and the clip's length (`Server-Timing`). `louiseVoice.timeline()`.
  const now = () => Math.round(performance.now());
  let times = { asked: null, first: null, last: null, started: [], taps: [], items: [] };

  // the first sentence of a reading in two where it is long (FIRST_MAX)
  function firstPieces(s) {
    if (s.length <= FIRST_MAX) return [s];
    const head = s.slice(0, FIRST_MAX);
    let at = -1;
    for (const m of head.matchAll(/[,;:](?=\s)/g)) if (m.index + 1 >= FIRST_MIN) at = m.index + 1;
    return at < 0 ? [s] : [s.slice(0, at), s.slice(at).trim()];
  }
  // the answer AS WRITTEN (ask.html `answerSource`): the page shows its
  // Markdown formatted, the node drops the marks before reading (app/speech.py)
  const answerText = () => {
    if (typeof window.answerSource === 'function') return window.answerSource();
    const b = document.getElementById('answer'); return b ? b.textContent : '';
  };
  const collapse = line => line.replace(/\s+/g, ' ').trim();

  // one line's sentences (its whitespace collapsed), each with where it ends
  // in `t`: the segmenter's pieces, a piece under MIN_SENTENCE read with the
  // next, a short last piece with the one before
  function lineSentences(t, lang) {
    const seg = window.Intl && Intl.Segmenter ? new Intl.Segmenter(lang || undefined, { granularity: 'sentence' }) : null;
    let parts = seg ? [...seg.segment(t)].map(s => [s.segment.trim(), s.index + s.segment.length])
                    : [...t.matchAll(/[^.!?…。！？]+(?:[.!?…。！？]+["'»”)\]]*\s*|$)/g)].map(m => [m[0].trim(), m.index + m[0].length]);
    if (!parts.length) parts = [[t, t.length]];
    const out = []; let carry = '';
    for (const [p, end] of parts) {
      if (!p) continue;
      const s = carry ? carry + ' ' + p : p;
      if (s.length < MIN_SENTENCE || endsInName(s)) { carry = s; continue; }
      out.push({ text: s, end }); carry = '';
    }
    if (carry) {
      const last = out[out.length - 1];
      if (last && last.text.length + carry.length < 400) { last.text += ' ' + carry; last.end = t.length; }
      else out.push({ text: carry, end: t.length });
    }
    return out;
  }
  function sentences(text, lang) {
    const out = [];
    for (const line of String(text || '').split(/\n+/)) for (const s of lineSentences(collapse(line), lang)) out.push(s.text);
    return out;
  }

  // THE SENTENCES OF A TEXT STILL BEING WRITTEN (operator, 2026-11-08):
  // those after the reading's place — `r.at`, where its line starts in the
  // text, and `r.off`, in that line collapsed — that are final, and the place
  // moved past them. A line followed by a newline is final; in the open line,
  // every sentence but the last: one is complete only once a later one has
  // begun and is long enough not to be read with it (MIN_SENTENCE) — never
  // half of one. Once the answer has ended, all that is left. The rule of
  // `sentences`, so an answer read as it streams is read in the same pieces.
  function cut(r, text, ended) {
    const out = [];
    for (;;) {
      const nl = text.indexOf('\n', r.at), open = nl < 0;
      const ss = lineSentences(collapse(text.slice(r.at, open ? text.length : nl)).slice(r.off), r.lang);
      const fin = open && !ended ? ss.slice(0, -1) : ss;
      for (const s of fin) out.push(s.text);
      if (open) { if (fin.length) r.off += fin[fin.length - 1].end; return out; }
      r.at = nl + 1; r.off = 0;
    }
  }

  function speakState(on) {
    if (!speak) return;
    speak.classList.toggle('on', on);
    speak.setAttribute('aria-pressed', on ? 'true' : 'false');
    speak.title = on ? 'stop reading' : 'read the answer aloud';
    const l = speak.querySelector('.speak-label'); if (l) l.textContent = on ? 'stop' : 'read aloud';
  }

  // THE PAGE'S ONE PLAYER, played silent inside a tap — the microphone's,
  // Ask's, the speaker's: the browser then lets it play the answer that
  // comes later, without one (iOS asks it of the element itself)
  function unlock() {
    if (!speak) return;                        // the node reads nothing aloud
    if (!player) player = new Audio();
    if (reading) return;                       // it is playing already
    player.src = SILENT; player.play().catch(() => {});
  }

  function stopReading() {
    const r = reading; if (!r) return;
    reading = null; r.ctl.abort();             // the fetches in flight with it
    if (player) { player.pause(); player.removeAttribute('src'); }
    if (r.wake) r.wake();
    if (r.more) r.more();
    for (const it of r.items) it.ok(null);     // nothing waits on them any more
    for (const u of r.urls) URL.revokeObjectURL(u);
    speakState(false);
  }

  function play(r, url) {
    return new Promise(resolve => {
      const a = player;
      const done = () => { if (a.onended === done) a.onended = a.onerror = null; if (r.wake === done) r.wake = null; resolve(); };
      a.onended = a.onerror = done; r.wake = done;
      a.src = url; active();
      a.play().catch(e => { if (e && e.name === 'NotAllowedError') r.blocked = true; done(); });
    });
  }

  // `Server-Timing: synth;dur=812.4, audio;dur=2960.0` → {synth: 812.4, audio: 2960}
  function serverTiming(h) {
    const out = {};
    for (const part of String(h || '').split(',')) {
      const m = /^\s*([\w-]+)\s*;\s*dur=([\d.]+)/.exec(part); if (m) out[m[1]] = +m[2];
    }
    return out;
  }

  // THE READER'S VOICE (operator, 2026-11-11: "choose philippe or
  // valentina"): one of the node's recorded voices (GET /ask/voices), kept in
  // this browser; none kept, the node's own. A voice the node no longer holds
  // is not sent (and the node would speak its own anyway, app/speech.py).
  // A READER WITH AN ACCOUNT (operator, 2026-11-11: "Shall a user account
  // can choose it?") keeps it with the account (POST /ask/voice,
  // app/users.py): every browser and client of the account hears it.
  const VOICE_KEY = 'louise-voice';
  let voiceIds = new Set(), account = null, accountVoice = null;
  function chosenVoice() {
    let v = null;
    if (account) v = accountVoice;
    else { try { v = localStorage.getItem(VOICE_KEY); } catch (e) {} }
    return v && voiceIds.has(v) ? v : null;
  }
  async function keepVoice(v) {
    if (!account) { try { localStorage.setItem(VOICE_KEY, v); } catch (e) {} return; }
    accountVoice = v;
    try {
      const r = await fetch('/ask/voice', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ voice: v }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    } catch (e) { say('the voice could not be kept with your account: ' + (e.message || e)); }
  }
  async function loadVoices() {
    const box = document.getElementById('voice-choice'), pick = document.getElementById('voice-pick');
    if (!box || !pick) return;
    let got = null;
    try { const r = await fetch('/ask/voices'); if (r.ok) got = await r.json(); } catch (e) {}
    const list = (got && got.voices) || [];
    voiceIds = new Set(list.map(v => v.voice));
    account = got && got.account ? got.account.name : null;
    accountVoice = got && got.account ? got.account.voice : null;
    if (list.length < 2) { box.hidden = true; return; }
    pick.textContent = '';
    for (const v of list) {
      const o = document.createElement('option'); o.value = v.voice; o.textContent = v.name; pick.appendChild(o);
    }
    pick.value = chosenVoice() || got.default || list[0].voice;
    // what was made ahead of a tap is in the voice it was made in: made again
    pick.onchange = () => { keepVoice(pick.value); if (ahead) { dropAhead(); prefetch(); } };
    box.hidden = false;
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadVoices);
  else loadVoices();

  async function synth(r, it) {
    const s = it.text;
    const body = { input: s }; if (r.lang) body.language = r.lang;
    if (r.voice) body.voice = r.voice;
    it.t.sent = now();
    // the reading's order: the node sends sentence n once n - 1 is back
    // (app/speech.py Turns), so the backend never makes the second first
    const resp = await fetch('/v1/audio/speech', { method: 'POST', signal: r.ctl.signal,
      // `/last`: the answer has ended and this is its last sentence — the
      // node lets the corpus resume as soon as it is made (app/speech.py)
      headers: { 'Content-Type': 'application/json',
                 'Louise-Reading': r.id + '/' + it.n + (r.ended && it.n === r.items.length - 1 ? '/last' : '') },
      body: JSON.stringify(body) });
    Object.assign(it.t, serverTiming(resp.headers.get('server-timing')));
    if (resp.status === 400) return null;      // nothing to say once its citations are gone
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      throw new Error((d.error && d.error.message) || d.detail || resp.statusText);
    }
    const blob = await resp.blob();
    it.t.got = now();
    if (!live(r)) return null;
    const u = URL.createObjectURL(blob); r.urls.push(u); return u;
  }

  // EACH SENTENCE GOES TO THE NODE AS SOON AS IT IS COMPLETE, whatever the
  // stream and the player are doing (operator, 2026-11-08: "Chat and TTS use
  // different GPU resources"); only the playing is in order. IN_FLIGHT at a
  // time: a browser opens six connections to a host over HTTP/1.1, and the
  // answer's stream and the page's other requests keep theirs.
  function pump(r) {
    while (live(r) && r.next < r.items.length && r.inflight < IN_FLIGHT) {
      const it = r.items[r.next++]; r.inflight++;
      synth(r, it).then(it.ok, it.no).finally(() => { r.inflight--; pump(r); });
    }
  }

  // the reading takes in what the answer box holds now
  function feed(r) {
    const t = answerText();
    r.ended = !writing; r.text = t;
    for (const whole of cut(r, t, r.ended)) {
      for (const s of (r.items.length ? [whole] : firstPieces(whole))) {
        const it = { text: s, n: r.items.length, t: { chars: s.length, cut: now() } };
        times.items.push(it.t);
        it.p = new Promise((ok, no) => { it.ok = ok; it.no = no; }); it.p.catch(() => {});
        r.items.push(it);
      }
    }
    pump(r);
    if (r.more) { const f = r.more; r.more = null; f(); }
  }

  // THE READING FOLLOWS THE TEXT SHOWN. While it only grows (the stream), its
  // new sentences join the queue; the same text shown again (its [n] linked,
  // the original restored over a failed translation) adds nothing and
  // repeats nothing. Another text — the answer translated into the language
  // the reader switched to — holds no sentence that is exactly one already
  // read, so the reading moves to it and starts at its first sentence.
  function follow() {
    const r = reading;
    if (r.lang === answerLang && answerText().startsWith(r.text)) { feed(r); return; }
    stopReading(); startReading();
  }

  function newReading() {
    return { id: Math.random().toString(36).slice(2, 12) || 'r', ctl: new AbortController(), lang: answerLang, text: '', at: 0, off: 0, ended: false,
             items: [], next: 0, inflight: 0, urls: [], wake: null, more: null, blocked: false, voice: chosenVoice() };
  }
  function dropAhead() {
    const r = ahead; if (!r) return;
    ahead = null; r.ctl.abort();
    for (const it of r.items) it.ok(null);
    for (const u of r.urls) URL.revokeObjectURL(u);
  }
  // the answer shown, made into speech ahead of any tap
  function prefetch() {
    if (!speak) return;
    if (ahead && ahead.lang === answerLang && ahead.voice === chosenVoice() && answerText().startsWith(ahead.text)) { feed(ahead); return; }
    dropAhead();
    ahead = newReading();
    feed(ahead);
  }

  function startReading() {
    if (!player) player = new Audio();
    let r;
    if (ahead && ahead.lang === answerLang && ahead.voice === chosenVoice() && answerText().startsWith(ahead.text)) { r = ahead; ahead = null; }
    else { dropAhead(); r = newReading(); }
    reading = r; speakState(true);
    times.started.push(now());
    feed(r);
    run(r);
  }

  // the sentences played in order, each as soon as it is there
  async function run(r) {
    try {
      for (let i = 0; ; i++) {
        while (reading === r && i >= r.items.length && !r.ended) await new Promise(ok => { r.more = ok; });
        if (reading !== r || i >= r.items.length) return;
        const url = await r.items[i].p;
        if (reading !== r) return;
        if (url) { r.items[i].t.play = now(); await play(r, url); r.items[i].t.end = now(); }
        if (reading !== r) return;
        if (r.blocked) {
          if (speak) speak.hidden = false;
          say('the browser did not let the page play sound — tap the speaker to hear the answer');
          return;
        }
      }
    } catch (e) {
      if (reading === r && !(e && e.name === 'AbortError')) say('the answer could not be read aloud: ' + (e.message || e));
    } finally { if (reading === r) stopReading(); }
  }

  if (speak) speak.addEventListener('click', () => {
    times.taps.push(now());                    // when the tap came, beside when a reading started
    if (reading) { stopReading(); return; }
    unlock(); startReading();
  });

  // the page's hooks (ask.html); `lang` is the language of the text shown
  // (null: as the model wrote it)
  window.louiseVoice = {
    // Ask was pressed, and this runs inside the press: a question the
    // microphone wrote — the box holds what it left there — has its answer
    // read aloud by itself
    asking() {
      const q = document.getElementById('q');
      stopReading(); dropAhead();
      times = { asked: now(), first: null, last: null, started: [], taps: [], items: [] };
      auto = !!speak && dictated !== null && !!q && q.value.trim() === dictated;
      if (auto) unlock();
    },
    // the answer being written grew
    answerGrew(lang) {
      writing = true; answerLang = lang || null;
      const t = now(); if (times.first === null) times.first = t; times.last = t;
      if (!speak || !answerText().trim()) return;
      speak.hidden = false;
      if (reading) follow(); else if (auto) { auto = false; startReading(); } else prefetch();
    },
    // the answer is all there, or shown again
    answerReady(lang) {
      writing = false; answerLang = lang || null;
      if (speak) speak.hidden = !answerText().trim();
      if (reading) follow(); else if (ahead) prefetch();
    },
    answerCleared() {
      writing = false; stopReading(); dropAhead(); if (speak) speak.hidden = true;
      const q = document.getElementById('q'); if (q) lastAsked = q.value.trim();
    },
    // a question of the page started (true) or ended (false): the
    // microphone is grey meanwhile (ask.html `go`)
    question(on) {
      questionOn = !!on;
      if (!rec && !busy) rest();
    },
    // the reading's times, in ms from Ask (null before an Ask on this page)
    timeline() {
      const at = times.asked, rel = v => (v == null || at == null ? null : v - at);
      return { first_word: rel(times.first), last_word: rel(times.last),
               // each start of a reading: a tap, a question read by itself,
               // or a reading moved to a changed text (follow)
               started: times.started.map(rel), taps: times.taps.map(rel),
               sentences: times.items.map(t => ({ chars: t.chars, cut: rel(t.cut), sent: rel(t.sent),
                 got: rel(t.got), synth: t.synth ?? null, audio: t.audio ?? null,
                 play: rel(t.play), end: rel(t.end) })) };
    },
    sentences, cut, firstPieces, downsample, wavBytes,
  };
})();
