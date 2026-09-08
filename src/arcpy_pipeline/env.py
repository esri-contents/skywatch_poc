"""arcpy 실행 환경 - 라이선스, 워크스페이스(FileGDB), 설정 로딩.

arcpy를 쓰는 모든 모듈이 공통으로 필요로 하는 것을 한 곳에 모은다:

1. **Extension 라이선스**: Spatial Analyst(래스터 연산), Image Analyst,
   3D Analyst를 실제로 CheckOut/CheckIn 한다. CheckOut 없이 arcpy.sa를
   호출하면 런타임에 모호한 오류가 나므로, 파이프라인 시작 시점에
   명시적으로 확보하고 실패하면 즉시 사람이 읽을 수 있는 메시지로 중단한다.
2. **전역 env 설정**: outputCoordinateSystem / snapRaster / extent를
   여기서 한 번만 잡는다. 이 셋을 안 잡으면 ProjectRaster/ExtractByMask가
   도구마다 다른 grid를 만들어 T1/T2 픽셀이 어긋난다 - Baseline에서
   raster_preprocess.py가 수동으로 맞추던 것을 arcpy env로 대체한다.
3. **FileGDB 워크스페이스**: LH 업무환경은 shapefile이 아니라 FileGDB가
   기본이다. 필드명 10자 제한이 없고(shapefile 제약), Domain/Subtype,
   Attribute Rule을 쓸 수 있어 업무 데이터와 결합하기에 적합하다.

이 모듈은 arcpy import 자체를 지연시키지 않는다 - arcpy가 없는 환경에서는
애초에 이 패키지를 쓸 수 없고, 조용히 degrade 하는 것보다 즉시 실패하는
편이 낫기 때문이다. arcpy 없이 결과를 재현해야 하면 Baseline(src/*)을 쓴다.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path

import arcpy
import yaml

logger = logging.getLogger("arcpy_pipeline.env")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_REQUIREMENTS = PROJECT_ROOT / "config" / "lh_requirements.yaml"

# EPSG:5186 (Korea 2000 / Central Belt 2010). 선택 근거는 config.yaml의
# crs.decision_note 참고 - Baseline과 반드시 동일해야 결과를 대조할 수 있다.
ANALYSIS_EPSG = 5186
WGS84_EPSG = 4326


class LicenseError(RuntimeError):
    """필요한 arcpy Extension 라이선스를 확보하지 못했을 때 발생."""


def setup_logging(level: int = logging.INFO) -> None:
    """Baseline(src/pipeline.py)과 동일한 로그 포맷을 쓴다."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )


def load_config(config_path: str | Path = DEFAULT_CONFIG) -> dict:
    """config/config.yaml을 읽는다 (Baseline과 동일한 파일)."""
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_requirements(path: str | Path = DEFAULT_REQUIREMENTS) -> dict:
    """LH 요구사항 추적 매트릭스를 읽는다."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def analysis_sr() -> "arcpy.SpatialReference":
    return arcpy.SpatialReference(ANALYSIS_EPSG)


def wgs84_sr() -> "arcpy.SpatialReference":
    return arcpy.SpatialReference(WGS84_EPSG)


def ensure_gdb(gdb_path: str | Path) -> Path:
    """FileGDB가 없으면 만든다. 이미 있으면 그대로 쓴다.

    Args:
        gdb_path: 만들 .gdb 경로 (예: outputs/arcgis/changneung.gdb).

    Returns:
        생성/확인된 gdb 경로.
    """
    gdb_path = Path(gdb_path)
    if arcpy.Exists(str(gdb_path)):
        return gdb_path
    gdb_path.parent.mkdir(parents=True, exist_ok=True)
    arcpy.management.CreateFileGDB(str(gdb_path.parent), gdb_path.name)
    logger.info("[ENV] FileGDB 생성: %s", gdb_path)
    return gdb_path


def fc_path(gdb_path: str | Path, name: str) -> str:
    """FileGDB 안의 Feature Class / Table 전체 경로를 만든다."""
    return str(Path(gdb_path) / name)


def delete_if_exists(*paths: str) -> None:
    """존재하는 것만 골라 삭제한다 (overwriteOutput로 안 지워지는 잔여물 정리용)."""
    for p in paths:
        if p and arcpy.Exists(p):
            arcpy.management.Delete(p)


@contextmanager
def arcpy_session(
    workspace: str | Path,
    extensions: tuple[str, ...] = ("Spatial",),
    snap_raster: str | None = None,
    extent: str | None = None,
    cell_size: float | None = None,
    parallel: str = "80%",
):
    """라이선스 확보 + arcpy.env 설정 + 종료 시 CheckIn 까지 책임지는 컨텍스트.

    Args:
        workspace: arcpy.env.workspace로 쓸 FileGDB 또는 폴더.
        extensions: 확보할 Extension 목록 ("Spatial", "ImageAnalyst", "3D").
        snap_raster: 지정하면 arcpy.env.snapRaster로 설정 - T1/T2 grid를
            강제로 일치시킬 때 사용한다.
        extent: arcpy.env.extent (보통 AOI 경계).
        cell_size: arcpy.env.cellSize.
        parallel: arcpy.env.parallelProcessingFactor.

    Raises:
        LicenseError: 요청한 Extension을 확보하지 못한 경우.
    """
    checked_out: list[str] = []
    previous = {
        "workspace": arcpy.env.workspace,
        "overwriteOutput": arcpy.env.overwriteOutput,
        "outputCoordinateSystem": arcpy.env.outputCoordinateSystem,
        "snapRaster": arcpy.env.snapRaster,
        "extent": arcpy.env.extent,
        "cellSize": arcpy.env.cellSize,
        "parallelProcessingFactor": arcpy.env.parallelProcessingFactor,
    }
    try:
        for ext in extensions:
            status = arcpy.CheckExtension(ext)
            if status != "Available":
                raise LicenseError(
                    f"[ENV] '{ext}' Extension을 쓸 수 없습니다 (상태={status}). "
                    "ArcGIS Pro에서 해당 익스텐션 라이선스가 활성화되어 있는지 확인하세요."
                )
            arcpy.CheckOutExtension(ext)
            checked_out.append(ext)
        logger.info("[ENV] Extension 확보: %s", ", ".join(checked_out))

        arcpy.env.workspace = str(workspace)
        arcpy.env.overwriteOutput = True
        arcpy.env.outputCoordinateSystem = analysis_sr()
        arcpy.env.parallelProcessingFactor = parallel
        if snap_raster:
            arcpy.env.snapRaster = snap_raster
        if extent:
            arcpy.env.extent = extent
        if cell_size:
            arcpy.env.cellSize = cell_size

        yield arcpy.env
    finally:
        for ext in checked_out:
            arcpy.CheckInExtension(ext)
        for k, v in previous.items():
            setattr(arcpy.env, k, v)


def describe_fc(path: str) -> dict:
    """Feature Class의 핵심 메타데이터를 dict로 (data_inventory / 검증용)."""
    d = arcpy.Describe(path)
    return {
        "path": path,
        "type": d.dataType,
        "shape_type": getattr(d, "shapeType", None),
        "crs": getattr(getattr(d, "spatialReference", None), "name", None),
        "epsg": getattr(getattr(d, "spatialReference", None), "factoryCode", None),
        "count": int(arcpy.management.GetCount(path)[0]),
        "fields": [f.name for f in arcpy.ListFields(path)],
    }


def field_exists(path: str, field_name: str) -> bool:
    return any(f.name.lower() == field_name.lower() for f in arcpy.ListFields(path))


def add_field_if_missing(path: str, name: str, field_type: str, **kwargs) -> None:
    """이미 있으면 건너뛰는 AddField (재실행 안전성).

    파이프라인을 같은 GDB에 여러 번 돌릴 때 AddField가 에러를 내지 않도록
    한다 - overwriteOutput은 Feature Class 단위라 필드에는 적용되지 않는다.
    """
    if not field_exists(path, name):
        arcpy.management.AddField(path, name, field_type, **kwargs)


def resolve_path(p: str | Path) -> str:
    """상대경로를 프로젝트 루트 기준 절대경로로 바꾼다.

    arcpy는 상대경로를 arcpy.env.workspace 기준으로 해석하기 때문에,
    config/paths.yaml의 "프로젝트 루트 기준 상대경로"를 그대로 넘기면
    워크스페이스가 GDB로 바뀐 뒤 엉뚱한 곳을 가리킨다. 모든 외부 입력은
    이 함수를 통과시킨다.
    """
    p = Path(p)
    if p.is_absolute():
        return str(p)
    return str((PROJECT_ROOT / p).resolve())


def env_report() -> dict:
    """실행 환경 스냅샷 (run_manifest에 기록해 재현성 추적)."""
    info = arcpy.GetInstallInfo()
    return {
        "arcpy_version": info.get("Version"),
        "arcpy_build": info.get("BuildNumber"),
        "product": arcpy.ProductInfo(),
        "install_dir": info.get("InstallDir"),
        "python": os.sys.version.split()[0],
        "extensions": {
            ext: arcpy.CheckExtension(ext)
            for ext in ("Spatial", "ImageAnalyst", "3D")
        },
    }
