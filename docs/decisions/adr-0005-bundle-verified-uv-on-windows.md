# ADR-0005: Windows 패키지에 검증된 uv 포함

- 상태: 승인됨
- 결정일: 2026-09-15
- 적용 대상: Windows 설치 패키지와 런처
- 대체 범위: [ADR-0001](adr-0001-python-uv-native-launcher.md)의 Windows 사용자가 uv를
  별도로 설치한다는 부분

## 배경

네이티브 런처는 릴리스 컴퓨터에서 미리 빌드하므로 사용자 PC에 .NET SDK나 C/C++ 빌드 도구가
필요하지 않다. 그러나 ADR-0001의 초기안은 첫 실행 전에 uv를 사용자가 별도로 설치하도록 했다.
Windows 사용자가 Python과 FFmpeg 외의 도구 설치 절차를 수행하지 않게 하면서도 고정 Python과
`uv.lock`의 재현성을 유지할 방법이 필요하다.

PowerShell 또는 배치 파일만 런처로 제공하는 대안은 별도 컴파일러를 요구하지 않지만, 상태 UI,
키보드 접근성, 파일 연결, 안전한 argv 전달과 프로세스 취소가 약해진다. 시스템 Python과 pip만
사용하면 uv 잠금 계약과 관리형 Python 패치 버전이 이중화된다.

## 결정

1. Windows x64 릴리스 payload에 공식 uv 0.12.1 `uv.exe`를 포함한다.
2. 버전, 공식 출처와 SHA-256은 `runtime-contract.json`에 고정한다.
3. 패키징은 지정된 실행 파일의 버전과 SHA-256이 모두 일치하지 않으면 실패한다.
4. 런처와 Windows 환경 스크립트는 앱 루트의 `tools/uv/uv.exe`를 시스템 PATH보다 우선한다.
5. uv 디렉터리 자체는 사용자 PATH에 등록하지 않고 제품 내부 절대 경로로만 실행한다.
6. uv는 Python과 잠긴 패키지를 자동으로 받지 않는다. 환경 구성·복구 동의 후에만 네트워크를
   사용할 수 있다.
7. uv 저작권 고지와 선택한 MIT 라이선스 전문을 패키지에 포함하고 버전 변경 때 고지를 다시
   검토한다.
8. CPython, `.venv`, PySide6/Qt와 FFmpeg/ffprobe는 계속 패키지에 포함하지 않는다.

## 결과

Windows 사용자는 uv, .NET SDK, Visual Studio Build Tools 또는 Zig를 따로 설치하지 않는다.
네이티브 런처 UI와 기존 uv 기반 재현성은 유지된다. 패키지는 uv 실행 파일만큼 커지고, 프로젝트는
공식 바이너리의 출처·해시·라이선스 고지를 업데이트할 책임을 갖는다. 소스 개발과 Linux 실행은
기존처럼 시스템에 설치된 uv를 사용한다.

## 제외한 대안

- PowerShell·배치 파일만을 Windows 사용자 런처로 제공
- 시스템 Python의 pip와 별도 requirements 잠금을 병행
- CPython과 전체 `.venv`를 설치 패키지에 포함
- 실행 시 동의 없이 최신 uv를 다운로드
