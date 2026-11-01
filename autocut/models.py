"""파이프라인 전체에서 공유하는 데이터 구조.

시간은 모두 '원본 미디어 시작 기준 초(float)'로 다룬다.
프레임 변환은 XML 내보내기 직전에만 한다(autocut.timecode).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MediaInfo:
    path: str  # 절대 경로
    duration: float  # 초
    size: int
    mtime_ns: int
    has_video: bool = False
    fps_num: int = 0
    fps_den: int = 1
    width: int = 0
    height: int = 0
    audio_channels: int = 0  # 모든 오디오 스트림 채널 합
    audio_sample_rate: int = 0
    start_timecode: str | None = None  # "HH:MM:SS:FF" (드롭프레임은 ';')

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den if self.fps_num else 0.0

    @property
    def name(self) -> str:
        from pathlib import PurePath

        return PurePath(self.path).name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MediaInfo":
        return cls(**d)


@dataclass
class Word:
    text: str
    start: float
    end: float
    score: float | None = None
    speaker: str | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    media_path: str
    language: str
    backend: str
    segments: list[Segment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        segs = [
            Segment(
                start=s["start"],
                end=s["end"],
                text=s["text"],
                speaker=s.get("speaker"),
                words=[Word(**w) for w in s.get("words", [])],
            )
            for s in d.get("segments", [])
        ]
        return cls(
            media_path=d["media_path"],
            language=d["language"],
            backend=d["backend"],
            segments=segs,
        )


@dataclass
class Marker:
    name: str
    comment: str = ""
    # 시퀀스 마커면 시퀀스 기준 초, 클립 마커면 원본 기준 초
    start: float = 0.0
    duration: float = 0.0
    color: str | None = None  # 3단계에서 사용 (제외 후보 등)


@dataclass
class Cut:
    """타임라인에 놓일 한 조각. src_in/src_out은 원본 기준 초."""

    media: MediaInfo
    src_in: float
    src_out: float
    label: str = ""
    reason: str = ""
    markers: list[Marker] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.src_out - self.src_in
