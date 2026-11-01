"""1단계 편집: 대사 구간을 그대로 이어붙인다.

- 단어 타임코드가 있으면 단어 기준, 없으면 세그먼트 기준
- 가까운 발화(gap_merge초 이하)는 한 컷으로 합친다
- 앞뒤 여유(pad) 후 원본 길이로 자른다
컷 지점 보정(무음 스냅·핸들·크로스페이드)은 2단계 edit/refine.py에서.
"""

from __future__ import annotations

from dataclasses import dataclass

from autocut.models import Cut, MediaInfo, Transcript


@dataclass
class SpeechCutOptions:
    gap_merge: float = 0.7
    pad_before: float = 0.15
    pad_after: float = 0.25
    min_duration: float = 0.6


def _spans(t: Transcript) -> list[tuple[float, float, str]]:
    spans: list[tuple[float, float, str]] = []
    for s in t.segments:
        if s.words:
            for w in s.words:
                if w.end > w.start:
                    spans.append((w.start, w.end, w.text))
        elif s.end > s.start:
            spans.append((s.start, s.end, s.text))
    spans.sort()
    return spans


def speech_cuts(media: MediaInfo, transcript: Transcript, opt: SpeechCutOptions | None = None) -> list[Cut]:
    opt = opt or SpeechCutOptions()
    groups: list[list] = []
    for start, end, text in _spans(transcript):
        if groups and start - groups[-1][1] <= opt.gap_merge:
            g = groups[-1]
            g[1] = max(g[1], end)
            g[2].append(text)
        else:
            groups.append([start, end, [text]])

    cuts: list[Cut] = []
    limit = media.duration or float("inf")
    for start, end, texts in groups:
        a = max(0.0, start - opt.pad_before)
        b = min(limit, end + opt.pad_after)
        # pad로 인해 이전 컷과 겹치면 중간에서 나눈다
        if cuts and a < cuts[-1].src_out:
            mid = (cuts[-1].src_out + a) / 2
            cuts[-1].src_out = mid
            a = mid
        if b - a < opt.min_duration:
            continue
        text = " ".join(t for t in texts if t).strip()
        cuts.append(Cut(media=media, src_in=a, src_out=b, label=text[:40], reason=text))
    return cuts
