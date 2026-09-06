# 프로젝트 스크립트

스크립트가 늘어나더라도 서로 다른 책임이 한 디렉터리에 섞이지 않도록 기능별 하위 디렉터리를 사용합니다.

## 디렉터리

- `environment/`: 외부 도구 점검, uv 관리형 Python 및 `.venv` 구성, 애플리케이션 실행
- `mock/`: 인터랙티브 UI 목업의 기준 화면 캡처

목업 화면 캡처는 개발 환경을 준비한 뒤 저장소 루트에서 실행한다.

```powershell
uv --managed-python run --locked --no-sync -- python scripts/mock/capture_mock.py
```

새 자동화 영역을 추가할 때는 `scripts/` 루트에 파일을 바로 놓지 않고, 빌드·패키징·릴리스·미디어 픽스처 등 책임에 맞는 하위 디렉터리를 먼저 만듭니다. Windows PowerShell과 Linux Bash 구현이 같은 작업을 제공한다면 기본 옵션, 검사 항목과 종료 동작을 가능한 한 동일하게 유지합니다.
