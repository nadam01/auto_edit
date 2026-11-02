# AutoCut

프리미어 프로용 AI 러프컷 도구. 전체 설계는 [docs/PROJECT_SPEC.md](docs/PROJECT_SPEC.md).

**현재 상태: 개발 순서 1단계** — 수집(ffprobe) + 음성인식 + 대사 구간 이어붙이기 → FCP7 XML.

## 설치

필요: Python 3.11+, ffmpeg(ffprobe)가 PATH에 있어야 함.

**맥**
```bash
brew install ffmpeg python@3.12
```
```bash
pip install -e ".[whisper,mlx,dev]"   # 애플 실리콘이면 mlx-whisper(GPU)가 자동 선택됨
```

**윈도우** (PowerShell)
```powershell
winget install Gyan.FFmpeg Python.Python.3.12
```
```powershell
pip install -e ".[whisper,cuda,dev]"  # cuda: CUDA 12 cuBLAS + cuDNN 9를 pip로 설치 (Toolkit 불필요)
```
NVIDIA 드라이버만 최신이면 GPU를 자동으로 사용한다(`nvidia-smi`로 드라이버 확인).

> **"애플리케이션 제어 정책에서 이 파일을 차단했습니다"** 가 나오면 (윈도우 스마트 앱 컨트롤)
> `pip` 대신 `python -m pip`, `autocut` 대신 `python -m autocut` 으로 실행한다. 기능은 같다.
> 원본 폴더·가상환경은 OneDrive(바탕 화면, 문서) 밖, 예: `C:\dev\auto_edit` 에 둔다.

**설치 후 컴퓨터마다 한 번**
```bash
autocut doctor
```
ffmpeg, GPU 사용 여부, 선택된 음성인식 백엔드, config.toml 루트(드라이브 연결), 캐시 폴더를 점검한다.

**권장 분담**: 긴 촬영본 음성인식은 윈도우(NVIDIA GPU)에서, 캐시를 외장 드라이브에 두면
맥에서는 다시 분석하지 않고 바로 `cut`/`relink`만 하면 된다. 맥(애플 실리콘)도 mlx-whisper로 충분히 빠르다.

화자 분리가 필요하면 `.[whisperx]`도 추가(HF_TOKEN 필요).

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

## 맥 ↔ 윈도우 번갈아 쓰기

같은 외장 드라이브가 맥에서는 `/Volumes/T7`, 윈도우에서는 `E:\`로 보이므로
**두 컴퓨터 모두** `~/.autocut/config.toml`(윈도우는 `C:\Users\이름\.autocut\config.toml`)에 같은 내용을 적는다:

```toml
[roots.t7]                 # 이름은 자유. 드라이브/NAS마다 하나씩
mac = "/Volumes/T7"
windows = "E:/"

[roots.nas]
mac = "/Volumes/share"
windows = '\\nas\share'

# 선택: 분석 캐시를 외장 드라이브에 두면 한쪽에서 한 음성인식을 다른 쪽이 그대로 씀
[cache_dir]
mac = "/Volumes/T7/.autocut_cache"
windows = "E:/.autocut_cache"
```

- **캐시는 OS와 무관**: 파일 내용 지문으로 찾으므로 경로가 달라도 다시 분석하지 않는다.
  음성인식 결과도 백엔드와 무관하게 재사용(맥 mlx → 윈도우 faster-whisper). 모델을 바꿔 다시 돌릴 땐 `--force-asr`.
- **XML은 기본적으로 지금 쓰는 OS용**. 다른 OS에서 열 XML이 필요하면:
  ```bash
  autocut cut /Volumes/T7/촬영 -o rough.xml --target windows   # 또는 --target all → rough.mac.xml + rough.win.xml
  ```
- **이미 있는 XML(손으로 다듬어 프리미어에서 내보낸 것 포함) 변환**:
  ```bash
  autocut relink rough.mac.xml --target windows
  ```
  열 컴퓨터에서 `--target current`(기본)로 돌리면 실제 파일 존재까지 확인하고,
  한글 파일명의 맥(NFD)/윈도우(NFC) 정규화 차이도 자동 보정한다. **가장 안전한 방법.**

### 주요 옵션 (`cut`)
| 옵션 | 기본 | 설명 |
|---|---|---|
| `--gap` | 0.7 | 이 간격(초) 이하로 떨어진 발화는 한 컷으로 합침 |
| `--pad-before` / `--pad-after` | 0.15 / 0.25 | 컷 앞뒤 여유 |
| `--audio-tracks` | 2 | 오디오 채널을 몇 트랙까지 놓을지 |
| `--backend` | auto | 애플 실리콘 맥 → mlx-whisper, 그 외 → faster-whisper |
| `--model` | large-v3 | CPU만 있으면 `medium`/`small` 권장 |
| `--device` | auto | cuda / cpu (faster-whisper, whisperx) |
| `--target` | current | mac / windows / all |
| `--force-asr` | | 캐시 무시하고 음성인식 다시 |

캐시: 기본 `~/.autocut/cache` (`--cache-dir`, `AUTOCUT_CACHE_DIR`, config.toml `cache_dir` 순으로 지정).
중단 후 재실행하면 끝난 파일은 건너뛴다. 원본 폴더에는 아무것도 쓰지 않는다.

## 프리미어 확인 체크리스트 (smoke.xml)
- [ ] 미디어 오프라인 없이 바로 연결되는가 (한글/공백 경로 포함)
- [ ] 각 조각의 소스 타임코드가 원본 TC(예: 01:00:00;00 + n)와 일치하는가
- [ ] 비디오와 A1/A2가 링크되어 함께 움직이는가
- [ ] 클립 마커에 코멘트가 보이는가
- [ ] 29.97 / 23.976 / 25 소스 각각에서 길이·싱크가 맞는가
- [ ] 맥에서 만든 `--target windows` XML이 윈도우 프리미어에서 오프라인 없이 열리는가 (반대도)

## 알려진 제한 (1단계)
- 시퀀스 fps는 첫 비디오 소스 기준. 다른 fps 소스가 섞이면 경고만 한다.
- 오디오는 채널별 모노 트랙으로 배치(스테레오 → A1 L / A2 R).
- 컷 지점 보정(무음 스냅·핸들·크로스페이드)은 2단계.

## 구조
```
autocut/
  cli.py            doctor / probe / smoke / analyze / cut / relink  (reference / learn 예정)
  doctor.py         설치 상태 점검
  paths.py          맥↔윈도우 경로 변환, config.toml, pathurl
  models.py         MediaInfo, Transcript, Cut, Marker
  timecode.py       NTSC 레이트, 드롭프레임 TC
  ingest/probe.py   폴더 스캔 + ffprobe
  transcribe/       faster-whisper, whisperx, mlx-whisper
  edit/simple.py    대사 구간 이어붙이기
  export/fcp7.py    xmeml v4 생성
  cache/store.py    분석 캐시
```

## 테스트
```bash
pytest
```
ffmpeg로 합성 클립(29.97 DF 타임코드·한글 파일명, 모노/스테레오)을 만들어 검사한다.
