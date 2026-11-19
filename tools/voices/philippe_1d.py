"""Philippe candidate 1d: sample 1a with longer pauses between the sentences.
Each sentence pause keeps ALL of 1a's own pause audio (the speech tails at its
start and the breath before the next sentence at its end, untouched); the extra
length is inserted in the MIDDLE of the pause, from the quietest stretch of his
own pauses (3.90-4.10 s, -62 to -65 dBFS), joined with 10 ms crossfades.
Pauses: 780 -> 1000 ms after "vittime?", 620 -> 1000 ms after "dell'arte?"."""
import array
import wave

w = wave.open("orig.wav")
sr = w.getframerate()
x = array.array("h", w.readframes(w.getnframes()))
XF = int(0.010 * sr)


def seg(a, b):
    return x[int(a * sr):int(b * sr)]


def join(parts):
    out = array.array("h", parts[0])
    for p in parts[1:]:
        p = array.array("h", p)
        for i in range(XF):
            t = (i + 1) / (XF + 1)
            out[len(out) - XF + i] = int(out[len(out) - XF + i] * (1 - t) + p[i] * t)
        out.extend(p[XF:])
    return out


QUIET = (3.90, 4.10)          # 200 ms of his quietest room tone


def filler(ms):
    """`ms` of his quiet room tone, the 200 ms stretch repeated with crossfades."""
    parts, left = [], ms / 1000
    while left > 0.001:
        take = min(left + XF / sr, QUIET[1] - QUIET[0])
        parts.append(seg(QUIET[0], QUIET[0] + take))
        left -= take - XF / sr
    return join(parts)


def longer(a, mid, b, target_ms):
    """the pause a..b, its own audio kept, `target - (b - a)` added at `mid`."""
    extra = target_ms - (b - a) * 1000 + 2 * XF / sr * 1000
    return join([seg(a, mid), filler(extra), seg(mid, b)])


s1, s2, s3 = seg(0.32, 3.85), seg(21.54, 23.86), seg(24.48, 27.69)
p1 = longer(3.85, 4.20, 4.63, 1000)     # tail of "vittime?" (to ~3.95) and lead-in kept
p2 = longer(23.86, 24.20, 24.48, 1000)  # the sound at 23.96-24.06 kept
y = join([s1, p1, s2, p2, s3])
o = wave.open("candidate-1d.wav", "wb")
o.setnchannels(1)
o.setsampwidth(2)
o.setframerate(sr)
o.writeframes(y.tobytes())
o.close()
open("candidate-1d.txt", "w", encoding="utf-8").write(open("candidate-1a.txt", encoding="utf-8").read())
print("1d", round(len(y) / sr, 2), "s; pauses", round(len(p1) / sr * 1000), "ms and", round(len(p2) / sr * 1000), "ms")
