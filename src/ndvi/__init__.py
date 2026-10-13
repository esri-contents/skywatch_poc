"""Sentinel-2 NDVI x 연속지적도 필지 단위 식생 변화 모니터링 (ArcGIS Pro 없이 실행 가능).

NDVI 감소는 식생/피복 상태 변화 신호이며 건축물 신축, 불법 개발, 보상 대상 지장물의
직접 증거가 아니다.
"""

import os
from pathlib import Path


def _use_bundled_proj_data() -> None:
    """시스템 PROJ_LIB(PostGIS 구버전 proj.db) 충돌 회피 - README 'Windows PROJ 충돌 주의' 참고.

    프로세스 로컬 환경변수만 바꾸며 시스템 설정은 건드리지 않는다. rasterio/pyproj가 번들한
    데이터가 있을 때만 적용한다.
    """
    import importlib.util

    spec = importlib.util.find_spec("rasterio")
    if spec and spec.origin:
        root = Path(spec.origin).parent
        proj = root / "proj_data"
        if proj.exists():
            os.environ["PROJ_LIB"] = str(proj)
            os.environ["PROJ_DATA"] = str(proj)
        gdal = root / "gdal_data"
        if gdal.exists():
            os.environ["GDAL_DATA"] = str(gdal)
    try:
        import pyproj.datadir

        spec_p = importlib.util.find_spec("pyproj")
        if spec_p and spec_p.origin:
            d = Path(spec_p.origin).parent / "proj_dir" / "share" / "proj"
            if d.exists():
                pyproj.datadir.set_data_dir(str(d))
    except Exception:  # noqa: BLE001
        pass


_use_bundled_proj_data()
