"""autocut 명령줄.

  autocut probe   SRC                  메타데이터만 확인
  autocut smoke   SRC -o smoke.xml     음성인식 없이 XML 연결 테스트 (프리미어 호환 확인용)
  autocut analyze SRC                  수집 + 음성인식 (캐시)
  autocut cut     SRC -o rough.xml     대사 구간 이어붙인 러프컷 XML
  autocut reference / learn            (예정: 개발 순서 5, 6단계)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from autocut.cache.store import Cache, probe_cached
from autocut.ingest.probe import ProbeError, scan
from autocut.models import Cut, MediaInfo


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _collect(args) -> tuple[Cache, list[MediaInfo]]:
    cache = Cache(args.cache_dir)
    files = scan(args.src, recursive=not args.no_recursive)
    if not files:
        raise SystemExit(f"미디어 파일이 없습니다: {args.src}")
    medias: list[MediaInfo] = []
    for i, f in enumerate(files, 1):
        try:
            m = probe_cached(f, cache)
        except ProbeError as e:
            _log(f"  [건너뜀] {e}")
            continue
        _log(f"[{i}/{len(files)}] {m.name}  {m.duration:7.1f}s  "
             + (f"{m.fps:.3f}fps {m.width}x{m.height}  " if m.has_video else "audio-only  ")
             + f"{m.audio_channels}ch  TC {m.start_timecode or '-'}")
        medias.append(m)
    return cache, medias


def _backend(args):
    from autocut.transcribe.backends import get_backend

    return get_backend(
        args.backend, model=args.model, language=args.language, device=args.device,
        **({"diarize": args.diarize} if args.backend == "whisperx" else {}),
    )


def _transcribe_all(cache: Cache, medias: list[MediaInfo], args):
    from autocut.transcribe.backends import BackendUnavailable, transcribe_cached

    backend = _backend(args)
    out = {}
    targets = [m for m in medias if m.audio_channels]
    for i, m in enumerate(targets, 1):
        t0 = time.time()
        _log(f"[음성인식 {i}/{len(targets)}] {m.name} ...")
        try:
            t = transcribe_cached(m, backend, cache)
        except BackendUnavailable as e:
            raise SystemExit(str(e))
        n_words = sum(len(s.words) for s in t.segments)
        _log(f"    세그먼트 {len(t.segments)}, 단어 {n_words}, {time.time() - t0:.1f}s")
        out[m.path] = t
    return out


def cmd_probe(args) -> None:
    _collect(args)


def cmd_smoke(args) -> None:
    """음성인식 없이 각 파일을 3조각(앞/중간/뒤)으로 잘라 XML 생성.
    프리미어에서 경로 연결·in/out·링크·마커가 맞는지 먼저 확인하는 용도."""
    from autocut.export.fcp7 import ExportOptions, write_xmeml

    _, medias = _collect(args)
    cuts: list[Cut] = []
    for m in medias:
        d = m.duration
        piece = min(args.piece, d / 3)
        for k, start in enumerate((0.0, d / 2 - piece / 2, d - piece)):
            cuts.append(Cut(
                media=m, src_in=max(0.0, start), src_out=min(d, start + piece),
                label=f"{m.name} #{k + 1}",
                reason=f"smoke test: 원본 {start:.2f}s~{start + piece:.2f}s",
            ))
    out = write_xmeml(cuts, args.output, ExportOptions(sequence_name=args.name or "AutoCut smoke"))
    _log(f"완료: {out}  (컷 {len(cuts)}개)")


def cmd_analyze(args) -> None:
    cache, medias = _collect(args)
    _transcribe_all(cache, medias, args)
    _log(f"분석 완료. 캐시: {cache.root}")


def cmd_cut(args) -> None:
    from autocut.edit.simple import SpeechCutOptions, speech_cuts
    from autocut.export.fcp7 import ExportOptions, write_xmeml

    cache, medias = _collect(args)
    transcripts = _transcribe_all(cache, medias, args)
    opt = SpeechCutOptions(gap_merge=args.gap, pad_before=args.pad_before, pad_after=args.pad_after)
    cuts: list[Cut] = []
    for m in medias:
        t = transcripts.get(m.path)
        if t:
            cuts.extend(speech_cuts(m, t, opt))
    if not cuts:
        raise SystemExit("대사 구간을 찾지 못했습니다.")
    total = sum(c.duration for c in cuts)
    src_total = sum(m.duration for m in medias)
    out = write_xmeml(cuts, args.output, ExportOptions(
        sequence_name=args.name or Path(args.output).stem,
        max_audio_tracks=args.audio_tracks,
        clip_markers=not args.no_markers,
    ))
    _log(f"완료: {out}  컷 {len(cuts)}개, {total:.1f}s (원본 {src_total:.1f}s의 {total / max(src_total, 1e-9):.0%})")


def cmd_todo(args) -> None:
    raise SystemExit(f"'{args.cmd}'는 아직 구현 전입니다 (PROJECT_SPEC 개발 순서 5~6단계).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="autocut", description="프리미어 프로용 AI 러프컷")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("src", help="소스 폴더 또는 파일")
        sp.add_argument("--cache-dir", default=None, help="분석 캐시 위치 (기본 ~/.autocut/cache)")
        sp.add_argument("--no-recursive", action="store_true", help="하위 폴더 제외")

    def asr(sp):
        sp.add_argument("--backend", default="faster-whisper", choices=["faster-whisper", "whisperx"])
        sp.add_argument("--model", default="large-v3")
        sp.add_argument("--language", default="ko")
        sp.add_argument("--device", default="auto", help="auto / cuda / cpu")
        sp.add_argument("--diarize", action="store_true", help="화자 분리 (whisperx + HF_TOKEN)")

    sp = sub.add_parser("probe", help="메타데이터 확인")
    common(sp)
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("smoke", help="음성인식 없이 XML 호환성 테스트")
    common(sp)
    sp.add_argument("-o", "--output", default="autocut_smoke.xml")
    sp.add_argument("--name", default=None)
    sp.add_argument("--piece", type=float, default=3.0, help="조각 길이(초)")
    sp.set_defaults(func=cmd_smoke)

    sp = sub.add_parser("analyze", help="수집 + 음성인식")
    common(sp)
    asr(sp)
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("cut", help="대사 구간 러프컷 XML")
    common(sp)
    asr(sp)
    sp.add_argument("-o", "--output", default="autocut_rough.xml")
    sp.add_argument("--name", default=None, help="시퀀스 이름")
    sp.add_argument("--gap", type=float, default=0.7, help="이 간격(초) 이하 발화는 한 컷으로")
    sp.add_argument("--pad-before", type=float, default=0.15)
    sp.add_argument("--pad-after", type=float, default=0.25)
    sp.add_argument("--audio-tracks", type=int, default=2, help="최대 오디오 트랙(채널) 수")
    sp.add_argument("--no-markers", action="store_true")
    sp.set_defaults(func=cmd_cut)

    for name in ("reference", "learn"):
        sp = sub.add_parser(name, help="(예정)")
        sp.add_argument("rest", nargs="*")
        sp.set_defaults(func=cmd_todo)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
