"""ArcGIS Online 자동 발행 스크립트.

`outputs/vectors/*.geojson`, `outputs/rasters/*.tif`, `data/processed/imagery/*.tif`를
ArcGIS Online에 업로드해 Hosted Feature Layer / Tile Layer로 발행하고, 두 벌의
심볼로지(change_type 카테고리, inspection_priority 3단계)를 적용한 뒤 Web Map으로
묶는다. `outputs/reports/handoff.md` 9번 섹션의 수동 절차를 그대로 자동화한 것이다.

인증은 `.env`의 AGOL_USERNAME / AGOL_PASSWORD (필요 시 AGOL_PORTAL_URL)를 사용한다.
Key/비밀번호를 코드에 하드코딩하지 않는다 (src/data/download.py와 동일한 원칙).

사전 준비:
    pip install arcgis   (requirements.txt에 추가됨)

사용 예:
    python -m src.publish.arcgis_online --include-buildings --build-webmap
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger("publish.arcgis_online")

load_dotenv()

AGOL_USERNAME = os.getenv("AGOL_USERNAME")
AGOL_PASSWORD = os.getenv("AGOL_PASSWORD")
AGOL_PORTAL_URL = os.getenv("AGOL_PORTAL_URL", "https://www.arcgis.com")


class MissingCredentialsError(RuntimeError):
    """AGOL_USERNAME/AGOL_PASSWORD가 .env에 설정되지 않았을 때 발생."""


# handoff.md 4번 섹션 "변화유형 분류" / 5번 섹션 우선순위 등급과 동일한 값.
CHANGE_TYPE_COLORS: dict[str, list[int]] = {
    "NEW_BUILDING": [230, 25, 75, 255],
    "EXPANSION_OR_RECONSTRUCTION": [245, 130, 48, 255],
    "OTHER_CHANGE": [120, 120, 120, 255],
    "DEMOLITION": [70, 70, 200, 255],
}

PRIORITY_COLORS: dict[str, list[int]] = {
    "HIGH": [230, 25, 75, 255],
    "MEDIUM": [245, 130, 48, 255],
    "LOW": [255, 220, 50, 255],
}

POPUP_FIELDS = [
    "change_type",
    "priority_score",
    "site_id",
    "classification_note",
    "useAprDay",
]


def connect_gis() -> Any:
    """AGOL_USERNAME/PASSWORD로 GIS에 연결한다.

    Returns:
        arcgis.gis.GIS 인스턴스.
    """
    from arcgis.gis import GIS

    if not AGOL_USERNAME or not AGOL_PASSWORD:
        raise MissingCredentialsError(
            "[PUBLISH] AGOL_USERNAME / AGOL_PASSWORD가 설정되어 있지 않습니다. "
            ".env 파일에 두 값을 추가한 뒤 다시 실행하세요 (.env.example 참고)."
        )
    gis = GIS(AGOL_PORTAL_URL, AGOL_USERNAME, AGOL_PASSWORD)
    logger.info("[PUBLISH] 연결 완료: %s (사용자=%s)", AGOL_PORTAL_URL, gis.users.me.username)
    return gis


def _find_existing_item(gis: Any, title: str, item_type: str) -> Any | None:
    query = f'title:"{title}" AND owner:{gis.users.me.username}'
    for item in gis.content.search(query=query, item_type=item_type, max_items=10):
        if item.title == title:
            return item
    return None


def _get_folder(gis: Any, folder_name: str | None) -> Any:
    """폴더 객체를 가져오거나 없으면 만든다.

    이 arcgis 버전(2.4.3)에서는 `gis.content.add()`(구 API)가
    `arcgis.features.geo._is_geoenabled` 속성 누락으로 항상 실패하는
    버그가 실측 확인되어, 신규 API인 `Folder.add()`를 사용한다.
    """
    if not folder_name:
        return gis.content.folders.get()
    return gis.content.folders.create(folder_name, exist_ok=True)


def publish_geojson_layer(
    gis: Any,
    file_path: str | Path,
    title: str,
    folder: str | None = None,
    overwrite: bool = True,
) -> Any:
    """GeoJSON 파일을 업로드하고 Hosted Feature Layer로 발행한다.

    같은 제목의 Feature Layer/GeoJSON 아이템이 이미 있으면(overwrite=True)
    지우고 새로 발행한다 - 재실행 시 중복 아이템이 쌓이지 않게 하기 위함.

    Args:
        gis: connect_gis()로 얻은 GIS 인스턴스.
        file_path: WGS84 GeoJSON 경로 (outputs/vectors/*.geojson).
        title: AGOL에 표시할 아이템 제목.
        folder: 업로드할 Content 폴더명 (없으면 루트).
        overwrite: True면 동일 제목의 기존 아이템을 삭제 후 재발행.

    Returns:
        발행된 Hosted Feature Layer의 arcgis.gis.Item.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(
            f"[PUBLISH] {file_path} 가 없습니다. 먼저 pipeline을 실행해 "
            "outputs/vectors/*.geojson을 생성하세요 (handoff.md 3번 섹션 참고)."
        )

    if overwrite:
        for item_type in ("GeoJson", "Feature Service"):
            existing = _find_existing_item(gis, title, item_type)
            if existing is not None:
                logger.info("[PUBLISH] 기존 아이템 삭제: %s (%s)", title, item_type)
                existing.delete(permanent=True)

    item_properties = {"title": title, "type": "GeoJson", "tags": "skywatch_poc,changneung"}
    folder_obj = _get_folder(gis, folder)
    job = folder_obj.add(item_properties, file=str(file_path))
    source_item = job.result()
    logger.info("[PUBLISH] 업로드 완료: %s (item_id=%s)", title, source_item.id)

    published_item = source_item.publish()
    logger.info("[PUBLISH] 발행 완료: %s -> %s", title, published_item.homepage)
    return published_item


def publish_raster_tile_layer(
    gis: Any,
    file_path: str | Path,
    title: str,
    folder: str | None = None,
    overwrite: bool = True,
) -> Any:
    """단일 GeoTIFF를 업로드하고 Tile Layer(호스팅 이미지 레이어)로 발행한다.

    조직 라이선스에 따라(래스터 분석 기능 활성화 여부) 실패할 수 있다 -
    실패 시 예외를 그대로 올려서 원인(라이선스/용량 등)을 사용자가 바로
    확인하게 한다 (src/data/download.py의 download_imagery와 동일한 원칙:
    자동화 실패를 숨기지 않는다).

    Args:
        gis: connect_gis()로 얻은 GIS 인스턴스.
        file_path: GeoTIFF 경로 (예: data/processed/imagery/2022_stack.tif).
        title: AGOL에 표시할 아이템 제목.
        folder: 업로드할 Content 폴더명.
        overwrite: True면 동일 제목의 기존 아이템을 삭제 후 재발행.

    Returns:
        발행된 Tile/Image Layer의 arcgis.gis.Item.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"[PUBLISH] {file_path} 가 없습니다.")

    if overwrite:
        for item_type in ("Image", "GeoTIFF", "Image Service"):
            existing = _find_existing_item(gis, title, item_type)
            if existing is not None:
                logger.info("[PUBLISH] 기존 아이템 삭제: %s (%s)", title, item_type)
                existing.delete(permanent=True)

    item_properties = {"title": title, "type": "GeoTIFF", "tags": "skywatch_poc,changneung"}
    folder_obj = _get_folder(gis, folder)
    job = folder_obj.add(item_properties, file=str(file_path))
    source_item = job.result()
    logger.info("[PUBLISH] 업로드 완료: %s (item_id=%s)", title, source_item.id)

    published_item = source_item.publish(output_type="Tiles")
    logger.info("[PUBLISH] 발행 완료: %s -> %s", title, published_item.homepage)
    return published_item


def apply_unique_value_renderer(
    layer_item: Any,
    field: str,
    value_colors: dict[str, list[int]],
    layer_index: int = 0,
) -> None:
    """Hosted Feature Layer에 field 기준 카테고리(unique value) 심볼을 적용한다.

    Args:
        layer_item: publish_geojson_layer()가 반환한 Item.
        field: 심볼 기준 필드명 (예: "change_type", "inspection_priority").
        value_colors: {필드값: [R,G,B,A]} 매핑.
        layer_index: FeatureLayerCollection 내 레이어 인덱스 (기본 0).
    """
    layer = layer_item.layers[layer_index]
    unique_value_infos = [
        {
            "value": value,
            "label": value,
            "symbol": {
                "type": "esriSFS",
                "style": "esriSFSSolid",
                "color": color,
                "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [50, 50, 50, 255], "width": 0.5},
            },
        }
        for value, color in value_colors.items()
    ]
    drawing_info = {
        "renderer": {
            "type": "uniqueValue",
            "field1": field,
            "uniqueValueInfos": unique_value_infos,
            "defaultSymbol": {
                "type": "esriSFS", "style": "esriSFSSolid",
                "color": [200, 200, 200, 180],
                "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [50, 50, 50, 255], "width": 0.5},
            },
        }
    }
    layer.manager.update_definition({"drawingInfo": drawing_info})
    logger.info("[PUBLISH] 심볼로지 적용: %s (field=%s)", layer_item.title, field)


def _operational_layer(
    item: Any,
    title: str,
    layer_index: int = 0,
    renderer_field: str | None = None,
    value_colors: dict[str, list[int]] | None = None,
    popup_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Web Map JSON의 operationalLayers 항목 하나를 만든다."""
    layer_entry: dict[str, Any] = {
        "id": f"{item.id}_{layer_index}",
        "title": title,
        "itemId": item.id,
        "layerType": "ArcGISFeatureLayer",
        "url": f"{item.url.rstrip('/')}/{layer_index}" if hasattr(item, "url") and item.url else None,
        "visibility": True,
    }
    if renderer_field and value_colors:
        unique_value_infos = [
            {
                "value": value,
                "label": value,
                "symbol": {
                    "type": "esriSFS", "style": "esriSFSSolid", "color": color,
                    "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [50, 50, 50, 255], "width": 0.5},
                },
            }
            for value, color in value_colors.items()
        ]
        layer_entry["layerDefinition"] = {
            "drawingInfo": {
                "renderer": {
                    "type": "uniqueValue",
                    "field1": renderer_field,
                    "uniqueValueInfos": unique_value_infos,
                }
            }
        }
    if popup_fields:
        layer_entry["popupInfo"] = {
            "title": title,
            "fieldInfos": [{"fieldName": f, "visible": True, "label": f} for f in popup_fields],
        }
    return {k: v for k, v in layer_entry.items() if v is not None}


def build_web_map(
    gis: Any,
    title: str,
    imagery_2022_item: Any | None,
    imagery_2024_item: Any | None,
    aoi_item: Any,
    change_polygons_item: Any,
    building_change_item: Any,
) -> Any:
    """handoff.md 9-5 순서(2022영상 -> 2024영상 -> AOI -> 변화폴리곤 -> 건물결과 x2)로 Web Map을 만든다.

    building_change_item은 change_type / inspection_priority 두 벌의 심볼로
    두 번 참조한다 (동일 Hosted Feature Layer, 클라이언트 렌더러만 다르게).

    Returns:
        발행된 Web Map의 arcgis.gis.Item.
    """
    operational_layers: list[dict[str, Any]] = []
    if imagery_2022_item is not None:
        operational_layers.append(_operational_layer(imagery_2022_item, "T1 2022-05-17 Sentinel-2"))
    if imagery_2024_item is not None:
        operational_layers.append(_operational_layer(imagery_2024_item, "T2 2024-05-31 Sentinel-2"))
    operational_layers.append(_operational_layer(aoi_item, "창릉동 AOI"))
    operational_layers.append(_operational_layer(change_polygons_item, "변화 탐지 폴리곤"))
    operational_layers.append(
        _operational_layer(
            building_change_item, "건물 변화 결과 - 변화유형별",
            renderer_field="change_type", value_colors=CHANGE_TYPE_COLORS,
            popup_fields=POPUP_FIELDS,
        )
    )
    operational_layers.append(
        _operational_layer(
            building_change_item, "건물 변화 결과 - 현장조사 우선순위",
            renderer_field="inspection_priority", value_colors=PRIORITY_COLORS,
            popup_fields=POPUP_FIELDS,
        )
    )

    webmap_dict = {
        "operationalLayers": operational_layers,
        "baseMap": {
            "baseMapLayers": [
                {
                    "id": "world-imagery",
                    "layerType": "ArcGISTiledMapServiceLayer",
                    "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer",
                    "title": "World Imagery",
                }
            ],
            "title": "World Imagery",
        },
        "spatialReference": {"wkid": 4326},
        "version": "2.28",
    }

    existing = _find_existing_item(gis, title, "Web Map")
    if existing is not None:
        logger.info("[PUBLISH] 기존 Web Map 삭제: %s", title)
        existing.delete(permanent=True)

    folder_obj = _get_folder(gis, None)
    job = folder_obj.add(
        {"title": title, "type": "Web Map", "tags": "skywatch_poc,changneung"},
        text=json.dumps(webmap_dict, ensure_ascii=False),
    )
    webmap_item = job.result()
    logger.info("[PUBLISH] Web Map 발행 완료: %s -> %s", title, webmap_item.homepage)
    return webmap_item


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vectors-dir", default="outputs/vectors")
    parser.add_argument("--imagery-dir", default="data/processed/imagery")
    parser.add_argument("--rasters-dir", default="outputs/rasters")
    parser.add_argument("--folder", default="skywatch_poc_changneung", help="AGOL Content 폴더명")
    parser.add_argument("--include-buildings", action="store_true", help="건물 footprint 전체(2,737개) 레이어도 발행")
    parser.add_argument("--include-rasters", action="store_true", help="T1/T2 스택 및 변화확률 래스터도 Tile Layer로 발행")
    parser.add_argument("--build-webmap", action="store_true", help="발행 후 Web Map까지 자동 구성")
    parser.add_argument("--share", choices=["private", "org", "everyone"], default="private")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    gis = connect_gis()
    vectors_dir = Path(args.vectors_dir)

    aoi_item = publish_geojson_layer(gis, vectors_dir / "changneung_aoi.geojson", "창릉동 AOI", args.folder)
    change_polygons_item = publish_geojson_layer(
        gis, vectors_dir / "change_polygons.geojson", "변화 탐지 폴리곤", args.folder
    )
    building_change_item = publish_geojson_layer(
        gis, vectors_dir / "building_change_results.geojson", "건물 변화 결과", args.folder
    )
    apply_unique_value_renderer(building_change_item, "change_type", CHANGE_TYPE_COLORS)

    buildings_item = None
    if args.include_buildings:
        buildings_item = publish_geojson_layer(
            gis, vectors_dir / "changneung_buildings.geojson", "건물 Footprint 전체 (2,737개)", args.folder
        )

    imagery_2022_item = imagery_2024_item = change_probability_item = None
    if args.include_rasters:
        imagery_dir = Path(args.imagery_dir)
        rasters_dir = Path(args.rasters_dir)
        imagery_2022_item = publish_raster_tile_layer(gis, imagery_dir / "2022_stack.tif", "T1 2022 영상 스택", args.folder)
        imagery_2024_item = publish_raster_tile_layer(gis, imagery_dir / "2024_stack.tif", "T2 2024 영상 스택", args.folder)
        change_probability_item = publish_raster_tile_layer(
            gis, rasters_dir / "change_probability.tif", "변화확률 래스터", args.folder
        )

    published_items = [aoi_item, change_polygons_item, building_change_item]
    if buildings_item is not None:
        published_items.append(buildings_item)
    for raster_item in (imagery_2022_item, imagery_2024_item, change_probability_item):
        if raster_item is not None:
            published_items.append(raster_item)

    webmap_item = None
    if args.build_webmap:
        webmap_item = build_web_map(
            gis, "고양 창릉동 건물 변화 탐지 (2022-2024)",
            imagery_2022_item, imagery_2024_item, aoi_item, change_polygons_item, building_change_item,
        )
        published_items.append(webmap_item)

    if args.share != "private":
        org_flag = args.share == "org"
        everyone_flag = args.share == "everyone"
        for item in published_items:
            item.share(org=org_flag, everyone=everyone_flag)
        logger.info("[PUBLISH] 공유 설정 적용: %s", args.share)

    logger.info("[PUBLISH] 전체 완료. 발행된 아이템:")
    for item in published_items:
        logger.info("  - %s: %s", item.title, item.homepage)


if __name__ == "__main__":
    main()
