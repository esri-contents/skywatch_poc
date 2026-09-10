"""REQ10 - Web Map 공유 (LH 요구 산출물까지 포함한 확장판).

`src/publish/arcgis_online.py`(Baseline)가 만든 AGOL 연결/업로드/Web Map
빌더를 그대로 재사용하고, 이 파이프라인에서 새로 생긴 LH 업무용 레이어
(site_priority 현장조사 지점, 필지 단위 요약, 개발단계 블록, 보상기준일/
무허가 의심 스크리닝)를 Web Map에 추가로 얹는다. AGOL 인증·업로드 로직을
두 벌로 유지하지 않기 위해 Baseline 모듈을 import해서 쓴다.

산출물을 발행하려면 arcpy 결과(GDB Feature Class)를 먼저 WGS84 GeoJSON으로
내보내야 한다 - `export_wgs84_geojson()`이 그 변환을 맡는다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import zipfile

import arcpy

from src.publish.arcgis_online import (  # Baseline 인프라 재사용
    _find_existing_item,
    _get_folder,
    _operational_layer,
    apply_unique_value_renderer,
    connect_gis,
    publish_file_geodatabase_layer,
    publish_geojson_layer,
)

logger = logging.getLogger("arcpy_pipeline.webmap_publish")

SITE_PRIORITY_COLORS = {
    "HIGH": [230, 25, 75, 255],
    "MEDIUM": [245, 130, 48, 255],
    "LOW": [255, 220, 50, 255],
}
STAGE_COLORS = {
    "미착수": [200, 200, 200, 200],
    "부지조성": [255, 195, 0, 220],
    "건축진행": [230, 25, 75, 220],
    "마무리단계": [60, 160, 220, 220],
    "변화없음/완료": [100, 180, 100, 160],
}
COMPENSATION_COLORS = {
    "POST_BASELINE_UNVERIFIED": [200, 0, 0, 220],
    "STRADDLES_BASELINE": [230, 140, 0, 220],
    "UNKNOWN": [150, 150, 150, 200],
    "POST_BASELINE_PERMITTED": [80, 150, 220, 200],
    "PRE_BASELINE": [100, 180, 100, 160],
}
ILLEGAL_COLORS = {
    "A_STRONG": [180, 0, 0, 230],
    "B_MODERATE": [230, 120, 0, 220],
    "C_WEAK": [230, 200, 0, 200],
    "NONE": [180, 180, 180, 140],
}

SITE_POPUP_FIELDS = [
    "site_id", "site_priority", "dominant_change_type", "candidate_count",
    "building_count", "survey_focus", "compensation_status", "illegal_grade",
]


def export_wgs84_geojson(fc: str, out_path: str | Path) -> Path:
    """FileGDB Feature Class를 WGS84 GeoJSON으로 내보낸다.

    GeoJSON(RFC 7946)은 WGS84 좌표를 요구한다. 분석 좌표계(EPSG:5186,
    미터 단위) 그대로 저장하면 CRS 멤버를 무시하는 일부 업로드 경로에서
    좌표가 완전히 엉뚱한 위치로 표시된다(Baseline이 실측으로 확인하고
    고친 문제와 동일 - `src/pipeline.py`의 to_crs("EPSG:4326") 주석 참고).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wgs84 = arcpy.SpatialReference(4326)
    projected = arcpy.CreateScratchName("wgs84", "", "FeatureClass", arcpy.env.scratchGDB)
    arcpy.management.Project(fc, projected, wgs84)
    if out_path.exists():
        out_path.unlink()
    arcpy.conversion.FeaturesToJSON(
        projected, str(out_path), geoJSON="GEOJSON", outputToWGS84="WGS84"
    )
    arcpy.management.Delete(projected)
    logger.info("[PUBLISH] WGS84 GeoJSON 내보내기: %s -> %s", fc, out_path)
    return out_path


def export_file_gdb_zip(fc: str, out_path: str | Path, layer_name: str) -> Path:
    """Feature Class 하나를 WGS84 FileGDB ZIP으로 패키징한다.

    ArcGIS Enterprise에서 복잡한 GeoJSON 스키마 발행이 실패하는 경우를
    피하면서 원래 필드 타입과 긴 텍스트 필드를 보존한다.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdb = out_path.with_suffix(".gdb")
    if arcpy.Exists(str(gdb)):
        arcpy.management.Delete(str(gdb))
    arcpy.management.CreateFileGDB(str(gdb.parent), gdb.name)

    target = str(gdb / layer_name)
    arcpy.management.Project(fc, target, arcpy.SpatialReference(4326))

    if out_path.exists():
        out_path.unlink()
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in gdb.rglob("*"):
            # ArcPy가 워크스페이스 사용 중 생성한 잠금 파일은 배포 데이터가
            # 아니며 Windows에서 읽을 수도 없으므로 ZIP에서 제외한다.
            if file_path.is_file() and file_path.suffix.lower() != ".lock":
                archive.write(file_path, file_path.relative_to(gdb.parent))
    logger.info("[PUBLISH] FileGDB 패키지 생성: %s -> %s", fc, out_path)
    return out_path


def publish_lh_layers(
    gis: Any,
    exports: dict[str, Path],
    folder: str,
    title_prefix: str = "고양 창릉",
    overwrite: bool = False,
) -> dict[str, Any]:
    """LH 업무용 산출물 레이어들을 발행한다.

    Args:
        gis: connect_gis() 결과.
        exports: {"survey_sites": path, "parcel_change_summary": path,
                  "progress_by_block": path, "building_change_results": path,
                  "change_polygons": path, "aoi": path} 중 존재하는 것만.
        folder: AGOL Content 폴더명.

    Returns:
        {키: 발행된 Item} - 존재하는 것만.
    """
    titles = {
        "aoi": "공식 AOI",
        "change_polygons": "변화 탐지 폴리곤",
        "building_change_results": "건물 변화 결과",
        "survey_sites": "현장조사 우선순위 지점 (site 단위)",
        "parcel_change_summary": "필지 단위 변화 요약",
        "progress_by_block": "사업지구 개발 진행 블록",
    }
    items: dict[str, Any] = {}
    for key, path in exports.items():
        if not path or not Path(path).exists():
            continue
        if Path(path).suffix.lower() == ".zip":
            items[key] = publish_file_geodatabase_layer(
                gis, path, f"{title_prefix} {titles.get(key, key)}", folder, overwrite=overwrite
            )
        else:
            items[key] = publish_geojson_layer(
                gis, path, f"{title_prefix} {titles.get(key, key)}", folder, overwrite=overwrite
            )

    if "building_change_results" in items:
        apply_unique_value_renderer(
            items["building_change_results"], "change_type",
            __import__("src.publish.arcgis_online", fromlist=["CHANGE_TYPE_COLORS"]).CHANGE_TYPE_COLORS,
        )
    if "survey_sites" in items:
        apply_unique_value_renderer(items["survey_sites"], "site_priority", SITE_PRIORITY_COLORS)
    if "progress_by_block" in items:
        apply_unique_value_renderer(items["progress_by_block"], "development_stage", STAGE_COLORS)
    if "parcel_change_summary" in items:
        apply_unique_value_renderer(
            items["parcel_change_summary"], "worst_compensation_status", COMPENSATION_COLORS
        )

    return items


def build_lh_web_map(gis: Any, title: str, items: dict[str, Any], overwrite: bool = False) -> Any:
    """LH 업무 레이어를 전부 얹은 Web Map을 만든다.

    레이어 순서(아래→위): AOI → 개발단계 블록 → 변화 폴리곤 →
    필지 단위 요약 → 건물 변화 결과 → 현장조사 지점(가장 위, 항상 보이게).
    """
    layers = []
    if "aoi" in items:
        layers.append(_operational_layer(items["aoi"], items["aoi"].title))
    if "progress_by_block" in items:
        layers.append(_operational_layer(
            items["progress_by_block"], "개발 진행 단계",
            renderer_field="development_stage", value_colors=STAGE_COLORS,
        ))
    if "change_polygons" in items:
        layers.append(_operational_layer(items["change_polygons"], "변화 탐지 폴리곤"))
    if "parcel_change_summary" in items:
        layers.append(_operational_layer(
            items["parcel_change_summary"], "필지 단위 변화 요약 (보상기준일)",
            renderer_field="worst_compensation_status", value_colors=COMPENSATION_COLORS,
            popup_fields=["parcel_pnu", "parcel_jibun", "change_area_ratio",
                         "dominant_change_type", "worst_compensation_status", "worst_illegal_grade"],
        ))
    if "building_change_results" in items:
        cc = __import__("src.publish.arcgis_online", fromlist=["CHANGE_TYPE_COLORS", "POPUP_FIELDS"])
        layers.append(_operational_layer(
            items["building_change_results"], "건물 변화 결과",
            renderer_field="change_type", value_colors=cc.CHANGE_TYPE_COLORS,
            popup_fields=cc.POPUP_FIELDS,
        ))
    if "survey_sites" in items:
        layers.append(_operational_layer(
            items["survey_sites"], "현장조사 우선순위 지점",
            renderer_field="site_priority", value_colors=SITE_PRIORITY_COLORS,
            popup_fields=SITE_POPUP_FIELDS,
        ))

    import json as _json

    webmap_dict = {
        "operationalLayers": layers,
        "baseMap": {
            "baseMapLayers": [{
                "id": "world-imagery", "layerType": "ArcGISTiledMapServiceLayer",
                "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer",
                "title": "World Imagery",
            }],
            "title": "World Imagery",
        },
        "spatialReference": {"wkid": 4326},
        "version": "2.28",
    }

    existing = _find_existing_item(gis, title, "Web Map")
    if existing is not None:
        if not overwrite:
            logger.info("[PUBLISH] 기존 Web Map 재사용: %s", title)
            return existing
        logger.info("[PUBLISH] 기존 Web Map 교체: %s", title)
        existing.delete(permanent=True)

    folder_obj = _get_folder(gis, None)
    job = folder_obj.add(
        {"title": title, "type": "Web Map", "tags": "skywatch_poc,changneung,lh"},
        text=_json.dumps(webmap_dict, ensure_ascii=False),
    )
    webmap_item = job.result()
    logger.info("[PUBLISH] LH Web Map 발행 완료: %s -> %s", title, webmap_item.homepage)
    return webmap_item


def publish_all(
    exports: dict[str, Path],
    folder: str = "skywatch_poc_changneung",
    web_map_title: str = "고양 창릉 Building Change Intelligence - LH 업무 통합",
    title_prefix: str = "고양 창릉",
    overwrite: bool = False,
) -> dict[str, Any]:
    """명시 자격증명 또는 ArcGIS Pro 로그인으로 전체 레이어와 Web Map을 발행한다.

    둘 다 사용할 수 없으면 MissingCredentialsError를 그대로 올린다. 호출부는
    실패 사유를 outputs/reports/lh_traceability.md에 기록한다.
    """
    gis = connect_gis()
    items = publish_lh_layers(gis, exports, folder, title_prefix=title_prefix, overwrite=overwrite)
    if items:
        items["web_map"] = build_lh_web_map(gis, web_map_title, items, overwrite=overwrite)
    items["_portal"] = {
        "url": str(gis.url),
        "type": "ArcGIS Online" if "arcgis.com" in str(gis.url).lower() else "ArcGIS Enterprise",
        "username": gis.users.me.username,
    }
    items["_validation"] = validate_published_items(items)
    return items


def validate_published_items(items: dict[str, Any]) -> dict[str, Any]:
    """Read back services and Web Map so publication is evidence, not just an upload result."""
    validation: dict[str, Any] = {}
    for key, item in items.items():
        if key.startswith("_") or key == "web_map" or not hasattr(item, "layers"):
            continue
        try:
            layer = item.layers[0]
            props = layer.properties
            validation[key] = {
                "item_id": item.id,
                "url": item.homepage,
                "feature_count": int(layer.query(return_count_only=True)),
                "fields": [f["name"] for f in props.get("fields", [])],
                "spatial_reference": dict(props.get("extent", {}).get("spatialReference", {})),
                "geometry_type": props.get("geometryType"),
                "renderer": dict(props.get("drawingInfo", {}).get("renderer", {})),
            }
        except Exception as exc:
            validation[key] = {"error": f"{type(exc).__name__}: {exc}"}
    web_map = items.get("web_map")
    if web_map is not None:
        try:
            data = web_map.get_data() or {}
            validation["web_map"] = {
                "item_id": web_map.id,
                "url": web_map.homepage,
                "operational_layer_count": len(data.get("operationalLayers", [])),
                "connected_item_ids": [x.get("itemId") for x in data.get("operationalLayers", [])],
                "private": getattr(web_map, "access", None) == "private",
            }
        except Exception as exc:
            validation["web_map"] = {"error": f"{type(exc).__name__}: {exc}"}
    return validation
