"""媒体时长探测：SaveAudio / SaveVideo 的节点 metadata 常常不带 duration，
页面上就只剩一个播放器、看不到「几秒」。这里补一条兜底：

1. 有 ffprobe 就直接问它（最准，视频/音频/各种容器都认）；
2. 没有 ffprobe：wav 读头算，mp3 按首帧码率估算（只用来显示「约 N 秒」）。

失败一律返回 None，绝不让探测拖累任务收尾。
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

_FFPROBE = shutil.which("ffprobe")


def _via_ffprobe(path: Path) -> float | None:
    if not _FFPROBE:
        return None
    try:
        out = subprocess.run(
            [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0:
            value = float((out.stdout or "").strip())
            return value if value > 0 else None
    except Exception:  # noqa: BLE001 - 探测失败只是少一个数字
        return None
    return None


def _via_wav(path: Path) -> float | None:
    try:
        with path.open("rb") as fh:
            head = fh.read(44)
            if len(head) < 44 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                return None
            channels, rate = struct.unpack("<HI", head[22:28])
            bits = struct.unpack("<H", head[34:36])[0]
            size = path.stat().st_size
            if not channels or not rate or not bits:
                return None
            return size / (channels * rate * bits / 8)
    except Exception:  # noqa: BLE001
        return None


_MP3_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _via_mp3(path: Path) -> float | None:
    """按第一个有效帧的码率估：够显示「约 18 秒」，不追求帧级精确。"""
    try:
        data = path.read_bytes()[:65536]
        i = 0
        while i < len(data) - 4:
            if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
                ver = (data[i + 1] >> 3) & 0x03
                layer = (data[i + 1] >> 1) & 0x03
                br_i = (data[i + 2] >> 4) & 0x0F
                sr_i = (data[i + 2] >> 2) & 0x03
                if layer == 1 and br_i and sr_i != 3 and ver in _MP3_RATES:
                    kbps = _MP3_BITRATES[br_i] if br_i < len(_MP3_BITRATES) else 0
                    if kbps:
                        return path.stat().st_size * 8 / (kbps * 1000)
            i += 1
    except Exception:  # noqa: BLE001
        return None
    return None


def duration(path: str | Path) -> float | None:
    """返回秒数；探测不出来给 None。"""
    p = Path(path)
    if not p.is_file():
        return None
    got = _via_ffprobe(p)
    if got:
        return round(got, 2)
    suffix = p.suffix.lower()
    if suffix == ".wav":
        got = _via_wav(p)
    elif suffix == ".mp3":
        got = _via_mp3(p)
    else:
        got = None
    return round(got, 2) if got else None
