"""분석 결과 캐시.

키 = 원본 '내용' 지문(크기 + 앞/뒤 1MB 해시) + 단계 이름 + 단계 파라미터.
경로·수정시각은 쓰지 않으므로 같은 파일을 맥(/Volumes/..)과 윈도우(E:\\..)에서
열어도, 다른 드라이브로 복사해도 캐시가 그대로 맞는다.
캐시 폴더를 외장 드라이브에 두면 두 컴퓨터가 분석 결과를 공유한다.
파일 단위로 저장하므로 중간에 멈춰도 끝난 파일은 재사용된다(이어하기).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from autocut.models import MediaInfo

DEFAULT_CACHE_DIR = Path(os.environ.get("AUTOCUT_CACHE_DIR", Path.home() / ".autocut" / "cache"))


class Cache:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root).expanduser() if root else DEFAULT_CACHE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    _key_memo: dict[tuple[str, int, int], str] = {}

    @classmethod
    def media_key(cls, path: str | Path) -> str:
        p = Path(path)
        st = p.stat()
        memo = (str(p), st.st_size, st.st_mtime_ns)
        if memo in cls._key_memo:
            return cls._key_memo[memo]
        h = hashlib.sha1(f"v2|{st.st_size}|".encode())
        chunk = 1 << 20
        with open(p, "rb") as f:
            h.update(f.read(chunk))
            if st.st_size > 2 * chunk:
                f.seek(-chunk, os.SEEK_END)
                h.update(f.read(chunk))
        key = h.hexdigest()[:20]
        cls._key_memo[memo] = key
        return key

    def _path(self, media_path: str | Path, stage: str, params: dict[str, Any] | None) -> Path:
        mk = self.media_key(media_path)
        pk = hashlib.sha1(json.dumps(params or {}, sort_keys=True).encode()).hexdigest()[:10]
        return self.root / mk / f"{stage}-{pk}.json"

    def get(self, media_path, stage: str, params: dict[str, Any] | None = None) -> Any | None:
        p = self._path(media_path, stage, params)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, media_path, stage: str, value: Any, params: dict[str, Any] | None = None) -> Path:
        p = self._path(media_path, stage, params)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 원자적 쓰기: 중간에 죽어도 깨진 캐시가 남지 않게
        fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return p


def probe_cached(path, cache: Cache) -> MediaInfo:
    from autocut.ingest.probe import probe

    hit = cache.get(path, "probe")
    if hit:
        info = MediaInfo.from_dict(hit)
        # 캐시는 다른 OS/경로에서 만들어졌을 수 있다 -> 현재 위치로 갱신
        st = Path(path).stat()
        info.path, info.size, info.mtime_ns = str(Path(path).resolve()), st.st_size, st.st_mtime_ns
        return info
    info = probe(path)
    cache.put(path, "probe", info.to_dict())
    return info
