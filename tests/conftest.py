"""테스트 전역 설정.

이 개발 환경은 시스템 전역 PROJ_LIB이 PostgreSQL/PostGIS의 구버전 proj.db를
가리키고 있어(README "Windows PROJ 충돌 주의" 참고), CRS가 있는 GeoTIFF를
실제로 열거나 쓰는 테스트가 PROJ_LIB을 명시하지 않으면 CRSError로 실패한다.
매번 `PROJ_LIB=... pytest`로 실행하는 걸 잊기 쉬우므로, rasterio가 번들한
proj_data가 있으면 자동으로 가리키도록 임포트 시점에 강제한다.
"""

from pathlib import Path

import rasterio

_bundled_proj = Path(rasterio.__file__).parent / "proj_data"
if _bundled_proj.exists():
    import os

    os.environ["PROJ_LIB"] = str(_bundled_proj)
    os.environ["GDAL_DATA"] = ""
