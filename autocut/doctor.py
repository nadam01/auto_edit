"""설치 상태 점검: autocut doctor"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from autocut.paths import current_os, load_config

OK, WARN, FAIL = "OK", "주의", "실패"


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _ffmpeg_version(binary: str) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, timeout=10).stdout.decode(errors="replace")
        return out.splitlines()[0] if out else path
    except Exception:
        return path


def run(config: str | None = None) -> list[tuple[str, str, str]]:
    from autocut.cache.store import DEFAULT_CACHE_DIR
    from autocut.transcribe.backends import cuda_device_count, is_apple_silicon, resolve_auto

    rows: list[tuple[str, str, str]] = []
    add = lambda st, item, msg: rows.append((st, item, msg))  # noqa: E731

    add(OK if sys.version_info >= (3, 11) else FAIL, "Python", sys.version.split()[0])
    add(OK, "OS", f"{current_os()} ({platform.platform()}, {platform.machine()})")

    for b in ("ffmpeg", "ffprobe"):
        v = _ffmpeg_version(b)
        add(OK if v else FAIL, b, v or ("brew install ffmpeg" if current_os() == "mac" else "winget install Gyan.FFmpeg"))

    # 음성인식
    if is_apple_silicon():
        add(OK if _has("mlx_whisper") else WARN, "mlx-whisper (맥 GPU)",
            "설치됨" if _has("mlx_whisper") else "pip install -e \".[mlx]\" 권장 (없으면 CPU로 느림)")
    elif sys.platform == "darwin":
        add(WARN, "맥 GPU", "인텔 맥: GPU 가속 없음 → --model medium 이하 권장")
    if _has("faster_whisper"):
        n = cuda_device_count()
        if n:
            add(OK, "faster-whisper", f"CUDA GPU {n}개 사용 가능")
        elif current_os() == "windows":
            add(WARN, "faster-whisper", "GPU를 못 찾음 → NVIDIA 드라이버 확인 후 pip install -e \".[cuda]\"")
        else:
            add(OK, "faster-whisper", "설치됨 (CPU)")
    elif not is_apple_silicon():
        add(FAIL, "faster-whisper", "pip install -e \".[whisper]\"")
    add(OK if _has("whisperx") else WARN, "whisperx (화자 분리)", "설치됨" if _has("whisperx") else "선택 사항")
    if _has("whisperx"):
        add(OK if os.environ.get("HF_TOKEN") else WARN, "HF_TOKEN", "설정됨" if os.environ.get("HF_TOKEN") else "화자 분리에 필요")
    try:
        add(OK, "auto 백엔드", resolve_auto())
    except Exception as e:  # pragma: no cover
        add(WARN, "auto 백엔드", str(e))

    # 설정·캐시
    try:
        cfg = load_config(config)
        if cfg.source:
            roots = ", ".join(f"{k}={v.get(current_os(), '-')}" for k, v in cfg.path_map.roots.items()) or "없음"
            add(OK, "config.toml", f"{cfg.source}  roots: {roots}")
            for name, osmap in cfg.path_map.roots.items():
                here = osmap.get(current_os())
                if here and not Path(here).exists():
                    add(WARN, f"roots.{name}", f"{here} 가 지금 보이지 않음 (드라이브 연결 확인)")
                if set(osmap) != {"mac", "windows"}:
                    add(WARN, f"roots.{name}", "mac / windows 둘 다 적어야 변환 가능")
        else:
            add(WARN, "config.toml", "없음 → 맥↔윈도우 XML 변환 불가 (README 참고)")
        cache_dir = Path(cfg.cache_dir).expanduser() if cfg.cache_dir else DEFAULT_CACHE_DIR
    except Exception as e:
        add(FAIL, "config.toml", str(e))
        cache_dir = DEFAULT_CACHE_DIR
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir):
            pass
        add(OK, "캐시 폴더", str(cache_dir))
    except OSError as e:
        add(FAIL, "캐시 폴더", f"{cache_dir} 쓰기 불가: {e}")

    add(OK if os.environ.get("ANTHROPIC_API_KEY") else WARN, "ANTHROPIC_API_KEY",
        "설정됨" if os.environ.get("ANTHROPIC_API_KEY") else "3단계(LLM 편집)부터 필요")
    return rows


def _width(s: str) -> int:
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def main(config: str | None = None) -> int:
    rows = run(config)
    w = max(_width(r[1]) for r in rows)
    for st, item, msg in rows:
        print(f"[{st}] {item}{' ' * (w - _width(item))}  {msg}")
    fails = sum(r[0] == FAIL for r in rows)
    print(f"\n실패 {fails}개" if fails else "\n필수 항목 정상")
    return 1 if fails else 0
