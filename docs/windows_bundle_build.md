# Windows 묶음 빌드 — 유지보수 담당자용

폐쇄망 사용자는 빌드를 실행하지 않고 Git의 bundles/windows_cp312_win_amd64.zip을 사용한다. 설치 절차는 [usage.md](usage.md)를 따른다.

의존성 다운로드를 수행하는 빌드 컴퓨터에만 네트워크, uv 0.11.23, Python/pip가 필요하다. 저장소 루트에서 `python scripts/build_windows_bundle.py`로 CPython 3.12 win_amd64 wheel을 생성한다. Hatchling 1.27.0 고정, manifest의 wheel 해시·의존성을 검증한다. 생성물은 outputs/windows_cp312_win_amd64.zip이다.

ZIP에는 install_offline.cmd, verify_windows_bundle.py, requirements-windows.txt, manifest.json, wheelhouse 및 fixture를 포함한다. 검증 후 bundles/windows_cp312_win_amd64.zip에 복사하고 커밋한다. 기존 압축 해제 폴더는 Git 업데이트만으로 바뀌지 않는다. 새 ZIP은 새 폴더에 해제한다.

설치·CLI 도움말 확인과 수치 smoke/실자료 검증은 구분한다. 로컬 ZIP 무결성 검사를 Windows 실행 검증으로 표시하지 않는다.
