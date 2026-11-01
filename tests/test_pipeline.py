from xml.etree import ElementTree as ET

import pytest

from autocut.cache.store import Cache, probe_cached
from autocut.cli import main
from autocut.edit.simple import SpeechCutOptions, speech_cuts
from autocut.export.fcp7 import ExportOptions, build_xmeml, path_to_url
from autocut.ingest.probe import scan
from autocut.models import Cut, MediaInfo, Segment, Transcript, Word
from autocut.transcribe.backends import transcribe_cached


def test_scan_and_probe(clips, tmp_path):
    files = scan(clips)
    assert [f.name for f in files] == ["b_roll.mp4", "인터뷰 A.mov"]
    cache = Cache(tmp_path / "c")
    a = probe_cached(files[1], cache)
    assert a.has_video and (a.fps_num, a.fps_den) == (30000, 1001)
    assert a.audio_channels == 2 and a.audio_sample_rate == 48000
    assert a.start_timecode == "01:00:00;00"
    assert 5.9 < a.duration < 6.2
    # 두 번째 호출은 캐시
    assert probe_cached(files[1], cache) == a


@pytest.mark.parametrize("p,url", [
    ("/Users/me/영상/인터뷰 A.mov", "file://localhost/Users/me/%EC%98%81%EC%83%81/%EC%9D%B8%ED%84%B0%EB%B7%B0%20A.mov"),
    (r"C:\Footage\day 1\a.mov", "file://localhost/C%3A/Footage/day%201/a.mov"),
    (r"\\nas\share\a.mov", "file://nas/share/a.mov"),
])
def test_path_to_url(p, url):
    assert path_to_url(p) == url


def _media(path="/x/a.mov", dur=60.0, ch=2, fps=(30000, 1001), tc=None):
    return MediaInfo(path=path, duration=dur, size=1, mtime_ns=1, has_video=fps is not None,
                     fps_num=fps[0] if fps else 0, fps_den=fps[1] if fps else 1,
                     width=1920, height=1080, audio_channels=ch, audio_sample_rate=48000,
                     start_timecode=tc)


def _transcript(m, words):
    return Transcript(m.path, "ko", "fake", [Segment(words[0][0], words[-1][1], " ".join(w[2] for w in words),
                                                     words=[Word(w[2], w[0], w[1]) for w in words])])


def test_speech_cuts_merge_and_pad():
    m = _media(dur=20)
    t = _transcript(m, [(1.0, 1.5, "안녕"), (1.6, 2.0, "하세요"), (5.0, 5.5, "네"), (5.6, 6.0, "반가워요"),
                        (6.05, 6.1, "")])
    cuts = speech_cuts(m, t, SpeechCutOptions(gap_merge=0.7, pad_before=0.2, pad_after=0.2, min_duration=0.3))
    assert len(cuts) == 2
    assert cuts[0].src_in == pytest.approx(0.8) and cuts[0].src_out == pytest.approx(2.2)
    assert cuts[1].src_in == pytest.approx(4.8) and cuts[1].reason.startswith("네 반가워요")


def test_speech_cuts_no_overlap_and_clamp():
    m = _media(dur=3.0)
    t = _transcript(m, [(0.05, 1.0, "a"), (1.9, 2.95, "b")])
    cuts = speech_cuts(m, t, SpeechCutOptions(gap_merge=0.5, pad_before=0.5, pad_after=0.5, min_duration=0.1))
    assert cuts[0].src_in == 0.0 and cuts[-1].src_out == 3.0
    assert cuts[0].src_out <= cuts[1].src_in


def _parse(tree):
    return ET.fromstring(ET.tostring(tree.getroot()))


def test_xmeml_structure():
    a = _media("/x/a.mov", tc="01:00:00;00")
    b = _media("/x/b.wav", dur=30, ch=1, fps=None)  # 오디오 전용
    cuts = [Cut(a, 1.0, 2.0, "c1", "이유1"), Cut(b, 3.0, 4.5, "c2", "이유2"), Cut(a, 10.0, 12.0, "c3", "이유3")]
    root = _parse(build_xmeml(cuts, ExportOptions(sequence_name="t")))
    seq = root.find("sequence")
    assert seq.findtext("rate/timebase") == "30" and seq.findtext("rate/ntsc") == "TRUE"

    vclips = seq.findall("media/video/track/clipitem")
    atracks = seq.findall("media/audio/track")
    assert len(vclips) == 2 and len(atracks) == 2
    assert len(atracks[0].findall("clipitem")) == 3 and len(atracks[1].findall("clipitem")) == 2

    # 타임라인이 빈틈없이 이어지고 길이가 in/out과 일치
    a1 = atracks[0].findall("clipitem")
    ends = [(int(c.findtext("start")), int(c.findtext("end"))) for c in a1]
    assert ends[0][0] == 0 and all(ends[i][1] == ends[i + 1][0] for i in range(len(ends) - 1))
    for c in a1:
        assert int(c.findtext("end")) - int(c.findtext("start")) == int(c.findtext("out")) - int(c.findtext("in"))
    assert int(seq.findtext("duration")) == ends[-1][1]

    # file은 처음 한 번만 전체 정의
    full = [f for f in root.iter("file") if f.find("pathurl") is not None]
    assert sorted(f.get("id") for f in full) == ["file-1", "file-2"]
    assert next(f for f in full if f.get("id") == "file-1").findtext("timecode/frame") == "107892"

    # link 대상 id가 모두 존재
    ids = {c.get("id") for c in root.iter("clipitem")}
    refs = {lk.findtext("linkclipref") for lk in root.iter("link")}
    assert refs <= ids

    # 클립 마커에 선택 이유
    assert vclips[0].findtext("marker/comment") == "이유1"
    assert atracks[0].findall("clipitem")[1].findtext("marker/comment") == "이유2"


class FakeBackend:
    name = "fake"
    calls = 0

    def params(self):
        return {"backend": "fake"}

    def transcribe(self, media):
        FakeBackend.calls += 1
        return Transcript(media.path, "ko", "fake", [
            Segment(0.5, 1.5, "첫 문장", words=[Word("첫", 0.5, 0.9), Word("문장", 1.0, 1.5)]),
            Segment(3.0, 3.6, "둘째", words=[Word("둘째", 3.0, 3.6)]),
        ])


def test_transcript_cache(clips, tmp_path):
    cache = Cache(tmp_path / "c")
    m = probe_cached(scan(clips)[0], cache)
    FakeBackend.calls = 0
    t1 = transcribe_cached(m, FakeBackend(), cache)
    t2 = transcribe_cached(m, FakeBackend(), cache)
    assert FakeBackend.calls == 1 and t1 == t2


def test_cli_smoke(clips, tmp_path):
    out = tmp_path / "smoke.xml"
    main(["smoke", str(clips), "-o", str(out), "--cache-dir", str(tmp_path / "c"), "--piece", "1"])
    text = out.read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>')
    root = ET.fromstring(text.split("\n", 2)[2])
    assert len(root.findall("sequence/media/video/track/clipitem")) == 6
    assert "%EC%9D%B8%ED%84%B0%EB%B7%B0%20A.mov" in text


def test_cli_cut_with_fake_backend(clips, tmp_path, monkeypatch):
    import autocut.transcribe.backends as be

    monkeypatch.setattr(be, "get_backend", lambda *a, **k: FakeBackend())
    out = tmp_path / "rough.xml"
    main(["cut", str(clips), "-o", str(out), "--cache-dir", str(tmp_path / "c")])
    root = ET.fromstring(out.read_text(encoding="utf-8").split("\n", 2)[2])
    vclips = root.findall("sequence/media/video/track/clipitem")
    assert len(vclips) == 4  # 파일 2개 x 대사 구간 2개
    assert vclips[0].findtext("marker/comment") == "첫 문장"
