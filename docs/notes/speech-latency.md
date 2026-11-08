# The Ask page read aloud: why it starts late and pauses (2026-11-08)

The operator reported four faults with the reading on 2026-11-08: numbers and
dates read in Italian; a long pause after "G."; the answer arriving all at
once; a slow start and long pauses. The first two are fixed (#431 num2words,
#429 initials). This note holds the measurements for the other two and what
they point to.

## Measured (München, ab5f939, 2026-11-08)

Headless Chromium through the public site (Cloudflare Tunnel), three typed
questions, the speaker tapped once shown, **node paused**. The raw JSON is on
the node at /root/measure/tts/result.json.

| | search | chat cold load | first word | last word | first sound |
|---|---|---|---|---|---|
| 1, it, 2,387 chars | 44.5 s | 11.0 s | 57.8 s | 66.3 s | 99.5 s |
| 2, en, 3,911 chars | 21.2 s | 12.6 s | 35.9 s | 47.1 s | 59.7 s |
| 3, en, 2,422 chars | 20.6 s | 5.4 s | 28.1 s | 36.1 s | 54.8 s |

- **The stream is not bunched.** Events arrive spread (Cloudflare passes
  SSE through), first to last word 8.5 / 11.3 / 8.0 s, as the server
  writes them. "All at once" is the wait before the first word (search and
  cold loads), then an answer written at ~300 characters a second.
- **OmniVoice is slower than real time.** Each clip took 0.7–4.7 times its
  length to make, typically 1.5–2 (17–37 s for 3–35 s of audio), with two
  requests in flight. A first sentence of 9–17 s of audio waited 12–33 s. A
  pause follows wherever a sentence takes longer to make than the one before
  it takes to play.
- **The cold loads are the pause's.** A paused node preloads nothing (the
  GPU is the operator's, 2026-10-23; app/resident.py), so the chat model and
  the query encoder loaded on every question. This is measured again in
  acquire-only, which preloads and embeds nothing.
- **No thinking.** The chat model's stream carries content from 0.2 s and no
  reasoning deltas, with or without " /no_think".
- **Every sentence was cut at the last word + 40 ms.** This does not
  reproduce: test 18 in tests/browser/ask_voice.js streams an answer of the
  same shape, one word per delta, tapped at the first, and it is cut while
  it is written. `louiseVoice.timeline().started` (#435) tells a late tap
  from a reading moved at the end on the next run.

## What OmniVoice should do

From its own documentation ([PyPI](https://pypi.org/project/omnivoice/)):
- a real-time factor "as low as 0.025" (hardware not stated), against
  1.5–2 here, 60–80 times slower;
- `device_map` cuda, mps or **xpu (Intel Arc)**, so the B70 is supported;
- `num_step=32` diffusion steps, "or 16 for faster inference";
- a reference clip of 3–10 s: "longer clips slow inference and may reduce
  cloning quality".

The gap is too large for steps alone. In order of likelihood:
1. the backend runs on the CPU, not the card;
2. the Philippe profile's reference clip is longer than 10 s;
3. 32 steps where 16 would do.

## Asked of the node

- The omnivoice model YAML: device, `num_step`, any speed parameter.
- The Philippe profile's reference clip length.
- One fixed 200-character Italian sentence, 3 times one at a time and 3
  times two at once: synth and audio from `Server-Timing`, and whether the
  card or the CPU is busy meanwhile.
- The three questions again in acquire-only, with `timeline().started`.

## What follows from the numbers

On the CPU, the fix is the card (`xpu`). Then 16 steps if it is still
slower than real time, and a shorter reference clip if it is long. Fewer
steps or a shorter clip changes the voice, and is the operator's call by
ear; the device is not. Already done for the start (#435): a first sentence
over 90 characters is read in two, at a clause mark.
