# Hands-free voice: the microphone left on, Philippe called by name

*2026-11-26. The operator: "I would like to be able to keep the mic on,
summoning the chat by starting a phrase with its name 'Philippe...', and
possibly pause or stop it 'Philippe wait/stop/hold on', resume, or ask a
follow up question." This is a design to evaluate. Nothing has been built
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
and is measured in a day (§7). B is the standardized alternative, taken
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

**Recommendation: A, as its own step** (§8, step 2), because the typed
Ask needs it too. Default: A.

## 7. What is measured before step 1 is called done

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
  - the node's corpus work: it must keep running (§8, rule).

## 8. The steps

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
2. **The follow-up** (Decision 4A), for the typed and the spoken Ask
   alike.
3. **Only if M2 or M4 fail:** a wake-word model (Decision 1B, openWakeWord
   over Wyoming), a new dependency to ask for first.

**The cost of step 1 in the car:** with the microphone open, the car stays
in its call profile. The 5-s switch happens once, not at each question.
But the reading goes through the car in call quality (mono, 16 kHz at
best), not in its media quality as today. M1 hears it.
