"""분석 결과 캐시.

키 = (원본 절대경로, 크기, 수정시각) + 단계 이름 + 단계 파라미터.
원본이 바뀌면 자동으로 새로 분석한다. 파일 단위로 저장하므로
중간에 멈춰도 끝난 파일은 재사용된다(이어하기).
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
        self.root = Path(root) if root else DEFAULT_CACHE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def media_key(path: str | Path) -> str:
        p = Path(path).resolve()
        st = p.stat()
        raw = f"{p}|{st.st_size}|{st.st_mtime_ns}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

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
        return MediaInfo.from_dict(hit)
    info = probe(path)
    cache.put(path, "probe", info.to_dict())
    return info
