# Third-party notices

현재 Windows 설치 패키지는 CPython, `.venv`, PySide6/Qt 또는 FFmpeg 실행 파일을 포함하지
않습니다. 런처와 설치 관리자는 Windows Win32 시스템 API만 사용하며 별도 .NET 런타임을
요구하지 않습니다.

Windows 패키지는 사용자가 패키지 관리 도구를 따로 설치하지 않도록 uv 0.12.1 `uv.exe`를
포함합니다. 이 파일은 Astral Software Inc.의 공식 Windows x64 릴리스에서 가져오며
MIT 또는 Apache-2.0으로 제공됩니다. 이 배포는 MIT 조건을 선택하고
`licenses/uv-LICENSE-MIT.txt`에 저작권 고지와 라이선스 전문을 함께 둡니다. 빌드 스크립트는
실행 파일 버전과 고정된 SHA-256을 모두 검증합니다.

사용자가 명시적으로 환경 구성을 승인하면 패키지의 uv가 `uv.lock`에 고정된 Python과 패키지를
각 배포처에서 받을 수 있습니다. 직접 의존성은 `pyproject.toml`, 전체 해상 결과는 `uv.lock`에서
확인할 수 있습니다. FFmpeg와 ffprobe는 사용자가 별도로 설치한 같은 배포본을 외부 프로세스로
사용합니다.

정식 배포 전 프로젝트 자체 라이선스와 의존성별 재배포 고지를 확정해야 합니다. 현재 정책과 검토
경계는 `docs/policies/policy-0001-licensing-and-output-rights.md`를 따릅니다.
