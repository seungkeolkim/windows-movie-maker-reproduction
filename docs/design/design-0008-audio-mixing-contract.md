# 원본음·음악 믹싱 계약

- 문서 번호: `DESIGN-0008`
- 상태: 승인됨
- 작업: `W-06` 원본음·음악 믹싱
- 작성일: 2026-09-11
- 선행 계약:
  [DESIGN-0003](design-0003-project-core-contract.md),
  [DESIGN-0005](design-0005-project-persistence-contract.md),
  [DESIGN-0006](design-0006-timeline-editing-contract.md),
  [DESIGN-0007](design-0007-preview-playback-contract.md)
- 아키텍처 결정:
  [ADR-0002](../decisions/adr-0002-command-based-edit-history.md),
  [ADR-0003](../decisions/adr-0003-project-core-state-and-storage.md)

## 목적과 범위

W-06은 영상 클립의 원본음과 고정 음악 트랙을 실제 프로젝트 상태, FFmpeg 믹싱과 Qt 오디오
출력에 연결한다. 재생·일시 정지·탐색과 편집 뒤 갱신은 W-05와 같은 `ProjectTime` 및 단조
시계를 사용한다. W-07은 UI나 목업 상태를 거치지 않고 같은 오디오 그래프와 FFmpeg 인자
생성기를 내보내기에 재사용한다.

마이크 녹음, 내레이션, 파형, 프록시, 페이드, 자동 더킹, 텍스트·전환·시각 효과와 MP4 생성은
이 계약의 범위가 아니다. 관련 1.0 화면은 목업임을 계속 표시한다.

## 모듈 경계

| 모듈 | 책임 |
| --- | --- |
| `project/model.py` | 결정적인 음량 값과 클립 음소거 지속 상태 |
| `timeline/editing.py` | 오디오 속성과 타이밍을 한 명령으로 적용하고 undo/redo 제공 |
| `project/persistence.py` | 스키마 1의 호환 가능한 선택 필드 저장·기본값 복원 |
| `audio/graph.py` | 프로젝트 구간을 UI 독립 오디오 소스와 공통 FFmpeg 필터로 해석 |
| `audio/decoder.py` | 취소 가능한 FFmpeg PCM 디코딩과 타입이 있는 실패 |
| `audio/coordinator.py` | 제한된 작업자와 최신 요청만 전달하는 세대 관리 |
| `ui/audio.py` | Qt 스레드 신호 전달과 주입 가능한 48 kHz 스테레오 출력 |
| `ui/main_window.py` | 재생 시간과 PCM 위치 동기화, 세션 음소거 및 사용자 피드백 |

프로젝트 코어는 Qt, 오디오 장치와 FFmpeg를 import하지 않는다. 오디오 그래프는 Qt나 목업
모델을 import하지 않으므로 미리 듣기와 W-07 출력이 같은 의미를 사용한다.

## 프로젝트 모델과 저장 호환성

`AudioLevel`은 `0~100`의 정수 백분율을 가진 불변 값이다. 부동소수점 값을 받지 않으며
FFmpeg 경계에서만 정확한 유리수를 소수 문자열로 바꾼다. `Clip.audio_level`의 기본값은
`100`, `Clip.audio_muted`의 기본값은 `false`다. 음소거는 음량을 0으로 덮어쓰지 않으므로
해제해도 이전 음량이 유지된다.

영상과 음악 클립은 오디오 값을 가질 수 있다. 사진과 텍스트는 기본 오디오 값만 허용한다.
분할은 앞뒤 결과에 기존 값을 복사하고 이동·트리밍·속도 변경은 값을 보존한다. 음악 클립은
시각 리플과 무관한 절대 `timeline_start`를 유지하며 프로젝트 길이를 늘리지 않는다.

스키마 버전은 `1`을 유지하고 각 클립에 다음 선택 필드를 기록한다.

```json
{
  "audio_level_percent": 75,
  "audio_muted": false
}
```

필드가 없는 기존 문서는 `100`, `false`로 읽는다. 필드 타입이나 범위가 잘못되면 문서 전체를
거부한다. 선택, 재생 위치, 미리 보기 음소거, 장치, PCM 버퍼와 디코딩 작업은 기록하지 않는다.

## 명령과 원자성

`UpdateClipAudio`는 음량 또는 음소거만 바꾸며 지정하지 않은 값을 보존한다.
`UpdateClipTiming`은 속성 패널의 source in/out, 절대 시작, 속도, 음량과 음소거를 한 번에
적용할 수 있다. 한 번의 사용자 적용은 하나의 `ProjectCommand`와 이력 항목만 만들며 결과
클립과 이전 클립만 가진 최소 역명령으로 되돌린다.

입력 검증과 새 `Project` 생성이 모두 성공한 뒤에만 실행기가 프로젝트와 이력 위치를 함께
교체한다. 명령, undo 또는 redo 실패는 프로젝트, dirty 기준과 저장 파일을 바꾸지 않는다.

## 오디오 그래프 의미

`build_audio_graph`는 프로젝트의 반열린 범위 `[start, start + duration)`를 해석한다. 범위는
시각 트랙의 프로젝트 길이에서 잘리며 그 밖의 음악은 샘플을 만들지 않는다. 영상은 첫 실제
오디오 스트림을, 오디오 미디어는 분석 시 지정한 대표 오디오 스트림을 명시적으로 선택한다.
오디오 스트림이 없는 영상은 정상 무음이고 오디오 미디어에 스트림이 없으면 타입이 있는
오류다. 내레이션 트랙은 W-09 전까지 그래프에 포함하지 않는다.

클립 시작 `C`, source in `I`, 재생 속도 `R`, 프로젝트 위치 `T`의 source 위치는 W-05와 같이
다음 식으로 매번 절대 위치에서 계산한다.

```text
S = I + (T - C) × R
```

모든 계산은 정수 나노초와 `Fraction`을 사용한다. 결과 시작 위치는 48 kHz 샘플 격자에서 가장
가까운 정수로 반올림하고 정확히 절반이면 뒤 샘플을 선택한다. 현재 영상 속도 `1/2`, `1`,
`3/2`, `2`는 FFmpeg `atempo`로 원본음과 함께 변경하며 지원 밖 속도는 조용히 대체하지 않는다.

각 소스는 source 구간을 `atrim`하고 PTS를 0으로 맞춘 뒤 속도, 48 kHz 리샘플링, signed
16-bit 스테레오 정규화, 음량과 타임라인 지연을 적용한다. 여러 소스는 자동 정규화 없이
합산하고 `0.95` 리미터를 거쳐 클리핑을 제한한다. 마지막에는 프로젝트 범위만큼 무음을
패딩하고 정확한 길이로 자른다. 소스가 없으면 같은 형식과 길이의 결정적인 무음을 만든다.

## FFmpeg 및 원본 안전

원본 경로와 실행 파일은 셸 문자열에 합치지 않고 `shell=False` 프로세스의 argv로 전달한다.
필터 문자열에는 검증된 정수, 유리수, 고정 레이블만 넣고 입력 스트림은 `[입력:스트림]`으로
지정한다. stdin은 닫고 PCM은 stdout, 진단은 stderr로 분리한다. 프로세스는 원본을 읽기
전용 입력으로만 사용하며 임시 미디어, 데이터베이스 또는 출력 파일을 만들지 않는다.

Windows와 Linux 환경 점검은 `atrim`, `asetpts`, `aformat`, `atempo`, `aresample`, `adelay`,
`volume`, `amix`, `alimiter`, `apad`, `anull`과 `anullsrc`를 확인한다.

## 미리 듣기, 동시성 및 장치

FFmpeg 디코딩과 프로세스 대기는 단일 작업자에서 수행한다. 각 요청은 증가하는 세대와 취소
이벤트를 가지며 새 요청은 이전 프로세스를 terminate하고 제한 시간 뒤 kill한다. 완료 결과는
현재 세대이고 서비스가 열려 있을 때만 Qt 신호로 전달한다. 취소와 오래된 결과는 사용자
오류로 표시하지 않는다.

미리 듣기는 최대 30초의 PCM 구간을 요청한다. 디코딩 중에도 W-05 단조 시계가 진행되므로
결과가 도착하면 현재 `ProjectTime` 이전의 샘플을 정확히 버리고 남은 PCM만 장치에 전달한다.
재생, 일시 정지, 탐색, 프로젝트 편집·교체, 미리 보기 음소거와 창 종료는 진행 중 작업과
장치 버퍼를 취소하거나 새 세대로 교체한다.

`AudioOutput` 프로토콜은 테스트에서 실제 장치 없이 주입할 수 있다. 운영 구현은 Qt의 현재
기본 장치에 48 kHz signed 16-bit 스테레오 PCM을 전달한다. 장치 없음, 형식 미지원과 버퍼
열기 실패는 프로젝트 음량과 구분되는 세션 오류다. 미리 보기 음소거도 세션 값이며 프로젝트
JSON과 행동 이력을 바꾸지 않는다.

## 오류 계약

| 코드 또는 경계 | 의미 |
| --- | --- |
| `invalid_range` | 프로젝트 밖 또는 길이가 없는 요청 |
| `no_audio_stream` | 오디오 미디어의 대표 스트림 없음 |
| `unsupported_speed` | 현재 그래프가 지원하지 않는 영상 원본음 속도 |
| `source_not_found` | 원본 경로 누락 |
| `ffmpeg_not_found` | FFmpeg 실행 파일 없음 |
| `filter_unavailable` | 필수 FFmpeg 오디오 필터 없음 |
| `unsupported_media` | 코덱 또는 샘플 형식 미지원 |
| `timeout` | 제한 시간 안에 디코딩되지 않음 |
| `process_failed`, `invalid_audio` | 프로세스 실패 또는 잘못된 PCM |
| 출력 장치 오류 | 기본 장치 없음, 형식 또는 버퍼 열기 실패 |

모든 실패는 원인과 다음 행동을 한국어로 표시한다. 실패, 취소와 오래된 결과 폐기는 프로젝트,
이력, dirty, 저장 파일과 원본 파일을 바꾸지 않는다.

## 구현 및 검증 결과

| 계약 | 자동 검증 |
| --- | --- |
| 음량 범위, 음소거 보존, 명령 원자성, undo/redo | `tests/project/test_audio_properties.py` |
| source 시간, 속도, 음악 배치·절단과 필터 argv | `tests/audio/test_graph.py` |
| 실패 분류, 제한 시간, terminate/kill과 PCM 검증 | `tests/audio/test_audio_decoder.py` |
| 최신 요청, 대기 작업 취소와 종료 콜백 억제 | `tests/audio/test_audio_coordinator.py` |
| 다중 샘플률·채널 실제 FFmpeg 혼합과 원본 불변 | `tests/audio/test_audio_real_media.py` |
| Qt 재생·음소거·장치 실패·편집 갱신과 저장 왕복 | `tests/ui/test_audio_playback_integration.py` |

FFmpeg 9.0.1에서 44.1 kHz AAC 영상 원본음과 32 kHz 스테레오 PCM 음악을 생성해 48 kHz
스테레오로 혼합했다. 음악의 0.25초 절대 배치 전 무음, 이후 실제 샘플, 채널 정규화, 반복
결과 일치와 전후 SHA-256·크기·수정 시간을 검증했다. W-06 완료 시 전체 189개 pytest,
Ruff, strict mypy, 애플리케이션 `--check`, Windows FFmpeg 환경 점검과 `git diff --check`가
통과했다.

다음 작업은 이 공통 그래프를 사용하는 `W-07` H.264/AAC MP4 출력이다.
