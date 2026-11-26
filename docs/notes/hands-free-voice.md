# Hands-free voice: the microphone left on, Philippe called by name

*2026-11-26. The operator: "I would like to be able to keep the mic on,
summoning the chat by starting a phrase with its name 'Philippe...', and
possibly pause or stop it 'Philippe wait/stop/hold on', resume, or ask a
follow up question." And the same day: "a 'waiting music' phrase, likely a
clip from a proper set, to cover the search time, something like 'let me
check', 'well, let's see what I can find on the topic' … maybe adding
something like 'uhm', or something that shows that it's doing something"
(§7). This is a design to evaluate. Nothing has been built
or measured; every figure below is read from the repository, and its
source is named.*

## 1. What exists today

- **In (app/static/voice.js, app/transcribe.py):**
  - The reader taps the microphone. The page records and ends the
    recording itself after `AUTO_STOP_MS` = 1.5 s of quiet once speech was
    heard, using a plain energy check.
  - It posts a 16 kHz mono WAV to `/v1/audio/transcriptions`.
  - The node's VAD (silero, CPU) trims the silence, and a clip with no
    speech is answered empty, with no STT call.
  - The STT (`[speech] stt_model`, parakeet) writes the text. On the
    iGPU it runs at RTF 0.158 (STATUS, 2026-11-11), so 2 s of speech takes
    about 0.3 s.
  - The text is asked at once.
- **Out:**
  - The answer is read sentence by sentence while it is written, through
    one `Audio` element (`player`).
  - Tapping the speaker stops it. Tapping Abort ends the answer (#47).
- **Activity:** the microphone and every clip played post `/ask/active`.
  That keeps the question open, and the node's corpus work waits until
  five minutes have passed with neither (ask-card-workflow.md, spec 6;
  dispatch `QUESTION_LINGER_S` = 300 s).
- **Conversation:**
  - The Ask page sends ONE turn: `messages: [{role: user, content: q}]`.
  - Its search uses `q` alone.
  - The answer API already keeps up to `HISTORY_TURNS` = 6 prior turns
    when they are sent (app/api.py `_build_answer`). The Ask page never
    sends them.
- **The car (voice.js, 2026-11-25):**
  - Opening the microphone switches a Bluetooth car from its media
    profile to its call profile.
  - The first ~5 s give nothing, which is why the page now waits for the
    first non-zero sample before showing it is recording.
- **Bug's branch (ccr-97590d8f-j56u36, not merged):** adds a ready tone,
  the deaf time at the start of a recording, and replay. The work below
  builds on it.

## 2. What the request needs

1. **A mode the reader turns on:** hands-free. The microphone stays open
   until it is turned off.
2. **An utterance addressed by its first word, the name.** Everything
   else heard is dropped: never asked, never shown, never logged.
3. **Commands, said after the name**, while an answer is read or written:
   - wait / hold on: the reading pauses;
   - resume / go on: it continues;
   - stop: the reading stops and an answer still being written is
     aborted.
4. **A question, said after the name:** a new question, or a follow-up to
   the last one.
5. **The node's own voice is not taken for the reader's** (echo).
6. **The wait is covered:** a short spoken phrase says the question was
   taken while the search runs (§7).

## 3. Decision 1: where the name is heard

**What it is:** something must decide, for each thing said near the phone,
whether it began with "Philippe".

| choice | pros | cons |
|---|---|---|
| **A. The node's STT, matched on the text** (recommended) | No new dependency: the page's energy gate already cuts utterances; the node's VAD drops non-speech, its STT writes the text, and the node checks the first word. The name and the question are ONE clip, so there is no wake step and no second recording. Any name works without training. | Every utterance near the phone (passengers, the car radio) goes to the node and is transcribed. A name the STT mis-spells is missed (Philippe → Philip, Filippo, Felipe): matched by a list of its written forms, measured (M2). About 32 KB/s while speech is heard (8 KB/s as Opus). |
| B. A wake-word model on the node, on the continuous stream (Home Assistant's pattern: openWakeWord behind the Wyoming protocol, in its own container) | Robust detection. Nothing is transcribed before the name. A low, flat CPU cost. A named, proven standard. | A new container and protocol (a new dependency: ask first). A model trained per name: openWakeWord trains "Philippe" from synthetic TTS clips, about an hour of GPU. The audio streams to the node all the time, ~32 KB/s. |
| C. A wake-word model in the browser (openWakeWord ported to onnxruntime-web, or Picovoice Porcupine) | Nothing leaves the phone before the name. The least data. | A new browser dependency (onnxruntime-web, ~10 MB of WASM). Battery. Porcupine is proprietary, and a custom word needs its console and an access key (a secret on a page). The openWakeWord ports are community code. |
| D. The browser's Web Speech API | Nothing to build. | Chrome sends the audio to Google, and Firefox lacks it. Not self-hosted: rejected. |

**Recommendation: A.** It is the smallest step, uses only what runs now,
and is measured in a day (§8). B is the standardized alternative, taken
if M2 or M4 fail: too many misses or false calls, or too much STT load
from a car radio. Default if the operator says nothing: A.

**The name** is the node's (`[speech] wake_name = "Philippe"`, desired
state in platform.toml; `louise node speech set --name`). Its written
forms are a list kept beside it, seeded from what the STT writes in M2:
Philippe, Philip, Phillip, Filippo, Felipe, Filip, Philipp. A form is
matched on the first word, case- and accent-folded. The
standardized alternative is a phonetic key (Double Metaphone), which
would need a library, so not now.

## 4. Decision 2: the node's voice and the reader's (echo)

**What it is:** with the microphone open while the answer is read, the
phone hears its own speaker. Whether the browser removes that echo is not
known on the operator's phone, or in the car. The page asks for
`echoCancellation: true`. Chrome on Android may use the platform's echo
canceller for any output, and a car's hands-free unit usually cancels its
own speaker, but neither is measured (M1).

| choice | pros | cons |
|---|---|---|
| **A. Keep the microphone open while reading; drop an utterance whose text matches the sentences just read** (recommended) | "Philippe, stop" works over the reading. The guard holds even where the browser cancels nothing: the node knows what it said. | An echo the canceller leaves still costs an STT call. A reader who quotes the answer back is dropped (rare). |
| B. Mute the microphone while reading; a tap stops it, as today | No echo at all. | Commands cannot be spoken during the reading, the very case the request names. |
| C. A, plus lowering the reading's volume while speech is heard | The reader hears that they are heard. The canceller has less to remove. | Every word a passenger says ducks the reading. |

**Recommendation: A**, measured by M1; C only if M1 shows the canceller
failing. Default: A.

## 5. Decision 3: what each command does

| said | while reading | while writing | otherwise |
|---|---|---|---|
| Philippe (alone) | pauses, and takes the next utterance without the name (5 s) | the same | takes the next utterance without the name (5 s) |
| Philippe, wait / hold on / un momento / aspetta / attends | pauses at once; the answer keeps being written and its sentences synthesised | the reading will start paused | nothing |
| Philippe, resume / go on / continua / vas-y | the paused sentence read again from its start (never mid-word) | — | nothing |
| Philippe, stop / basta / stop / arrête | the reading ends | Abort, as the button does (#47) | nothing |
| Philippe, `<anything else>` | the reading ends and it is asked: a follow-up (§6) | Abort, then ask it | asked |

- **A command is a short closed list**, kept in a table on the node:
  three languages, the words above, and any others M2 shows the operator
  uses. Anything not in the list is a question.
- **CONTINUATION:** a question that pauses mid-sentence arrives in two
  clips, and the second does not start with the name. For 5 s after an
  addressed clip, the next clip joins it, with no name needed. The same
  window follows the end of an answer, for a follow-up.
- **MATCHING ON THE NODE:** a Louise endpoint, not the OpenAI one,
  `POST /ask/voice/heard`. It answers `{addressed, command, text}`, and
  for an unaddressed clip it returns no text, so the name list, the
  commands and the echo guard live in one tested place.

## 6. Decision 4: the follow-up question

**What it is:** "Philippe, and his brother?" needs the previous question
and answer. That is true of the search as much as of the answer. Today
neither gets them. This is the same feature as a typed follow-up, so it
is built once, for both.

| choice | pros | cons |
|---|---|---|
| **A. The page sends the prior turns; the chat model rewrites the follow-up into a standalone search query** (recommended; the standard "condense question" step, e.g. LangChain's ConversationalRetrievalChain) | The search finds what the follow-up is about. The answer sees the conversation (the API already keeps 6 turns). | One more chat call, about 1–2 s, before the search. A rewrite can drift, so the query used is shown on the page. |
| B. Search with the previous question and the follow-up joined | No model call. | It drifts with every turn, and "his brother" still finds nothing. |
| C. No new search: answer from the previous sources | The fastest. | Wrong whenever the follow-up needs other sources. |
| Status quo: each question stands alone | Nothing to build. | "and his brother?" is answered about nothing. |

**Recommendation: A, as its own step** (§9, step 3), because the typed
Ask needs it too. Default: A.

## 7. Decision 5: covering the wait

**What it is:** between the end of a question and the first sentence of
its answer there is silence:
- the search, measured at 13.0 s, of which 11.9 s is the rerank;
- then the answer's first words;
- then the first sentence's synthesis. OmniVoice makes a clip in 1.5–2×
  its length (speech-latency.md).

With the screen not watched (hands-free, the car), that silence reads as
"it did not hear me". A short spoken phrase says the question was taken
and is being worked on: "Vediamo…", "Let me check", "Mmm, let's see what
I can find on this".

| choice | pros | cons |
|---|---|---|
| **A. Phrases from a fixed set, made ONCE per voice and kept** (recommended) | Played the instant the question is taken, in the reader's own voice (the same profile as the answer). Nothing is synthesised while the search runs, so nothing competes for the card with the rerank. Each clip is made once and kept like every other clip (app/speech.py, keyed on the engine since #937), and a clip that sounds wrong is dropped from the set. | Generic: it cannot name the topic. A set heard often repeats, so it rotates, at most three a question. |
| B. A phrase that names the topic ("let's see what I can find on Napoleon"), synthesised per question | Sounds attentive. | Synthesis at 1.5–2× real time, on the card the search is using, so the phrase arrives late and slows the search. Taking the topic out of the question needs the chat model, or it takes the wrong words. |
| C. A sound, not words: a soft tone, or a looping "thinking" sound (the earcon of the commercial assistants) | Language-free, never repetitive in meaning, the smallest clips. | Says less than words. A loop over 13 s is "waiting music" in the bad sense. |
| Status quo: silence | Nothing to build. | In hands-free and in the car there is nothing to see, so the reader repeats the question. |

**Recommendation: A**, with C's single tone as the very first cue where
the reader turned phrases off. Default if the operator says nothing: A.

How A runs:
- **THE SET:** a small text file per language beside the commands' table.
  Each phrase is tagged by when it may be said:
  - at once (under 1 s): "Vediamo…", "Let me check", "Voyons…";
  - a thinking sound for the middle of the wait: "Mmm…", "Uhm, un
    attimo…";
  - a longer one for a long wait (about 2 s): "Sto cercando quello che
    trovo sull'argomento", "Still looking, one moment".
  - Nothing claims a result ("found it") before there is one.
  - The disfluencies ("uhm", "mmm") are what TTS renders worst, so each
    clip is heard once before it joins the set (M5).
- **WHEN:** the cue plan, at most three a question:
  - the "at once" phrase when the question is taken (hands-free: when
    `/ask/voice/heard` says it was addressed; tap-to-speak: when the
    transcript comes back; a typed question with the speaker on: at Ask);
  - a thinking sound at about 5 s if no sentence is ready;
  - the long phrase at about 11 s.
  - The answer's first sentence waits for the phrase that is playing
    (each is under 2 s), never cutting it.
  - The language is the answer's.
  - No phrase is said twice in a row (across questions too).
- **MADE IN ADVANCE:** the page asks the node for the set's clips (one
  Louise route, `/ask/voice/fillers?lang=`). The node makes the missing
  ones at low priority when the card is free, such as on the first use of a
  voice, and the page prefetches them when the microphone or the speaker
  is first used. A clip not made yet is simply not played: never made on
  demand during a search.
- **THE ECHO GUARD** (Decision 2A) knows the set, so a phrase heard back
  is never taken for the reader.
- **A PERSONAL CHOICE:** on by default for spoken questions. Settings ›
  voice turns the phrases off (C's tone instead), with the account, else
  in this browser. Nothing asks.

## 8. What is measured before each step is called done

- **M1 echo.** Hands-free on, on the phone's speaker and in the car:
  - utterances heard from the node's own voice: 0 after the guard;
  - "Philippe, stop" said over a reading: understood in at least 19 of 20.
- **M2 the name.** 50 addressed utterances in Italian, French and English,
  plus 30 min of ordinary talk and car radio:
  - called in at least 95 %;
  - at most one false call an hour;
  - the forms the STT writes, which seed the list.
- **M3 latency.**
  - From the end of "stop" to silence: target under 1.5 s. The 1.5-s
    end-of-speech wait is most of it; whether hands-free can use a
    shorter one is read from M3.
  - From the end of a question to its search starting, against today's
    tap.
- **M4 cost, per hour of car radio:**
  - STT seconds;
  - phone battery;
  - mobile data;
  - the node's corpus work: it must keep running (§9, rule).
- **M5 the phrases** (Decision 5): every clip of the set heard once in
  each voice used, and a clip that sounds wrong dropped. Measured on 10
  spoken questions:
  - the time from the question taken to the first phrase (target under
    1 s);
  - the share of the silence the cues cover;
  - how much the first sentence was delayed by a phrase still playing;
  - the operator's verdict on repetition.

## 9. The steps

1. **Hands-free, the name and the commands** (Decisions 1A, 2A, 3):
   - a hands-free switch beside the microphone;
   - the page keeps the microphone and cuts utterances with its energy
     gate, then `POST /ask/voice/heard`;
   - the node runs VAD, STT, the name, the commands, the echo guard and
     continuation;
   - the page pauses, resumes, stops or asks;
   - the Screen Wake Lock API keeps the screen on while hands-free (a
     page whose screen is off may lose its microphone on Android: M1
     checks it).
   - **THE RULE THIS CHANGES:** an open microphone no longer counts as
     activity. Only an addressed clip and the reading post `/ask/active`.
     Otherwise hands-free would hold the node's corpus work for as long
     as the switch is on.
   - **PRIVACY:** an unaddressed clip is transcribed in memory and
     dropped. Its text is never logged, kept, traced or returned. The
     voice report (`/ask/voice/report`) carries counts only.
   - Tests: the name forms, the commands, continuation, the echo guard
     and the activity rule in Python; the page's states in
     tests/browser/ask_voice.js.
2. **The phrases that cover the wait** (Decision 5A). They do not need
   hands-free: they serve tap-to-speak and a typed question with the
   speaker on as well. So they can ship first, alone, measured by M5.
   - Tests: the cue plan (when each phrase plays, never twice in a row,
     never during a sentence, at most three), the set file per language,
     the route making only the missing clips at low priority; the page's
     cues in tests/browser/ask_voice.js.
3. **The follow-up** (Decision 4A), for the typed and the spoken Ask
   alike.
4. **Only if M2 or M4 fail:** a wake-word model (Decision 1B, openWakeWord
   over Wyoming), a new dependency to ask for first.

**The cost of step 1 in the car:** with the microphone open, the car stays
in its call profile. The 5-s switch happens once, not at each question.
But the reading goes through the car in call quality (mono, 16 kHz at
best), not in its media quality as today. M1 hears it.
