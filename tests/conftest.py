import shutil
import subprocess
from pathlib import Path

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 필요")


@pytest.fixture(scope="session")
def clips(tmp_path_factory) -> Path:
    """짧은 합성 클립 2개: 29.97fps DF 타임코드 스테레오 / 25fps 무타임코드 모노."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 필요")
    d = tmp_path_factory.mktemp("src")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30000/1001:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6:sample_rate=48000",
        "-ac", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-timecode", "01:00:00;00", "-shortest", str(d / "인터뷰 A.mov"),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30000/1001:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=220:duration=4:sample_rate=48000",
        "-ac", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(d / "b_roll.mp4"),
    ], check=True)
    (d / "notes.txt").write_text("ignore me")
    return d
