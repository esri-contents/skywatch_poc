"""arcpy_pipeline 테스트 전역 설정.

`src/arcpy_pipeline/*`는 module-level에서 `import arcpy`를 하기 때문에,
arcpy가 없는 환경(Baseline용 `.venv`, CI 등)에서 이 디렉터리를 수집하면
전부 ImportError로 죽는다. 이 파일이 있으면 pytest는 이 디렉터리를 별도
루트로 다루므로, 여기서 세션 시작 시 한 번 arcpy 존재 여부를 검사해 없으면
디렉터리 전체를 깔끔하게 skip한다 (테스트 실패가 아니라 "수집 안 됨"으로
보고되게 하기 위해 collect_ignore를 사용한다 - 개별 테스트마다
`pytest.importorskip`을 반복하지 않아도 된다).

ArcGIS Pro가 설치된 머신에서 이 스위트를 돌리려면 ArcGIS Pro 파이썬으로
직접 pytest를 실행해야 한다:

    "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" -m pytest tests_arcpy
"""

import importlib.util

collect_ignore_glob: list[str] = []

if importlib.util.find_spec("arcpy") is None:
    collect_ignore_glob = ["*"]
