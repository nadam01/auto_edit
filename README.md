# AutoCut

프리미어 프로용 AI 러프컷 도구. 전체 설계는 [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md).

**현재 상태: 개발 순서 1단계** — 수집(ffprobe) + 음성인식 + 대사 구간 이어붙이기 → FCP7 XML.

## 설치

필요: Python 3.11+, ffmpeg(ffprobe)가 PATH에 있어야 함.

```bash
pip install -e ".[whisper,dev]"      # faster-whisper (권장 시작점)
# 또는
pip install -e ".[whisperx,dev]"     # 단어 경계 정밀 정렬 + 화자 분리
```

## 사용

```bash
# 0) 메타데이터 확인
autocut probe /path/to/footage

# 1) 음성인식 없이 XML 호환성부터 확인 — 파일마다 앞/중간/뒤 3조각
autocut smoke /path/to/footage -o smoke.xml

# 2) 대사 구간 러프컷
autocut cut /path/to/footage -o rough.xml
autocut cut /path/to/footage -o rough.xml --backend whisperx --diarize   # HF_TOKEN 필요
```

프리미어: **파일 > 가져오기** 로 XML 선택 → 시퀀스가 생성되고 원본 파일에 그대로 연결된다.

### 주요 옵션 (`cut`)
| 옵션 | 기본 | 설명 |
|---|---|---|
| `--gap` | 0.7 | 이 간격(초) 이하로 떨어진 발화는 한 컷으로 합침 |
| `--pad-before` / `--pad-after` | 0.15 / 0.25 | 컷 앞뒤 여유 |
| `--audio-tracks` | 2 | 오디오 채널을 몇 트랙까지 놓을지 |
| `--model` | large-v3 | CPU면 `medium`/`small` 권장 |
| `--device` | auto | cuda / cpu |

캐시: `~/.autocut/cache` (`--cache-dir` 또는 `AUTOCUT_CACHE_DIR`). 원본 경로·크기·수정시각 기준이라
원본이 바뀌면 자동 재분석, 중단 후 재실행하면 끝난 파일은 건너뛴다. 원본 폴더에는 아무것도 쓰지 않는다.

## 프리미어 확인 체크리스트 (smoke.xml)
- [ ] 미디어 오프라인 없이 바로 연결되는가 (한글/공백 경로 포함)
- [ ] 각 조각의 소스 타임코드가 원본 TC(예: 01:00:00;00 + n)와 일치하는가
- [ ] 비디오와 A1/A2가 링크되어 함께 움직이는가
- [ ] 클립 마커에 코멘트가 보이는가
- [ ] 29.97 / 23.976 / 25 소스 각각에서 길이·싱크가 맞는가

## 알려진 제한 (1단계)
- 시퀀스 fps는 첫 비디오 소스 기준. 다른 fps 소스가 섞이면 경고만 한다.
- 오디오는 채널별 모노 트랙으로 배치(스테레오 → A1 L / A2 R).
- 컷 지점 보정(무음 스냅·핸들·크로스페이드)은 2단계.

## 구조
```
autocut/
  cli.py            probe / smoke / analyze / cut  (reference / learn 예정)
  models.py         MediaInfo, Transcript, Cut, Marker
  timecode.py       NTSC 레이트, 드롭프레임 TC
  ingest/probe.py   폴더 스캔 + ffprobe
  transcribe/       faster-whisper, whisperx
  edit/simple.py    대사 구간 이어붙이기
  export/fcp7.py    xmeml v4 생성
  cache/store.py    분석 캐시
```

## 테스트
```bash
pytest
```
ffmpeg로 합성 클립(29.97 DF 타임코드·한글 파일명, 모노/스테레오)을 만들어 검사한다.
