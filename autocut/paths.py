"""맥 ↔ 윈도우 경로 변환.

같은 외장 드라이브가 맥에서는 /Volumes/T7, 윈도우에서는 E:\\ 로 보인다.
설정 파일에 '루트'를 OS별로 적어두면 XML 내보내기·relink 때 경로를 바꿔준다.

~/.autocut/config.toml 예:
    [roots.t7]
    mac = "/Volumes/T7"
    windows = "E:/"

    [cache_dir]            # 선택: 두 컴퓨터가 같은 캐시를 쓰도록 외장 드라이브에
    mac = "/Volumes/T7/.autocut_cache"
    windows = "E:/.autocut_cache"
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

OS_NAMES = ("mac", "windows")
_WIN_DRIVE = re.compile(r"^[A-Za-z]:([\\/]|$)")


def current_os() -> str:
    return "windows" if sys.platform.startswith("win") else "mac"


def is_windows_path(p: str) -> bool:
    return bool(_WIN_DRIVE.match(p)) or p.startswith("\\\\") or p.startswith("//")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _norm(p: str) -> str:
    """비교용: 구분자 '/', 유니코드 NFC, 끝 '/' 제거."""
    p = nfc(p.replace("\\", "/"))
    return p.rstrip("/") if len(p) > 1 and not re.fullmatch(r"[A-Za-z]:/", p) else p


def _starts_with(path: str, prefix: str, case_insensitive: bool) -> bool:
    a, b = (path.lower(), prefix.lower()) if case_insensitive else (path, prefix)
    if a == b:
        return True
    return a.startswith(b if b.endswith("/") else b + "/")


def _source_os(p: str) -> str:
    return "windows" if is_windows_path(p) else "mac"


@dataclass
class PathMap:
    roots: dict[str, dict[str, str]] = field(default_factory=dict)

    def convert(self, path: str, to_os: str) -> str | None:
        """path를 to_os 경로로. 이미 to_os 형식이면 그대로, 매칭되는 루트가 없으면 None."""
        if to_os not in OS_NAMES:
            raise ValueError(f"OS는 {OS_NAMES} 중 하나: {to_os}")
        from_os = _source_os(path)
        if from_os == to_os:
            return path
        n = _norm(path)
        best: tuple[int, str] | None = None
        for osmap in self.roots.values():
            src, dst = osmap.get(from_os), osmap.get(to_os)
            if not src or not dst:
                continue
            sp = _norm(src)
            if _starts_with(n, sp, case_insensitive=(from_os == "windows")):
                rest = n[len(sp):].lstrip("/")
                if best is None or len(sp) > best[0]:  # 가장 긴 루트 우선
                    best = (len(sp), rest)
                    dst_root = _norm(dst)
        if best is None:
            return None
        rest = best[1]
        joined = dst_root.rstrip("/") + ("/" + rest if rest else "")
        if to_os == "windows":
            if re.fullmatch(r"[A-Za-z]:", joined):
                joined += "/"
            return joined.replace("/", "\\")
        return joined


@dataclass
class Config:
    path_map: PathMap = field(default_factory=PathMap)
    cache_dir: str | None = None
    source: Path | None = None


def config_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get("AUTOCUT_CONFIG"):
        return Path(os.environ["AUTOCUT_CONFIG"])
    return Path.home() / ".autocut" / "config.toml"


def load_config(explicit: str | None = None) -> Config:
    p = config_path(explicit)
    if not p.exists():
        if explicit:
            raise FileNotFoundError(p)
        return Config()
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    roots = {}
    for name, osmap in (data.get("roots") or {}).items():
        unknown = set(osmap) - set(OS_NAMES)
        if unknown:
            raise ValueError(f"{p}: roots.{name}에 알 수 없는 키 {unknown} (mac / windows만)")
        roots[name] = {k: str(v) for k, v in osmap.items()}
    cd = data.get("cache_dir")
    if isinstance(cd, dict):
        cd = cd.get(current_os())
    return Config(PathMap(roots), cd, p)


# ---- pathurl ----

def path_to_url(path: str) -> str:
    """로컬 경로 -> Premiere pathurl. 어느 OS에서 실행하든 경로 형식을 보고 판단한다."""
    if _WIN_DRIVE.match(path):
        return "file://localhost/" + quote(path.replace("\\", "/"), safe="/")
    if path.startswith("\\\\") or path.startswith("//"):
        p = path.replace("\\", "/").lstrip("/")
        host, _, rest = p.partition("/")
        return f"file://{host}/" + quote(rest, safe="/")
    p = path if path.startswith("/") else str(Path(path).resolve())
    return "file://localhost" + quote(p, safe="/")


def url_to_path(url: str) -> str:
    u = urlparse(url)
    p = unquote(u.path)
    if re.match(r"^/[A-Za-z]:", p):  # /C:/x -> C:\x
        return p[1:].replace("/", "\\")
    if u.netloc and u.netloc.lower() != "localhost":
        return "\\\\" + u.netloc + p.replace("/", "\\")
    return p


def existing_variant(path: str) -> str | None:
    """한글 파일명의 NFC/NFD 차이 때문에 못 찾는 경우를 보정."""
    for cand in (path, unicodedata.normalize("NFC", path), unicodedata.normalize("NFD", path)):
        if os.path.exists(cand):
            return cand
    return None
