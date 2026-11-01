"""FCP7 XML(xmeml v4) 내보내기 — Premiere Pro: 파일 > 가져오기.

설계 메모
- clipitem의 start/end/in/out은 모두 '시퀀스 타임베이스' 프레임으로 쓴다.
  (Premiere가 내보내는 XML과 같은 방식. 소스 fps가 시퀀스와 다르면 경고)
- <file>은 문서 순서상 처음 나올 때만 전체 정의, 이후엔 <file id="..."/> 참조.
- 오디오는 채널마다 별도 모노 트랙(A1, A2...)에 놓고 비디오와 <link>로 묶는다.
- 원본 경로는 pathurl(file://localhost/...)로 연결만 한다. 복사하지 않음.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree as ET

from autocut.models import Cut, MediaInfo, Marker
from autocut.timecode import Rate, is_drop_frame, timecode_to_frames

_WIN_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def path_to_url(path: str) -> str:
    """로컬 경로 -> Premiere가 읽는 pathurl. 윈도우/UNC/맥 모두 처리."""
    if _WIN_DRIVE.match(path):
        p = path.replace("\\", "/")
        return "file://localhost/" + quote(p, safe="/")
    if path.startswith("\\\\"):  # \\server\share\...
        p = path[2:].replace("\\", "/")
        host, _, rest = p.partition("/")
        return f"file://{host}/" + quote(rest, safe="/")
    p = path if path.startswith("/") else str(Path(path).resolve())
    return "file://localhost" + quote(p, safe="/")


def _sub(parent: ET.Element, tag: str, text: object | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent: ET.Element, rate: Rate) -> None:
    r = _sub(parent, "rate")
    _sub(r, "timebase", rate.timebase)
    _sub(r, "ntsc", "TRUE" if rate.ntsc else "FALSE")


def _timecode(parent: ET.Element, rate: Rate, tc: str, frame: int) -> None:
    t = _sub(parent, "timecode")
    _rate(t, rate)
    _sub(t, "string", tc)
    _sub(t, "frame", frame)
    _sub(t, "displayformat", "DF" if is_drop_frame(tc) else "NDF")


def _marker(parent: ET.Element, m: Marker, rate: Rate, offset: float = 0.0) -> None:
    el = _sub(parent, "marker")
    _sub(el, "name", m.name)
    _sub(el, "comment", m.comment)
    start = rate.to_frames(m.start - offset)
    _sub(el, "in", start)
    _sub(el, "out", start + rate.to_frames(m.duration) if m.duration > 0 else -1)


@dataclass
class ExportOptions:
    sequence_name: str = "AutoCut"
    max_audio_tracks: int = 2  # 채널이 많은 카메라(4ch 등)도 앞 N채널만
    clip_markers: bool = True  # 각 컷의 reason을 클립 마커로
    sequence_markers: list[Marker] = field(default_factory=list)
    rate: Rate | None = None  # None이면 첫 비디오 소스 기준
    width: int | None = None
    height: int | None = None


@dataclass
class _Placed:
    cut: Cut
    start: int
    end: int
    src_in: int
    src_out: int
    video_id: str | None = None
    audio_ids: list[str] = field(default_factory=list)


def _sequence_format(cuts: list[Cut], opt: ExportOptions) -> tuple[Rate, int, int]:
    vids = [c.media for c in cuts if c.media.has_video and c.media.fps_num]
    rate = opt.rate or (Rate.from_fps(vids[0].fps_num, vids[0].fps_den) if vids else Rate(30, True))
    w = opt.width or (vids[0].width if vids else 1920)
    h = opt.height or (vids[0].height if vids else 1080)
    mixed = {Rate.from_fps(m.fps_num, m.fps_den) for m in vids} - {rate}
    if mixed:
        warnings.warn(
            f"시퀀스({float(rate.fps):.3f}fps)와 다른 프레임 레이트 소스가 있습니다: "
            + ", ".join(f"{float(r.fps):.3f}" for r in mixed)
            + " — Premiere에서 속도/싱크를 확인하세요.",
            stacklevel=3,
        )
    return rate, w, h


def build_xmeml(cuts: list[Cut], opt: ExportOptions | None = None) -> ET.ElementTree:
    opt = opt or ExportOptions()
    if not cuts:
        raise ValueError("내보낼 컷이 없습니다.")
    rate, width, height = _sequence_format(cuts, opt)

    # 1) 타임라인 배치 (프레임 정수로 누적해 반올림 오차가 쌓이지 않게)
    placed: list[_Placed] = []
    cursor = 0
    for c in cuts:
        si, so = rate.to_frames(c.src_in), rate.to_frames(c.src_out)
        if so <= si:
            continue
        placed.append(_Placed(c, cursor, cursor + (so - si), si, so))
        cursor += so - si
    if not placed:
        raise ValueError("모든 컷 길이가 1프레임 미만입니다.")

    n_audio = min(opt.max_audio_tracks, max((p.cut.media.audio_channels for p in placed), default=0))

    # 2) ID 부여
    file_ids: dict[str, str] = {}
    master_ids: dict[str, str] = {}
    for p in placed:
        fp = p.cut.media.path
        if fp not in file_ids:
            file_ids[fp] = f"file-{len(file_ids) + 1}"
            master_ids[fp] = f"masterclip-{len(master_ids) + 1}"
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"clipitem-{counter}"

    for p in placed:
        if p.cut.media.has_video:
            p.video_id = next_id()
        p.audio_ids = [next_id() for _ in range(min(n_audio, p.cut.media.audio_channels))]

    # 트랙별 clipindex(1부터) — link에 필요
    v_index: dict[str, int] = {}
    a_index: list[dict[str, int]] = [dict() for _ in range(n_audio)]
    for p in placed:
        if p.video_id:
            v_index[p.video_id] = len(v_index) + 1
        for ch, aid in enumerate(p.audio_ids):
            a_index[ch][aid] = len(a_index[ch]) + 1

    defined: set[str] = set()

    def file_el(parent: ET.Element, m: MediaInfo) -> None:
        fid = file_ids[m.path]
        f = _sub(parent, "file")
        f.set("id", fid)
        if fid in defined:
            return
        defined.add(fid)
        _sub(f, "name", m.name)
        _sub(f, "pathurl", path_to_url(m.path))
        src_rate = Rate.from_fps(m.fps_num, m.fps_den) if m.has_video and m.fps_num else rate
        _rate(f, src_rate)
        _sub(f, "duration", src_rate.to_frames(m.duration))
        if m.start_timecode:
            try:
                _timecode(f, src_rate, m.start_timecode, timecode_to_frames(m.start_timecode, src_rate))
            except ValueError:
                pass
        media = _sub(f, "media")
        if m.has_video:
            v = _sub(media, "video")
            sc = _sub(v, "samplecharacteristics")
            _rate(sc, src_rate)
            _sub(sc, "width", m.width)
            _sub(sc, "height", m.height)
            _sub(sc, "pixelaspectratio", "square")
            _sub(sc, "fielddominance", "none")
        if m.audio_channels:
            a = _sub(media, "audio")
            sc = _sub(a, "samplecharacteristics")
            _sub(sc, "depth", 16)
            _sub(sc, "samplerate", m.audio_sample_rate or 48000)
            _sub(a, "channelcount", m.audio_channels)

    def links(parent: ET.Element, p: _Placed) -> None:
        if p.video_id is None and len(p.audio_ids) < 2:
            return
        if p.video_id:
            lk = _sub(parent, "link")
            _sub(lk, "linkclipref", p.video_id)
            _sub(lk, "mediatype", "video")
            _sub(lk, "trackindex", 1)
            _sub(lk, "clipindex", v_index[p.video_id])
        for ch, aid in enumerate(p.audio_ids):
            lk = _sub(parent, "link")
            _sub(lk, "linkclipref", aid)
            _sub(lk, "mediatype", "audio")
            _sub(lk, "trackindex", ch + 1)
            _sub(lk, "clipindex", a_index[ch][aid])
            _sub(lk, "groupindex", 1)

    def clipitem(track: ET.Element, cid: str, p: _Placed, mediatype: str, src_track: int) -> ET.Element:
        m = p.cut.media
        ci = _sub(track, "clipitem")
        ci.set("id", cid)
        _sub(ci, "masterclipid", master_ids[m.path])
        _sub(ci, "name", m.name)
        _sub(ci, "enabled", "TRUE")
        _sub(ci, "duration", rate.to_frames(m.duration))
        _rate(ci, rate)
        _sub(ci, "start", p.start)
        _sub(ci, "end", p.end)
        _sub(ci, "in", p.src_in)
        _sub(ci, "out", p.src_out)
        file_el(ci, m)
        st = _sub(ci, "sourcetrack")
        _sub(st, "mediatype", mediatype)
        _sub(st, "trackindex", src_track)
        links(ci, p)
        return ci

    # 3) 문서 작성
    root = ET.Element("xmeml", version="4")
    seq = _sub(root, "sequence")
    seq.set("id", "sequence-1")
    _sub(seq, "name", opt.sequence_name)
    _sub(seq, "duration", cursor)
    _rate(seq, rate)
    _timecode(seq, rate, "00:00:00:00", 0)
    media = _sub(seq, "media")

    video = _sub(media, "video")
    fmt = _sub(video, "format")
    sc = _sub(fmt, "samplecharacteristics")
    _rate(sc, rate)
    _sub(sc, "width", width)
    _sub(sc, "height", height)
    _sub(sc, "anamorphic", "FALSE")
    _sub(sc, "pixelaspectratio", "square")
    _sub(sc, "fielddominance", "none")
    vtrack = _sub(video, "track")
    for p in placed:
        if p.video_id:
            ci = clipitem(vtrack, p.video_id, p, "video", 1)
            if opt.clip_markers and p.cut.reason:
                _marker(ci, Marker(name=p.cut.label or "cut", comment=p.cut.reason, start=p.cut.src_in), rate)
            for mk in p.cut.markers:
                _marker(ci, mk, rate)
    _sub(vtrack, "enabled", "TRUE")
    _sub(vtrack, "locked", "FALSE")

    audio = _sub(media, "audio")
    _sub(audio, "numOutputChannels", 2)
    afmt = _sub(audio, "format")
    asc = _sub(afmt, "samplecharacteristics")
    _sub(asc, "depth", 16)
    _sub(asc, "samplerate", next((p.cut.media.audio_sample_rate for p in placed if p.cut.media.audio_sample_rate), 48000))
    for ch in range(n_audio):
        atrack = _sub(audio, "track")
        for p in placed:
            if ch < len(p.audio_ids):
                ci = clipitem(atrack, p.audio_ids[ch], p, "audio", ch + 1)
                if p.video_id is None and ch == 0 and opt.clip_markers and p.cut.reason:
                    _marker(ci, Marker(name=p.cut.label or "cut", comment=p.cut.reason, start=p.cut.src_in), rate)
        _sub(atrack, "enabled", "TRUE")
        _sub(atrack, "locked", "FALSE")
        # 스테레오 소스면 좌/우로 패닝 (모노 2트랙으로 들어가므로)
        if n_audio == 2:
            _sub(atrack, "outputchannelindex", ch + 1)

    for mk in opt.sequence_markers:
        _marker(seq, mk, rate)

    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def write_xmeml(cuts: list[Cut], out_path: str | Path, opt: ExportOptions | None = None) -> Path:
    tree = build_xmeml(cuts, opt)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = ET.tostring(tree.getroot(), encoding="unicode")
    out_path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + body + "\n",
        encoding="utf-8",
    )
    return out_path
