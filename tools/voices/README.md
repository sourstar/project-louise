# Voice reference clips, as built

Each script here rebuilds a live voice reference **byte for byte** from the
original recording. Each one is kept exactly as it was run (its sha256 below),
so the hashes prove the rebuild. The general tool is `tools/voice_reference.py`:
its `assemble --sentence-pause S` does what these scripts did by hand, for any
voice, though not to the same bytes.

## philippe_1d.py — Philippe, live since 2026-11-11 ~09:10Z (operator: "Deal, I like it")

| file | sha256 |
|---|---|
| philippe_1d.py (this script) | `875b10ad853c002295ea4a0128872f8554830d9ef6c2095256beb7fb048de9fe` |
| input `orig.wav`: the profile's `reference-orig-27.75s.wav`, 24 kHz mono s16 | `5850273a85cb2bdba2f4d7cb72c682de30ef8d1274a67b946237aa49be3439a6` |
| output `candidate-1d.wav` = the profile's `reference.wav` / `reference-10s-1d.wav` | `843d9c7225c8ae31919f6fa9494f6bfae6bfe25c20292d82d3d2911bff3b3406` |

It is run in a directory that holds `orig.wav` and `candidate-1a.txt`. That
text file holds the transcript, one line: "Siamo oggi ancora protagonisti,
oppure siamo vittime? L'Italia è ancora il centro mondiale dell'arte?
Sicuramente non nel campo dell'arte contemporanea." The output is 11.04 s:

- original 0.32–3.85 s;
- his pause 3.85–4.63 s (780 ms), lengthened to 1,010 ms;
- original 21.54–23.86 s;
- his pause 23.86–24.48 s (620 ms), lengthened to 1,010 ms;
- original 24.48–27.69 s.

Each pause keeps all of its own audio. The extra length is his quietest room
tone, 3.90–4.10 s, inserted mid-pause with 10 ms linear crossfades. The times
were chosen by hand (München, 2026-11-11), from the tool's candidate 1, using
the speaker's own pause after "vittime?". That pause choice is now the tool's
rule at every join.
