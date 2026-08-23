"""Phase 1 - Data Inventory 생성.

data/raw, data/aoi, data/processed 아래의 raster/vector 파일을 스캔해
outputs/reports/data_inventory.csv 를 생성한다.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from .metadata import inspect_raster, inspect_vector

logger = logging.getLogger("validate")

RASTER_EXT = {".tif", ".tiff", ".img", ".jp2"}
VECTOR_EXT = {".gpkg", ".shp", ".geojson"}
SCAN_DIRS = ["data/raw", "data/aoi", "data/processed"]
FIELDNAMES = [
    "file_path", "data_type", "crs", "bounds",
    "width_or_feature_count", "height", "bands_or_geometry_type",
    "dtype", "nodata", "columns", "notes",
]


def scan_data_inventory(scan_dirs: list[str] = SCAN_DIRS) -> list[dict]:
    """지정 디렉터리들을 재귀적으로 스캔해 raster/vector 메타데이터 목록을 만든다."""
    rows: list[dict] = []
    for base in scan_dirs:
        base_path = Path(base)
        if not base_path.exists():
            continue
        for path in sorted(base_path.rglob("*")):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            try:
                if ext in RASTER_EXT:
                    rows.append(inspect_raster(path))
                elif ext in VECTOR_EXT:
                    rows.append(inspect_vector(path))
            except Exception as e:
                logger.warning("[DATA] 메타데이터 추출 실패: %s (%s)", path, e)
                rows.append({
                    "file_path": str(path), "data_type": "unknown",
                    "crs": "", "bounds": "", "width_or_feature_count": "",
                    "height": "", "bands_or_geometry_type": "", "dtype": "",
                    "nodata": "", "columns": "", "notes": f"ERROR: {e}",
                })
    return rows


def write_inventory_csv(rows: list[dict], out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("[DATA] Data Inventory 저장: %s (%d개 파일)", out_path, len(rows))
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    rows = scan_data_inventory()
    write_inventory_csv(rows, "outputs/reports/data_inventory.csv")
