"""음성인식 백엔드.

- faster-whisper : 가볍고 설치 쉬움. 단어 타임코드 O, 화자 분리 X.
- whisperx       : 강제 정렬로 단어 경계가 더 정확 + pyannote 화자 분리(HF_TOKEN 필요).

무거운 라이브러리는 실제로 쓸 때만 import 한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol

from autocut.cache.store import Cache
from autocut.models import MediaInfo, Segment, Transcript, Word


class Backend(Protocol):
    name: str

    def params(self) -> dict[str, Any]: ...
    def transcribe(self, media: MediaInfo) -> Transcript: ...


class BackendUnavailable(RuntimeError):
    pass


def _auto_device() -> str:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    try:
        import ctranslate2  # type: ignore

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    return "cpu"


@dataclass
class FasterWhisperBackend:
    model: str = "large-v3"
    language: str = "ko"
    device: str = "auto"
    compute_type: str = "default"
    beam_size: int = 5
    name: str = "faster-whisper"

    def params(self) -> dict[str, Any]:
        return {"backend": self.name, "model": self.model, "language": self.language, "beam": self.beam_size}

    def transcribe(self, media: MediaInfo) -> Transcript:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            raise BackendUnavailable("faster-whisper 미설치: pip install 'autocut[whisper]'") from e

        device = _auto_device() if self.device == "auto" else self.device
        if not hasattr(self, "_model"):
            self._model = WhisperModel(self.model, device=device, compute_type=self.compute_type)
        segments, info = self._model.transcribe(
            media.path,
            language=self.language or None,
            beam_size=self.beam_size,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            condition_on_previous_text=False,  # 반복 환각 억제
        )
        out: list[Segment] = []
        for s in segments:
            words = [
                Word(text=w.word.strip(), start=float(w.start), end=float(w.end), score=float(w.probability))
                for w in (s.words or [])
            ]
            out.append(Segment(start=float(s.start), end=float(s.end), text=s.text.strip(), words=words))
        return Transcript(media_path=media.path, language=info.language, backend=self.name, segments=out)


@dataclass
class WhisperXBackend:
    model: str = "large-v3"
    language: str = "ko"
    device: str = "auto"
    compute_type: str = "float16"
    batch_size: int = 16
    diarize: bool = False
    min_speakers: int | None = None
    max_speakers: int | None = None
    name: str = "whisperx"

    def params(self) -> dict[str, Any]:
        return {
            "backend": self.name, "model": self.model, "language": self.language,
            "diarize": self.diarize, "min_spk": self.min_speakers, "max_spk": self.max_speakers,
        }

    def transcribe(self, media: MediaInfo) -> Transcript:
        try:
            import whisperx  # type: ignore
        except ImportError as e:
            raise BackendUnavailable("whisperx 미설치: pip install 'autocut[whisperx]'") from e

        device = _auto_device() if self.device == "auto" else self.device
        compute_type = self.compute_type if device == "cuda" else "int8"
        audio = whisperx.load_audio(media.path)
        model = whisperx.load_model(self.model, device, compute_type=compute_type, language=self.language)
        result = model.transcribe(audio, batch_size=self.batch_size, language=self.language)
        lang = result.get("language", self.language)

        align_model, meta = whisperx.load_align_model(language_code=lang, device=device)
        result = whisperx.align(result["segments"], align_model, meta, audio, device, return_char_alignments=False)

        if self.diarize:
            token = os.environ.get("HF_TOKEN")
            if not token:
                raise BackendUnavailable("화자 분리에는 HF_TOKEN 환경변수(pyannote 모델 동의 필요)가 필요합니다.")
            try:
                from whisperx.diarize import DiarizationPipeline  # type: ignore  # 3.3+
            except ImportError:
                DiarizationPipeline = whisperx.DiarizationPipeline  # type: ignore
            pipe = DiarizationPipeline(use_auth_token=token, device=device)
            dia = pipe(audio, min_speakers=self.min_speakers, max_speakers=self.max_speakers)
            result = whisperx.assign_word_speakers(dia, result)

        out: list[Segment] = []
        for s in result["segments"]:
            words = []
            for w in s.get("words", []):
                # 숫자 등 정렬 실패한 단어는 start/end가 없다 -> 건너뜀(세그먼트 경계로 커버)
                if "start" not in w or "end" not in w:
                    continue
                words.append(Word(
                    text=w["word"].strip(), start=float(w["start"]), end=float(w["end"]),
                    score=w.get("score"), speaker=w.get("speaker"),
                ))
            out.append(Segment(
                start=float(s["start"]), end=float(s["end"]), text=s["text"].strip(),
                speaker=s.get("speaker"), words=words,
            ))
        return Transcript(media_path=media.path, language=lang, backend=self.name, segments=out)


def get_backend(name: str, **kw: Any) -> Backend:
    kw = {k: v for k, v in kw.items() if v is not None}
    if name in ("faster-whisper", "fw"):
        kw.pop("diarize", None)
        return FasterWhisperBackend(**kw)
    if name == "whisperx":
        return WhisperXBackend(**kw)
    raise ValueError(f"알 수 없는 백엔드: {name}")


def transcribe_cached(media: MediaInfo, backend: Backend, cache: Cache) -> Transcript:
    params = backend.params()
    hit = cache.get(media.path, "transcript", params)
    if hit:
        return Transcript.from_dict(hit)
    t = backend.transcribe(media)
    cache.put(media.path, "transcript", t.to_dict(), params)
    return t
