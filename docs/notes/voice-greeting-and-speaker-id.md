# Greeting the reader by name, and what recognising a speaker would cost

*Design note, 2026-11-28. Nothing here is built yet. Part 1 is a design.
Part 2 is a theoretical estimate only: no implementation, and every figure
is published or inferred, never measured on this node.*

The operator, 2026-11-28:

> 1. On the first voice query of the day the comfort phrase can be "Hi
>    <username>", in the appropriate language
> 2. Evaluate the cost of recognizing the identity of the speaker from their
>    voice pattern; only a theorical estimate, no implementation.

## 1. "Ciao Anna" on the first spoken question of the day

**What it is:** the SHORT phrase that covers a spoken question's wait
(hands-free-voice.md §7, Decision 5A) is replaced, once a day per account,
by a greeting that names the reader in the answer's language:

- "Ciao Anna, vediamo…"
- "Hi Anna, let me check."
- "Hallo Anna, mal sehen…"
- "Salut Anna, voyons…"
- "Hola Anna, a ver…"

Who the reader is comes from the token: `auth.account(viewer)`, the users.toml
account. Nothing is recognised from the voice; part 2 is that option.

### 1.1 The rules

| rule | what it says |
|---|---|
| who is greeted | An account, i.e. a user or admin of users.toml. The admin token (`master`), the depositor and a guest have no name, so they hear the ordinary SHORT phrase. |
| "of the day" | The reader's own calendar day. The page sends its local date (`Intl`) with the phrase request, so the node needs no time zone. Kept per account in runtime state (`state/greeted.json`, `{account: "YYYY-MM-DD"}`): it is not desired state (invariant 10), like `users-seen.json`. |
| when it counts as said | When the page reports the clip PLAYED (`POST /ask/voice/fillers/used`), never when it is served. A greeting prefetched and not played (no spoken question came) leaves the day ungreeted. Two devices of one account: the first one played wins, and the second gets the ordinary phrase. |
| the name said | A new `spoken_name` in the account, for the name as it is said. It defaults to the account name with its first letter capitalised and `.` `_` `-` read as spaces (`anna` → "Anna"). The reader may change it in Settings beside the voice (RULE 2: a name only they know, and nothing asks). |
| the setting | It follows "phrases while waiting". Off means the tone, and no greeting either. There is no new switch. |
| the voice | The one the answer is read in (`speech.reader_voice`). A regular user hears the language's synthetic voice, as with every phrase. |
| the texts | `app/filler_phrases/<lang>.toml` gains a third kind, `greeting`, of texts with one `{name}` each. `fillers.load` checks them like the others: one placeholder, a short phrase after the name, and no claimed result. |
| the echo guard | `fillers.texts(lang)` also returns each account's greeting with its name filled in, so a greeting heard back is never taken for the reader. |
| the clip | Made in advance, like the pool. There is one GREETING clip per account, voice and language, made the first time that account's phrases are asked for in that language. It is remade from another text after it is played, at low priority (the pool's rules: never during a search). It lives under `LOUISE_CACHE_DIR`, keyed on the voice's identity, the engine and the account. It is deleted when the account is removed, its `spoken_name` or voice changes, or the engine swaps. |
| missing | A greeting not made yet, or a voice that cannot be identified, gives the ordinary SHORT phrase, else the tone. A greeting is never synthesised while the reader waits. |

### 1.2 What it costs

- **Synthesis:** a greeting is about 1.5 s of speech. At OmniVoice's
  1.5–2× real time (speech-latency.md) that is 2–3 s of synthesis per
  account and language per day, at low priority. On a household node
  (a few accounts, one or two languages each) that is under a minute a day.
- **Wait:** none added. It is the SHORT slot. It is 0.5 s longer than a
  SHORT phrase and under the LONG's 2.5 s, and the first sentence already
  waits for the phrase that is playing.
- **Build:** the `greeting` kind and its checks, `spoken_name` (users.toml,
  Settings, `louise user`), the day record, the route's choice of greeting
  over SHORT, and the echo guard's per-account texts. Tests in
  tests/test_voice.py, tests/test_fillers.py and tests/browser/ask_voice.js.

### 1.3 The spoken name, the one choice

| choice | pros | cons |
|---|---|---|
| **A. Derived from the account name, changeable in Settings (recommended)** | Works on day one with nothing asked. The reader fixes it once if it is wrong ("a.rossi" → "Anna"). | A slug may read badly until changed. |
| B. The account name exactly as written | Nothing to build. | "a.rossi", "user2": said as written, often wrong. |
| C. Asked at the first greeting | Always right. | A form for something the system can propose (RULE 2). |

If the operator says nothing: A.

## 2. Recognising the speaker from the voice: a cost estimate

**What it is:** telling WHO is speaking from the sound of the voice
(text-independent speaker identification), so that a device several people
share — a car's tablet, the admin token on the kitchen phone — greets and
reads to the right person.

**What it could never be:** a way in. The node clones voices (its own voice
profiles are made from a reader's recordings), so anyone could play
"Anna" to it. A replay or any TTS does the same. Speaker identity could
choose a greeting, a voice and the reader's phrase setting. It could never
choose a token, a role, a collection or a hidden annotation. The token stays
the only authority (07 §7.4).

### 2.1 How it would run

1. The spoken question's audio is already on the node for speech to text
   (stt-on-igpu.md). A speaker-embedding model turns it into one vector, of
   192–256 numbers.
2. That vector is compared with each enrolled reader's vector (a cosine).
   The best one is taken only if it clears a threshold and beats the second
   by a margin. Otherwise the speaker is "unknown", which means the ordinary
   phrase and the token's settings.
3. Enrolment: three to five short sentences read once in Settings (about
   20–30 s of speech) give the reader's vector. It is stored locally, and
   only with the reader's explicit consent (§2.4).
4. Where it would run: on the CPU, in a small sidecar reached over HTTP
   (invariant 7: the app imports no model runtime), beside the STT and in
   parallel with it. The standard tool is sherpa-onnx, whose speaker
   identification runs the WeSpeaker and 3D-Speaker models exported to ONNX.
   Never in the web process, which has its own 1 GB budget.

### 2.2 The models, as published

| model | parameters | GFLOPs* | RTF* | EER, VoxCeleb1-O |
|---|---|---|---|---|
| CAM++ (3D-Speaker) | 7.2 M | 1.7 | 0.026 | ≈ 0.7 % |
| ECAPA-TDNN (C = 1024) | 14.7 M | 4.0 | 0.042 | 0.86–0.89 % |
| ResNet34 (WeSpeaker) | 6.6–6.7 M | 6.8 | 0.057 | 0.97–1.05 % |

*From the LightCAM and CAM++ papers' tables. The input length behind the
FLOPs and the hardware behind the real-time factor are each paper's own, so
the columns compare models with each other, not with this node. The ONNX
exports are 26–221 MB on disk; CAM++'s is about 30 MB, and about 28 MB as
int8.

### 2.3 The estimate for this node

| cost | estimate | how it was inferred |
|---|---|---|
| compute per spoken question | 0.1–0.2 s of one CPU core (CAM++, a 4–6 s question) | RTF 0.026 × the question's length. A desktop core runs ONNX at tens of GFLOPS. |
| added wait | none where it runs in parallel with STT (STT takes longer); ≤ 0.2 s if run after it | §2.1 step 4 |
| GPU | none | CPU only |
| memory | ≈ 30 MB of weights, ≈ 100 MB for the sidecar process | the ONNX file plus the runtime |
| storage per reader | ≈ 1 KB (one vector) | 192–256 floats |
| enrolment | 20–30 s of speech per reader, once | three to five sentences |
| comparison | N cosines of 192 numbers: nothing | N readers ≤ 10 |
| deploy | one container: memory cap, restart policy, health check, model file pinned by digest | LOUISE SURVIVES A CRASH WITHOUT AN AGENT |
| build | the sidecar, the enrolment page in Settings, the threshold, the greeting's choice of reader, deletion with the account, and tests | §2.1 |

**Accuracy, the real cost.** The published 0.7–1 % equal error rate is on
clean interview speech of about 8 s. A spoken question is 2–6 s, often from
a phone or a car microphone in noise, and household voices are alike
(siblings, a parent and child). Short tests and noise are commonly reported
to multiply the error rate several times. Our inference, to be measured:
with the threshold set for about 1 % wrong names, perhaps 5–15 % of
questions would come back "unknown", and would get the ordinary phrase. A
wrong name ("Ciao Marco" to Giulia) is the error a reader notices. So the
threshold favours "unknown", and the cost of a miss is only a greeting not
said.

**Measured before any decision** (measuring.md): the household's own voices
— enrolment, then 50 spoken questions each from the phone and the car —
through the deployed STT path. Count wrong names, "unknown" and the time
added. Without that row, the figures above are inference.

### 2.4 Privacy

A vector made to identify a person by their voice is biometric data: a
special category under the GDPR (Art. 9). It needs the reader's explicit
consent, and possibly a data-protection impact assessment (Art. 35).

A household's own node may fall under the household exemption (Art.
2(2)(c)). A guest's voice is still not theirs to enrol. So:
- enrolment is opt-in per account, never passive: a reader is not enrolled
  from their questions;
- the vectors stay on the node and are never in a bundle, a SHARD or a
  peer's exchange;
- they are deleted with the account or on request.

The node already holds voice recordings (the cloned profiles), but those are
kept to speak, not to identify.

### 2.5 The choice

| choice | pros | cons |
|---|---|---|
| **A. No speaker identification; the account is the token (status quo, recommended)** | Nothing to build or deploy. No biometric data. No wrong names. Part 1 greets every reader with an account. | A shared device greets its token's owner whoever speaks, and the admin token greets nobody. |
| B. Speaker identification for the greeting and the reader's own settings only | Greets and reads to the right person on a shared device. ≈ 0.1–0.2 s of one core and ≈ 100 MB per question path, no GPU. | A sidecar to run, enrolment, Art. 9 consent, about 1 % wrong names at the chosen threshold and 5–15 % "unknown" (estimated), and a measurement to make first. |
| C. Speaker identification as a login | Hands-free sign-in. | Defeated by the node's own voice clones and by any replay: refused. |

If the operator says nothing: A, with part 1 built on the token. B pays only
if a shared device is a real case here, and then the measurement in §2.3
comes first.

## Sources

- CAM++: <https://arxiv.org/abs/2303.00332>
- LightCAM (complexity table): <https://arxiv.org/abs/2402.06073>
- 3D-Speaker toolkit: <https://arxiv.org/abs/2403.19971>
- WeSpeaker pretrained models: <https://wenet-e2e.github.io/wespeaker/pretrained.html>
- sherpa-onnx speaker models: <https://huggingface.co/csukuangfj/speaker-embedding-models>
