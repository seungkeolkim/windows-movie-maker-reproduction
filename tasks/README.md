# 구현 작업 명세

이 디렉터리는 `W-06`~`W-11`의 독립 실행용 작업 명세를 보관한다. `W-06`은 완료됐고
`W-07`~`W-11`은 아직 구현되지 않았다. 각
작업을 수행하는 에이전트는 자신에게 지정된 파일 전체를 작업 목표로 사용하고, 파일에 연결된
승인 문서와 현재 코드를 확인한 뒤 요구사항을 충족하는 구현과 검증을 완료한다.

| 순서 | 작업 | 명세 | 핵심 결과 |
| --- | --- | --- | --- |
| `W-06` | 원본음·음악 믹싱 · 완료 | [w-06-audio-mixing.md](w-06-audio-mixing.md) | 실제 오디오 미리 듣기와 공통 믹싱 그래프 |
| `W-07` | FFmpeg MP4 출력 | [w-07-mp4-export.md](w-07-mp4-export.md) | H.264/AAC MP4 출력과 진행·취소·오류 처리 |
| `W-08` | 고급 타임라인 | [w-08-advanced-timeline.md](w-08-advanced-timeline.md) | 복제·다중 선택·그룹 편집·보기 전환·확대 |
| `W-09` | 창작 기능 | [w-09-creative-features.md](w-09-creative-features.md) | 내레이션·텍스트·전환·시각 효과 |
| `W-10` | 복구와 백그라운드 작업 | [w-10-recovery-and-background-jobs.md](w-10-recovery-and-background-jobs.md) | 자동 저장·복구·다시 연결·파형·프록시 |
| `W-11` | Windows 실행·설치 | [w-11-windows-launcher-and-installer.md](w-11-windows-launcher-and-installer.md) | 네이티브 런처와 설치·업데이트·제거 흐름 |

다음 작업은 `W-07`이다. 후속 작업은 선행 작업의 공개 계약을 재사용하며, 선행 기능을
별도의 임시 구현으로 복제하지 않는다. 요구사항 해석이 필요한 경우 제품 범위와 승인된 ADR을
우선하고, 장기 아키텍처 결정을 바꾸어야 할 때에는 기존 결정을 덮어쓰지 않고 새 ADR로 이유와
영향을 기록한다.

모든 작업에 공통으로 적용되는 품질 기준은 다음과 같다.

- 원본 미디어를 수정·이동·삭제하지 않는다.
- 지속되는 프로젝트 편집은 불변 프로젝트 값과 명령 실행 경계를 사용한다.
- 프로젝트 시간은 정수 나노초, 프레임률과 재생 속도는 유리수 계약을 유지한다.
- 셸 문자열 대신 구조화된 인자 목록으로 외부 프로세스를 실행한다.
- 오래 걸리는 작업은 UI 스레드를 막지 않고 취소와 종료 정리를 제공한다.
- 실패는 원인과 가능한 다음 행동을 한국어로 표시하며 프로젝트와 정상 파일을 보존한다.
- 새 동작은 집중 테스트와 기존 전체 회귀 테스트로 검증한다.
- 완료된 기능만 사용자 문서에서 지원한다고 표시한다.

공통 검증 명령은 다음과 같다.

```powershell
uv --managed-python run --locked --no-sync -- pytest
uv --managed-python run --locked --no-sync -- ruff check .
uv --managed-python run --locked --no-sync -- mypy
git diff --check
```
