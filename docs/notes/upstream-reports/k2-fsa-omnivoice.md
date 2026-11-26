# k2-fsa/OmniVoice — the fade that cuts a sentence's last word (2026-11-26)

*For the filer. NOT a new issue: upstream knows the symptom — #116
(closed; the maintainer: the 0.1 s fade-out "may also contain meaningful
verbal segments", and the root is training samples "truncated at the end")
and #245 (open, stale; "Setting fade_duration=0.0 may address it in some
cases"). Neither has a fix, and the maintainer's objection to removing the
fade is that a clip then ends "with a sudden stop of speech". Ours answers
that objection, so it is written as a COMMENT on #245, ready to post as it
is. Our implementation is the C++ port's
(`deploy/localai/patches/staged/0030-llama-cpp-omnivoice-fade-out-ends-with-the-kept-silence.patch`,
docs/notes/omnivoice-on-our-backend.md §15); the comment describes the same
change in the reference's Python terms and names no project of ours.*

<!-- comment:k2-fsa/OmniVoice#245 -->
A fix for the fade's part of this, which keeps the fade where it is harmless: let the fade-out last only as long as the trailing silence that `remove_silence` kept.

As `_post_process_audio` runs today, `remove_silence` trims the trailing silence to at most 100 ms, and `fade_and_pad_audio` then fades the last `fade_duration` (0.1 s) to zero whatever those samples hold. When the audio ends at its last word — fast speech, or one of the endings learned from truncated training samples (#116) — the trim leaves 0–40 ms of silence and the fade runs over the word. Measured on 40 clips of a C++ port of the same post-processing: in 30 the fade attenuated speech, 24 of them with under 40 ms of their own silence, and one lost a whole syllable.

The change:

- `remove_silence` reports how much trailing silence it kept: the trailing run below the threshold (in the 10 ms chunks it already scans), less what it trimmed — at most `trail_sil_ms`.
- `fade_and_pad_audio` takes the fade-out's length apart from the fade-in's.
- The fade-out is `max(min(kept, fade_duration), 5 ms)`: never longer than the silence it fades, never shorter than 5 ms, so a cut at a sample is never a click (the "sudden stop" of `fade_duration=0.0`).

What it does:

- a clip with ≥ 100 ms of its own silence is faded exactly as today, to the byte (the fade-out is the same sample count as the fade-in);
- a clip that ends at its word keeps the word, with a 5 ms ramp;
- the fade-in, the trim, the level and the pad are unchanged.

Checked on that port: a tone that runs to 20 ms before the end is not attenuated at all (today's fade attenuates 80 ms of it); a clip with 150 ms of silence is byte-identical to today's; and of three sentences at a fixed seed, the one whose audio ended in speech (-27.6 dBFS over its last 100 ms) had its fade go from 100 ms (81 % of that energy lost) to 5 ms.

It does not touch the other part of #116: an ending the model itself generates cut short is still cut short — only no longer faded on top. That one is the training data's, as you say.
<!-- /comment -->
