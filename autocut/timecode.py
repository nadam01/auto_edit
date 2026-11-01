"""프레임 레이트·타임코드 변환.

FCP7 XML은 레이트를 (timebase 정수, ntsc 여부)로 표현한다.
  29.97  -> timebase 30, ntsc TRUE
  23.976 -> timebase 24, ntsc TRUE
  25     -> timebase 25, ntsc FALSE
프레임 번호는 '실제' 프레임 수(29.97이면 초당 29.97장)다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Rate:
    timebase: int
    ntsc: bool

    @property
    def fps(self) -> Fraction:
        if self.ntsc:
            return Fraction(self.timebase * 1000, 1001)
        return Fraction(self.timebase)

    @classmethod
    def from_fps(cls, num: int, den: int = 1) -> "Rate":
        if not num or not den:
            raise ValueError(f"잘못된 fps: {num}/{den}")
        fps = Fraction(num, den)
        nearest = round(fps)
        if abs(fps - nearest) < Fraction(1, 100):
            return cls(int(nearest), False)
        # 29.97, 23.976, 59.94 등 NTSC 계열
        tb = round(fps * Fraction(1001, 1000))
        if abs(Fraction(tb * 1000, 1001) - fps) < Fraction(1, 100):
            return cls(int(tb), True)
        raise ValueError(f"지원하지 않는 프레임 레이트: {float(fps):.4f}")

    def to_frames(self, seconds: float) -> int:
        return round(Fraction(seconds).limit_denominator(10**6) * self.fps)

    def to_seconds(self, frames: int) -> float:
        return float(Fraction(frames) / self.fps)


_TC_RE = re.compile(r"^(\d{1,2})[:;.](\d{2})[:;.](\d{2})([:;.])(\d{2})$")


def is_drop_frame(tc: str) -> bool:
    m = _TC_RE.match(tc.strip())
    return bool(m and m.group(4) in ";.")


def timecode_to_frames(tc: str, rate: Rate) -> int:
    """타임코드 문자열 -> 0부터 센 프레임 번호. ';'면 드롭프레임."""
    m = _TC_RE.match(tc.strip())
    if not m:
        raise ValueError(f"타임코드 형식 오류: {tc!r}")
    h, mi, s, sep, f = m.groups()
    h, mi, s, f = int(h), int(mi), int(s), int(f)
    tb = rate.timebase
    frames = ((h * 3600 + mi * 60 + s) * tb) + f
    if sep in ";." and rate.ntsc and tb % 30 == 0:
        drop = tb // 15  # 29.97 -> 2, 59.94 -> 4
        total_min = h * 60 + mi
        frames -= drop * (total_min - total_min // 10)
    return frames


def frames_to_timecode(frames: int, rate: Rate, drop_frame: bool = False) -> str:
    tb = rate.timebase
    sep = ":"
    if drop_frame and rate.ntsc and tb % 30 == 0:
        sep = ";"
        drop = tb // 15
        per_10min = tb * 600 - drop * 9
        per_min = tb * 60 - drop
        d, m = divmod(frames, per_10min)
        if m > drop:
            frames += drop * 9 * d + drop * ((m - drop) // per_min)
        else:
            frames += drop * 9 * d
    f = frames % tb
    s = (frames // tb) % 60
    mi = (frames // (tb * 60)) % 60
    h = frames // (tb * 3600)
    return f"{h:02d}:{mi:02d}:{s:02d}{sep}{f:02d}"
