"""Read a picture's size, and a video's size and length, from the file itself.

No ffmpeg (the owner chose not to add it, ADR 0011): pictures are read from
their headers (PNG, GIF, JPEG), videos from the MP4/MOV box structure
(`moov` → `mvhd` for the length, the video track's `tkhd` for the size and
rotation). Phones often store portrait video as landscape plus a 90° turn,
so the turn is applied to report the size as it is shown.

Untrusted input: every read is bounds-checked and anything odd gives None.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

VIDEO_BRANDS = {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V ", b"qt  ", b"MSNV", b"dash", b"mp71"}
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}


def sniff_video(head: bytes) -> str | None:
    """video/mp4 or video/quicktime from the first bytes, or None."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of a PNG, GIF or JPEG, or None."""
    try:
        if data.startswith(b"\x89PNG") and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                # Start-of-frame markers carry the size; C4/C8/CC are other tables.
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[i + 5:i + 9])
                    return width, height
                i += 2 + length
    except struct.error:
        return None
    return None


@dataclass
class VideoInfo:
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = None


def _boxes(f: BinaryIO, start: int, end: int):
    """Yield (type, payload start, payload end) for the boxes between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        head = 8
        if size == 1:                      # 64-bit size follows
            size = struct.unpack(">Q", f.read(8))[0]
            head = 16
        elif size == 0:                    # runs to the end
            size = end - pos
        if size < head or pos + size > end:
            return
        yield kind, pos + head, pos + size
        pos += size


def video_info(path: Path) -> VideoInfo:
    info = VideoInfo()
    try:
        with open(path, "rb") as f:
            total = path.stat().st_size
            moov = next(((s, e) for k, s, e in _boxes(f, 0, total) if k == b"moov"), None)
            if not moov:
                return info
            _read_moov(f, *moov, info)
    except (OSError, struct.error):
        pass
    return info


def _read_moov(f: BinaryIO, start: int, end: int, info: VideoInfo) -> None:
    for kind, s, e in _boxes(f, start, end):
        if kind == b"mvhd":
            f.seek(s)
            payload = f.read(min(e - s, 32))
            version = payload[0]
            if version == 1:
                timescale, duration = struct.unpack(">IQ", payload[20:32])
            else:
                timescale, duration = struct.unpack(">II", payload[12:20])
            if timescale:
                info.duration_ms = int(duration * 1000 / timescale)
        elif kind == b"trak" and info.width is None:
            _read_trak(f, s, e, info)


def _read_trak(f: BinaryIO, start: int, end: int, info: VideoInfo) -> None:
    for kind, s, e in _boxes(f, start, end):
        if kind != b"tkhd":
            continue
        f.seek(s)
        payload = f.read(e - s)
        offset = 40 if payload[0] == 0 else 52          # where the 3x3 matrix starts
        a, b = struct.unpack(">ii", payload[offset:offset + 8])
        width, height = struct.unpack(">II", payload[offset + 36:offset + 44])
        width, height = width >> 16, height >> 16        # 16.16 fixed point
        if width and height:                             # audio tracks have no size
            if a == 0 and b != 0:                        # turned by 90° or 270°
                width, height = height, width
            info.width, info.height = width, height
        return
