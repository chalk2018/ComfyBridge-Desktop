"""参考音频合成引擎：给「参考生音乐 / 音乐翻唱」提供真实可播的音频参考文件。

不依赖采样库，全部用振荡器 + 包络 + 简易混响离线合成，输出 44.1k 立体声 mp3。
每个 spec 决定曲风、BPM、调性、配器与段落，因此提示词里写的「古筝 + 78BPM + D 羽」
和实际文件完全一致。
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
FFMPEG = "D:/ComfyUI-Desktop/Comfy-Desktop/tools/ffmpeg/bin/ffmpeg.exe"
NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
        "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]
DORIAN = [0, 2, 3, 5, 7, 9, 10]
PENTA = [0, 2, 4, 7, 9]
PENTA_MIN = [0, 3, 5, 7, 10]
YO = [0, 2, 5, 7, 9]          # 中国五声「羽」色彩
MIYAKO = [0, 1, 5, 7, 8]      # 都节音阶


def hz(root: str, octave: int, semi: int = 0) -> float:
    n = NOTE[root] + semi + (octave - 4) * 12
    return 440.0 * (2 ** ((n - 9) / 12))


def env(n: int, a=0.01, d=0.1, s=0.7, r=0.2, sr=SR) -> np.ndarray:
    a_n, d_n, r_n = int(a * sr), int(d * sr), int(r * sr)
    s_n = max(1, n - a_n - d_n - r_n)
    parts = [np.linspace(0, 1, max(1, a_n), endpoint=False),
             np.linspace(1, s, max(1, d_n), endpoint=False),
             np.full(s_n, s),
             np.linspace(s, 0, max(1, r_n))]
    e = np.concatenate(parts)
    if len(e) < n:
        e = np.pad(e, (0, n - len(e)))
    return e[:n]


def adsr_pluck(n: int, decay=0.35, sr=SR) -> np.ndarray:
    t = np.arange(n) / sr
    return np.exp(-t / max(0.01, decay))


def osc(kind: str, f: float, n: int, phase=0.0) -> np.ndarray:
    t = np.arange(n) / SR
    ph = 2 * np.pi * f * t + phase
    if kind == "sine":
        return np.sin(ph)
    if kind == "saw":
        out = np.zeros(n)
        for k in range(1, 9):
            out += np.sin(ph * k) / k
        return out * 0.55
    if kind == "square":
        out = np.zeros(n)
        for k in range(1, 10, 2):
            out += np.sin(ph * k) / k
        return out * 0.7
    if kind == "tri":
        out = np.zeros(n)
        for k in range(1, 12, 2):
            out += ((-1) ** ((k - 1) // 2)) * np.sin(ph * k) / (k * k)
        return out * 0.9
    return np.sin(ph)


def karplus(f: float, n: int, damp=0.996, bright=0.5) -> np.ndarray:
    """Karplus-Strong 拨弦：古筝 / 吉他 / 竖琴都用它。"""
    size = max(2, int(SR / f))
    buf = np.random.RandomState(int(f * 7) % 99991).uniform(-1, 1, size)
    buf = np.convolve(buf, [bright, 1 - bright], mode="same")
    out = np.zeros(n)
    idx = 0
    for i in range(n):
        out[i] = buf[idx]
        nxt = (idx + 1) % size
        buf[idx] = damp * 0.5 * (buf[idx] + buf[nxt])
        idx = nxt
    return out


def kick(n: int, f0=110, f1=45, decay=0.22) -> np.ndarray:
    t = np.arange(n) / SR
    f = f1 + (f0 - f1) * np.exp(-t / 0.03)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR)
    return body * np.exp(-t / decay)


def noise(n: int, seed=0) -> np.ndarray:
    return np.random.RandomState(seed).uniform(-1, 1, n)


def snare(n: int, seed=1) -> np.ndarray:
    t = np.arange(n) / SR
    body = noise(n, seed) * np.exp(-t / 0.12)
    tone = np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.07) * 0.5
    return (body * 0.8 + tone) * 0.55


def hat(n: int, seed=2, decay=0.045) -> np.ndarray:
    t = np.arange(n) / SR
    x = noise(n, seed)
    x = np.diff(np.concatenate([[0], x]))  # 高通化
    return x * np.exp(-t / decay) * 0.35


def taiko(n: int, f=72) -> np.ndarray:
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * f * t) * 0.9 + noise(n, 3) * 0.15) * np.exp(-t / 0.28)


def reverb(x: np.ndarray, wet=0.28, size=1.0, seed=7) -> np.ndarray:
    """合成脉冲响应做卷积混响，够用且不需要外部依赖。"""
    ir_len = int(SR * 1.4 * size)
    t = np.arange(ir_len) / SR
    ir = noise(ir_len, seed) * np.exp(-t / (0.35 * size))
    ir[: int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))
    ir /= np.abs(ir).sum() / 8
    n = len(x) + ir_len - 1
    nfft = 1 << (n - 1).bit_length()
    y = np.fft.irfft(np.fft.rfft(x, nfft) * np.fft.rfft(ir, nfft), nfft)[: len(x)]
    return x * (1 - wet) + y * wet


class Track:
    def __init__(self, n: int):
        self.buf = np.zeros(n, dtype=np.float64)

    def add(self, at: int, sig: np.ndarray, gain=1.0) -> None:
        if at >= len(self.buf):
            return
        end = min(len(self.buf), at + len(sig))
        self.buf[at:end] += sig[: end - at] * gain


def build(spec: dict) -> tuple[np.ndarray, np.ndarray]:
    rnd = np.random.RandomState(spec.get("seed", 1))
    bpm = spec["bpm"]
    beat = 60.0 / bpm
    bars = spec.get("bars", 16)
    total = int(SR * beat * 4 * bars) + SR // 2
    scale = {"major": MAJOR, "minor": MINOR, "dorian": DORIAN, "penta": PENTA,
             "penta_min": PENTA_MIN, "yo": YO, "miyako": MIYAKO}[spec.get("scale", "minor")]
    root = spec.get("root", "D")
    oct_ = spec.get("octave", 3)

    ch = spec.get("progression") or [0, 5, 3, 4]
    chord_len = 4  # 每和弦一小节

    L, R = Track(total), Track(total)
    drums = spec.get("drums", "none")
    style = spec.get("style", "cinematic")

    # ---- 和声铺底 ----
    pad_kind = spec.get("pad", "saw")
    for b in range(bars):
        deg = ch[(b // chord_len) % len(ch)]
        f0 = hz(root, oct_, scale[deg % len(scale)] + 12 * (deg // len(scale)))
        start = int(b * 4 * beat * SR)
        dur = int(4 * beat * SR)
        for k, semi in enumerate((0, scale[2], scale[4])):
            f = f0 * (2 ** (semi / 12))
            sig = osc(pad_kind, f, dur) * env(dur, a=0.35, d=0.4, s=0.65, r=0.9)
            sig += osc(pad_kind, f * 1.003, dur) * env(dur, a=0.4, d=0.4, s=0.6, r=0.9) * 0.6
            g = spec.get("pad_gain", 0.16) * (0.9 if k else 1.0)
            L.add(start, sig, g * 0.9)
            R.add(start + 90, sig, g)

    # ---- 低音 ----
    for b in range(bars):
        deg = ch[(b // chord_len) % len(ch)]
        f = hz(root, oct_ - 2, scale[deg % len(scale)] + 12 * (deg // len(scale)))
        start = int(b * 4 * beat * SR)
        patt = spec.get("bass_patt", [0, 1.5, 2, 3])
        for i, off in enumerate(patt):
            dur = int(beat * (0.85 if style != "edm" else 0.5) * SR)
            sig = osc("sine" if style != "rock" else "saw", f, dur) * adsr_pluck(dur, 0.5)
            at = start + int(off * beat * SR)
            L.add(at, sig, 0.20)
            R.add(at, sig, 0.20)

    # ---- 琶音 / 拨弦 ----
    arp_on = spec.get("arp", True)
    if arp_on:
        for b in range(bars):
            if spec.get("arp_from", 0) > b:
                continue
            deg = ch[(b // chord_len) % len(ch)]
            base = hz(root, oct_ + 1, scale[deg % len(scale)] + 12 * (deg // len(scale)))
            steps = spec.get("arp_steps", [0, 2, 4, 2, 4, 2, 0, 2])
            for i, s in enumerate(steps):
                f = base * (2 ** (scale[s % len(scale)] / 12)) * (2 if s >= len(scale) else 1)
                dur = int(beat * 0.9 * SR)
                if spec.get("pluck") == "ks":
                    sig = karplus(f, dur, damp=0.997, bright=0.45) * adsr_pluck(dur, 0.5)
                else:
                    sig = osc("tri", f, dur) * adsr_pluck(dur, 0.22)
                at = int((b * 4 + i * 0.5) * beat * SR)
                pan = 0.5 + 0.3 * math.sin(i * 1.7)
                L.add(at, sig, 0.13 * (1 - pan) + 0.04)
                R.add(at, sig, 0.13 * pan + 0.04)

    # ---- 主旋律 ----
    mel_on = spec.get("melody", True)
    if mel_on:
        n_mel = int(bars * 4 * 1.6)
        cur = rnd.randint(0, len(scale))
        for i in range(n_mel):
            if rnd.rand() < 0.22:
                continue
            cur = int(np.clip(cur + rnd.choice([-2, -1, -1, 0, 1, 1, 2]), 0, len(scale) - 1))
            deg = ch[(i // (4 * chord_len)) % len(ch)]
            f = hz(root, oct_ + 2, scale[cur])
            dur = int(beat * rnd.choice([0.5, 0.75, 1.0, 1.5]) * SR)
            kind = spec.get("lead", "sine")
            sig = osc(kind, f, dur)
            if spec.get("vibrato"):
                t = np.arange(dur) / SR
                sig *= (1 + 0.006 * np.sin(2 * np.pi * 5.2 * t))
            sig *= env(dur, a=0.03, d=0.12, s=0.55, r=0.25)
            at = int((i * 0.5) * beat * SR)
            if at >= total:
                break
            L.add(at, sig, spec.get("lead_gain", 0.12))
            R.add(at + 120, sig, spec.get("lead_gain", 0.12))

    # ---- 打击 ----
    if drums != "none":
        for b in range(bars):
            bar = int(b * 4 * beat * SR)
            for beat_i in range(4):
                at = bar + int(beat_i * beat * SR)
                if drums in ("four", "edm", "pop", "rock", "trap"):
                    L.add(at, kick(int(0.35 * SR)), 0.55)
                    R.add(at, kick(int(0.35 * SR)), 0.55)
                if drums in ("pop", "rock") and beat_i in (1, 3):
                    s = snare(int(0.25 * SR), seed=b)
                    L.add(at, s, 0.3)
                    R.add(at, s, 0.3)
                if drums == "trap":
                    for k in range(2):
                        L.add(at + int(k * 0.5 * beat * SR), hat(int(0.06 * SR), seed=b * 7 + k), 0.18)
                if drums in ("four", "edm", "house"):
                    L.add(at + int(0.5 * beat * SR), hat(int(0.05 * SR), seed=b * 5), 0.22)
                if drums in ("taiko", "guofeng"):
                    if beat_i in (0, 2):
                        L.add(at, taiko(int(0.5 * SR)), 0.45)
                        R.add(at, taiko(int(0.5 * SR)), 0.35)
                if drums == "shaker" and beat_i in (1, 3):
                    L.add(at, hat(int(0.12 * SR), seed=b), 0.14)

    l = reverb(L.buf, wet=spec.get("wet", 0.3), size=spec.get("room", 1.0), seed=spec.get("seed", 3))
    r = reverb(R.buf, wet=spec.get("wet", 0.3), size=spec.get("room", 1.1), seed=spec.get("seed", 3) + 11)
    # 首尾淡入淡出
    fade = int(0.25 * SR)
    for a in (l, r):
        a[:fade] *= np.linspace(0, 1, fade)
        a[-fade:] *= np.linspace(1, 0, fade)
    peak = max(1e-6, max(np.abs(l).max(), np.abs(r).max()))
    gain = 0.92 / peak
    return l * gain, r * gain


def write_wav(path: Path, l: np.ndarray, r: np.ndarray) -> None:
    data = np.stack([l, r], axis=1)
    pcm = np.clip(data, -1, 1)
    pcm = (pcm * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def render(spec: dict, out: Path) -> Path:
    l, r = build(spec)
    tmp = out.with_suffix(".wav")
    write_wav(tmp, l, r)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(tmp),
                    "-codec:a", "libmp3lame", "-b:a", "112k", str(out)],
                   check=True)
    tmp.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    specs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(specs, 1):
        p = outdir / (s["key"] + ".mp3")
        if not p.exists():
            render(s, p)
        if i % 20 == 0:
            print("rendered", i, flush=True)
    print("done", len(specs))
