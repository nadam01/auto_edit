"""맥 ↔ 윈도우 번갈아 쓰기."""

import shutil
import sys
import types
import unicodedata
from xml.etree import ElementTree as ET

import pytest

from autocut.cache.store import Cache, probe_cached
from autocut.cli import main
from autocut.export.fcp7 import ExportOptions, build_xmeml, relink_xml
from autocut.ingest.probe import scan
from autocut.models import Cut, MediaInfo
from autocut.paths import PathMap, existing_variant, load_config, path_to_url, url_to_path
from autocut.transcribe import backends as be

PM = PathMap({
    "t7": {"mac": "/Volumes/T7", "windows": "E:/"},
    "proj": {"mac": "/Volumes/T7/Projects", "windows": "E:/Projects"},
    "nas": {"mac": "/Volumes/share", "windows": r"\\nas\share"},
})


@pytest.mark.parametrize("src,to,exp", [
    ("/Volumes/T7/촬영/A.mov", "windows", r"E:\촬영\A.mov"),
    (r"E:\촬영\A.mov", "mac", "/Volumes/T7/촬영/A.mov"),
    (r"e:\촬영\A.mov", "mac", "/Volumes/T7/촬영/A.mov"),  # 드라이브 문자 대소문자 무시
    ("/Volumes/T7/Projects/x.mov", "windows", r"E:\Projects\x.mov"),
    (r"\\nas\share\day1\b.mov", "mac", "/Volumes/share/day1/b.mov"),
    ("/Volumes/share/b.mov", "windows", r"\\nas\share\b.mov"),
    ("/Volumes/T7", "windows", "E:\\"),
    ("/Volumes/T7/a.mov", "mac", "/Volumes/T7/a.mov"),  # 이미 같은 OS
])
def test_convert(src, to, exp):
    assert PM.convert(src, to) == exp


def test_convert_unmapped_and_prefix_boundary():
    assert PM.convert("/Volumes/T77/a.mov", "windows") is None  # T7 접두어지만 다른 드라이브
    assert PM.convert("/Users/me/a.mov", "windows") is None


def test_convert_nfd_input():
    nfd = unicodedata.normalize("NFD", "/Volumes/T7/촬영/인터뷰.mov")
    out = PM.convert(nfd, "windows")
    assert unicodedata.normalize("NFC", out) == r"E:\촬영\인터뷰.mov"


@pytest.mark.parametrize("p", ["/Volumes/T7/촬영 1/a.mov", r"E:\촬영 1\a.mov", r"\\nas\share\a b.mov"])
def test_url_roundtrip(p):
    assert url_to_path(path_to_url(p)) == p


def test_premiere_style_url():
    # 프리미어가 윈도우에서 내보낸 형식 (소문자 %3a)
    assert url_to_path("file://localhost/E%3a/%EC%B4%AC%EC%98%81/A.mov") == r"E:\촬영\A.mov"


def test_load_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[roots.t7]\nmac = "/Volumes/T7"\nwindows = "E:/"\n\n'
                   '[cache_dir]\nmac = "/Volumes/T7/.c"\nwindows = "E:/.c"\n', encoding="utf-8")
    monkeypatch.setattr("autocut.paths.current_os", lambda: "windows")
    c = load_config(str(cfg))
    assert c.path_map.convert("/Volumes/T7/a", "windows") == r"E:\a" and c.cache_dir == "E:/.c"
    cfg.write_text('[roots.x]\nlinux = "/mnt"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(cfg))


def _m(path):
    return MediaInfo(path=path, duration=10, size=1, mtime_ns=1, has_video=True, fps_num=25, fps_den=1,
                     width=1920, height=1080, audio_channels=2, audio_sample_rate=48000)


def test_export_for_other_os(monkeypatch):
    monkeypatch.setattr("autocut.export.fcp7.current_os", lambda: "mac")
    cuts = [Cut(_m("/Volumes/T7/촬영/A.mov"), 0, 1), Cut(_m("/Users/me/B.mov"), 0, 1)]
    with pytest.warns(UserWarning, match="바꾸지 못한 파일 1개"):
        tree = build_xmeml(cuts, ExportOptions(target_os="windows", path_map=PM))
    urls = [e.text for e in tree.getroot().iter("pathurl")]
    assert urls[0] == "file://localhost/E%3A/%EC%B4%AC%EC%98%81/A.mov"


def test_cache_shared_across_paths(clips, tmp_path):
    """같은 파일을 다른 경로(=다른 OS/드라이브)로 열어도 캐시 적중, 경로는 현재 것으로."""
    cache = Cache(tmp_path / "c")
    src = scan(clips)[0]
    a = probe_cached(src, cache)
    copy_dir = tmp_path / "other drive"
    copy_dir.mkdir()
    dst = copy_dir / src.name
    shutil.copy(src, dst)
    assert Cache.media_key(src) == Cache.media_key(dst)

    calls = []
    import autocut.ingest.probe as pr
    orig = pr.probe
    pr.probe = lambda p: calls.append(p) or orig(p)
    try:
        b = probe_cached(dst, cache)
    finally:
        pr.probe = orig
    assert calls == [] and b.path == str(dst.resolve()) and b.duration == a.duration


def test_relink_file_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("autocut.export.fcp7.current_os", lambda: "linux-test")
    cuts = [Cut(_m("/Volumes/T7/촬영/A.mov"), 0, 1)]
    from autocut.export.fcp7 import write_xmeml
    mac_xml = write_xmeml(cuts, tmp_path / "r.xml", ExportOptions(target_os="mac", path_map=PM))
    st = relink_xml(mac_xml, tmp_path / "r.win.xml", PM, "windows")
    assert st == {"total": 1, "changed": 1, "unmapped": 0, "missing": 0}
    text = (tmp_path / "r.win.xml").read_text(encoding="utf-8")
    assert "<!DOCTYPE xmeml>" in text and "file://localhost/E%3A/" in text
    relink_xml(tmp_path / "r.win.xml", tmp_path / "back.xml", PM, "mac")
    assert ET.parse(tmp_path / "back.xml").getroot().find(".//pathurl").text == \
        ET.parse(mac_xml).getroot().find(".//pathurl").text


def test_existing_variant_nfd(tmp_path):
    nfd = unicodedata.normalize("NFD", "인터뷰.mov")
    (tmp_path / nfd).write_bytes(b"x")
    found = existing_variant(str(tmp_path / unicodedata.normalize("NFC", "인터뷰.mov")))
    assert found is not None


def test_cli_target_all(clips, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[roots.src]\nmac = "{clips}"\nwindows = "E:/footage"\n', encoding="utf-8")
    main(["smoke", str(clips), "-o", str(tmp_path / "s.xml"), "--cache-dir", str(tmp_path / "c"),
          "--config", str(cfg), "--target", "all"])
    win = (tmp_path / "s.win.xml").read_text(encoding="utf-8")
    mac = (tmp_path / "s.mac.xml").read_text(encoding="utf-8")
    assert "file://localhost/E%3A/footage/b_roll.mp4" in win
    assert f"file://localhost{clips}/b_roll.mp4" in mac


def test_cli_other_os_without_roots(clips, tmp_path):
    empty = tmp_path / "empty.toml"
    empty.write_text("", encoding="utf-8")
    other = "mac" if __import__("autocut.paths").paths.current_os() == "windows" else "windows"
    with pytest.raises(SystemExit, match="roots"):
        main(["smoke", str(clips), "-o", str(tmp_path / "s.xml"), "--cache-dir", str(tmp_path / "c"),
              "--config", str(empty), "--target", other])


def test_transcript_cache_shared_between_backends(clips, tmp_path):
    """맥(mlx)에서 만든 음성인식 결과를 윈도우(faster-whisper)에서 재사용."""
    from tests.test_pipeline import FakeBackend

    cache = Cache(tmp_path / "c")
    m = probe_cached(scan(clips)[0], cache)
    FakeBackend.calls = 0
    be.transcribe_cached(m, FakeBackend(), cache)

    class Other(FakeBackend):
        name = "other"

        def params(self):
            return {"backend": "other", "model": "small", "language": None}

    class SameLang(FakeBackend):
        def params(self):
            return {"backend": "faster-whisper", "model": "medium"}

    be.transcribe_cached(m, SameLang(), cache)
    assert FakeBackend.calls == 1
    be.transcribe_cached(m, SameLang(), cache, force=True)
    assert FakeBackend.calls == 2


def test_mlx_backend_parsing(monkeypatch):
    fake = types.ModuleType("mlx_whisper")
    seen = {}

    def transcribe(path, **kw):
        seen.update(kw)
        return {"language": "ko", "segments": [{"start": 0.0, "end": 1.0, "text": " 안녕 ",
                "words": [{"word": " 안녕", "start": 0.1, "end": 0.8, "probability": 0.9}]}]}

    fake.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake)
    b = be.get_backend("mlx-whisper", model="large-v3", language="ko")
    t = b.transcribe(_m("/x/a.mov"))
    assert seen["path_or_hf_repo"] == "mlx-community/whisper-large-v3-mlx" and seen["word_timestamps"]
    assert t.segments[0].words[0].text == "안녕" and t.backend == "mlx-whisper"

    monkeypatch.setattr(be, "is_apple_silicon", lambda: True)
    assert be.resolve_auto() == "mlx-whisper"
    monkeypatch.setattr(be, "is_apple_silicon", lambda: False)
    assert be.resolve_auto() == "faster-whisper"
