import pytest

from autocut.timecode import Rate, frames_to_timecode, timecode_to_frames


@pytest.mark.parametrize("num,den,tb,ntsc", [
    (30000, 1001, 30, True),
    (24000, 1001, 24, True),
    (60000, 1001, 60, True),
    (25, 1, 25, False),
    (24, 1, 24, False),
    (2997, 100, 30, True),
])
def test_rate_from_fps(num, den, tb, ntsc):
    assert Rate.from_fps(num, den) == Rate(tb, ntsc)


def test_frames_roundtrip():
    r = Rate(30, True)
    assert r.to_frames(1.0) == 30  # 29.97 -> 29.97 -> 반올림 30
    assert r.to_frames(10.0) == 300
    assert r.to_frames(r.to_seconds(12345)) == 12345


def test_drop_frame():
    r = Rate(30, True)
    assert timecode_to_frames("00:01:00;02", r) == 1800
    assert timecode_to_frames("00:10:00;00", r) == 17982
    assert timecode_to_frames("01:00:00;00", r) == 107892
    for f in (0, 1799, 1800, 17982, 107892, 123456):
        assert timecode_to_frames(frames_to_timecode(f, r, drop_frame=True), r) == f


def test_non_drop():
    r = Rate(25, False)
    assert timecode_to_frames("01:00:00:00", r) == 90000
    assert frames_to_timecode(90001, r) == "01:00:00:01"
