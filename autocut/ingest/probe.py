"""소스 폴더 스캔 + ffprobe 메타데이터 수집. 원본은 읽기만 한다."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from autocut.models import MediaInfo

VIDEO_EXTS = {".mov", ".mp4", ".mxf", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".braw", ".r3d"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aif", ".aiff", ".flac", ".bwf"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


class ProbeError(RuntimeError):
    pass


def scan(folder: str | Path, recursive: bool = True) -> list[Path]:
    folder = Path(folder)
    if folder.is_file():
        return [folder.resolve()]
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    it = folder.rglob("*") if recursive else folder.glob("*")
    files = [
        p.resolve()
        for p in it
        if p.is_file()
        and p.suffix.lower() in MEDIA_EXTS
        and not p.name.startswith(".")  # macOS ._ 리소스 포크 등
        and not any(part.startswith(".") for part in p.relative_to(folder).parts[:-1])
    ]
    return sorted(files, key=lambda p: str(p).lower())


def _ffprobe_bin() -> str:
    b = shutil.which("ffprobe")
    if not b:
        raise ProbeError("ffprobe를 찾을 수 없습니다. ffmpeg를 설치하고 PATH에 추가하세요.")
    return b


def _parse_ratio(s: str | None) -> tuple[int, int]:
    if not s or "/" not in s:
        return 0, 1
    n, d = s.split("/", 1)
    try:
        n, d = int(n), int(d)
    except ValueError:
        return 0, 1
    return (n, d) if n and d else (0, 1)


def _find_timecode(data: dict) -> str | None:
    tags = data.get("format", {}).get("tags", {}) or {}
    if tags.get("timecode"):
        return tags["timecode"]
    for st in data.get("streams", []):
        tc = (st.get("tags") or {}).get("timecode")
        if tc:
            return tc
    return None


def parse_ffprobe(data: dict, path: Path) -> MediaInfo:
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    # 커버아트(attached_pic)는 비디오로 치지 않는다
    video = [
        s for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    audio = [s for s in streams if s.get("codec_type") == "audio"]

    duration = float(fmt.get("duration") or 0.0)
    if not duration:
        for s in streams:
            if s.get("duration"):
                duration = max(duration, float(s["duration"]))

    st = path.stat()
    info = MediaInfo(
        path=str(path),
        duration=duration,
        size=st.st_size,
        mtime_ns=st.st_mtime_ns,
        start_timecode=_find_timecode(data),
    )
    if video:
        v = video[0]
        num, den = _parse_ratio(v.get("r_frame_rate"))
        anum, aden = _parse_ratio(v.get("avg_frame_rate"))
        # VFR 휴대폰 영상은 r_frame_rate가 이상하게 높게 나올 수 있어 avg를 우선
        if anum and (not num or abs(anum / aden - num / den) > 1):
            num, den = anum, aden
        info.has_video = True
        info.fps_num, info.fps_den = num, den
        info.width = int(v.get("width") or 0)
        info.height = int(v.get("height") or 0)
    if audio:
        info.audio_channels = sum(int(a.get("channels") or 0) for a in audio)
        info.audio_sample_rate = int(audio[0].get("sample_rate") or 0)
    return info


def probe(path: str | Path) -> MediaInfo:
    path = Path(path).resolve()
    cmd = [
        _ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise ProbeError(f"ffprobe 실패 ({path.name}): {r.stderr.decode(errors='replace').strip()}")
    return parse_ffprobe(json.loads(r.stdout.decode("utf-8", errors="replace")), path)
