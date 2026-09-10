"""사업지 설정 하나로 공식 AOI와 공개 업무 데이터를 수집한다."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import planetary_computer
import rasterio
import requests
import yaml
from dotenv import load_dotenv
from pystac_client import Client
from rasterio.mask import mask
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom

from .build_buildings import build_buildings
from .build_cadastre import build_cadastre
from .download import (
    VWORLD_BUILDING_LAYER,
    VWORLD_CADASTRE_LAYERS,
    download_vworld_wfs_layer,
    fetch_building_title_info,
)

STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"
VWORLD_WFS = "https://api.vworld.kr/req/wfs"


def load_project(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def acquire_official_aoi(cfg: dict[str, Any]) -> gpd.GeoDataFrame:
    """VWorld LH 사업지구경계도에서 zonecode가 정확히 일치하는 지구계만 저장한다."""
    load_dotenv()
    key = os.getenv("VWORLD_API_KEY")
    if not key:
        raise RuntimeError("VWORLD_API_KEY가 필요합니다")
    aoi_cfg = cfg["aoi"]
    params = {
        "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
        "TYPENAME": aoi_cfg["layer"], "SRSNAME": "EPSG:4326",
        "OUTPUT": "application/json", "MAXFEATURES": 1000, "STARTINDEX": 0,
        "KEY": key,
    }
    response = requests.get(VWORLD_WFS, params=params, timeout=90)
    response.raise_for_status()
    features = response.json().get("features", [])
    matches = [f for f in features if f.get("properties", {}).get("zonecode") == aoi_cfg["zonecode"]]
    if len(matches) != 1:
        raise RuntimeError(f"공식 지구계가 1건이어야 합니다: matches={len(matches)}")
    if matches[0]["properties"].get("zonename") != aoi_cfg["zonename"]:
        raise RuntimeError("zonecode와 zonename이 일치하지 않습니다")

    gdf = gpd.GeoDataFrame.from_features(matches, crs="EPSG:4326").to_crs(cfg["project"]["analysis_crs"])
    gdf["source"] = aoi_cfg["source_url"]
    gdf["notice_no"] = aoi_cfg["designation_notice_number"]
    gdf["notice_date"] = aoi_cfg["designation_notice_date"]
    gdf["checked_at"] = cfg["sources_checked_at"]
    gdf["area_m2_calc"] = gdf.geometry.area
    out = Path(cfg["paths"]["aoi"])
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out, driver="GPKG", layer="official_aoi")
    return gdf


def _wgs84_bounds(aoi: gpd.GeoDataFrame, pad_deg: float = 0.002) -> tuple[float, float, float, float]:
    b = aoi.to_crs(4326).total_bounds
    return (float(b[0] - pad_deg), float(b[1] - pad_deg), float(b[2] + pad_deg), float(b[3] + pad_deg))


def acquire_reference_vectors(cfg: dict[str, Any], aoi: gpd.GeoDataFrame) -> dict[str, Any]:
    root = Path(cfg["paths"]["data_root"])
    bbox = _wgs84_bounds(aoi)
    raw_buildings = root / "raw" / "buildings" / "bbox.geojson"
    download_vworld_wfs_layer(VWORLD_BUILDING_LAYER, bbox, raw_buildings)
    buildings = build_buildings(raw_buildings, cfg["paths"]["aoi"], cfg["paths"]["buildings"])

    raw_cadastre = []
    for layer in VWORLD_CADASTRE_LAYERS:
        path = root / "raw" / "cadastre" / f"{layer}.geojson"
        download_vworld_wfs_layer(layer, bbox, path)
        raw_cadastre.append(path)
    cadastre = build_cadastre(raw_cadastre[0], raw_cadastre[1], cfg["paths"]["aoi"], cfg["paths"]["cadastre"])
    return {"buildings": len(buildings), "cadastre": len(cadastre), "bbox": bbox}


def acquire_building_register(cfg: dict[str, Any]) -> dict[str, Any]:
    buildings = gpd.read_file(cfg["paths"]["buildings"])
    pnu = buildings.get("pnu")
    codes = sorted({str(v)[:10] for v in pnu.dropna() if len(str(v)) >= 10}) if pnu is not None else []
    all_items: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for code in codes:
        for attempt in range(3):
            try:
                all_items.extend(fetch_building_title_info(code[:5], code[5:10]))
                break
            except Exception as exc:  # 일시적 비JSON 응답은 제한적으로 재시도
                errors[code] = str(exc)
                if attempt < 2:
                    time.sleep(1 + attempt)
        else:
            continue
        errors.pop(code, None)
    out = Path(cfg["paths"]["building_register"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as stream:
        json.dump(all_items, stream, ensure_ascii=False, indent=2)
    return {"legal_dong_codes": codes, "records": len(all_items), "errors": errors}


def acquire_building_permits(cfg: dict[str, Any], codes: list[str]) -> dict[str, Any]:
    """건축인허가 기본개요를 법정동별로 수집한다(법적 판정 정답으로 사용하지 않음)."""
    key = os.getenv("DATA_GO_KR_API_KEY")
    if not key:
        raise RuntimeError("DATA_GO_KR_API_KEY가 필요합니다")
    url = "https://apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo"
    all_items: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for code in codes:
        page = 1
        fetched_for_code = 0
        while True:
            params = {
                "ServiceKey": key, "sigunguCd": code[:5], "bjdongCd": code[5:10],
                "numOfRows": 500, "pageNo": page, "_type": "json",
            }
            for attempt in range(3):
                try:
                    response = requests.get(url, params=params, timeout=45)
                    response.raise_for_status()
                    body = response.json()["response"]["body"]
                    break
                except Exception as exc:
                    errors[code] = str(exc)
                    if attempt < 2:
                        time.sleep(1 + attempt)
            else:
                break
            items = body.get("items") or {}
            batch = items.get("item", []) if isinstance(items, dict) else []
            if isinstance(batch, dict):
                batch = [batch]
            for item in batch:
                item["pnu"] = (
                    f"{item.get('sigunguCd', '')}{item.get('bjdongCd', '')}"
                    f"{item.get('platGbCd', '0')}{str(item.get('bun', '0')).zfill(4)}"
                    f"{str(item.get('ji', '0')).zfill(4)}"
                )
            all_items.extend(batch)
            fetched_for_code += len(batch)
            # The public API may cap a response below ``numOfRows``.  Using
            # page * requested_rows therefore stopped after the first page.
            if fetched_for_code >= int(body.get("totalCount", 0)) or not batch:
                errors.pop(code, None)
                break
            page += 1
    out = Path(cfg["paths"]["building_permits"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as stream:
        json.dump(all_items, stream, ensure_ascii=False, indent=2)
    return {"records": len(all_items), "errors": errors}


def _find_item(item_id: str, stac_api: str, collection: str) -> dict[str, Any]:
    catalog = Client.open(stac_api)
    items = list(catalog.search(collections=[collection], ids=[item_id]).items())
    if len(items) != 1:
        raise RuntimeError(f"Sentinel-2 item을 찾지 못했습니다: {item_id}")
    return items[0].to_dict()


def acquire_imagery(cfg: dict[str, Any], aoi: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    """원격 COG에서 공식 AOI 부분만 읽어 실제 GeoTIFF와 SCL 품질지표를 저장한다."""
    root = Path(cfg["paths"]["data_root"]) / "raw" / "imagery"
    geom_wgs84 = aoi.to_crs(4326).geometry.union_all().__geo_interface__
    records = []
    for epoch in cfg["imagery"]["epochs"]:
        item = _find_item(
            epoch["item_id"], cfg["imagery"].get("stac_api", STAC_API),
            cfg["imagery"]["collection"],
        )
        signed = planetary_computer.sign(item) if "planetarycomputer" in cfg["imagery"].get("stac_api", "") else item
        epoch_dir = root / epoch["id"]
        epoch_dir.mkdir(parents=True, exist_ok=True)
        metrics = None
        aliases = cfg["imagery"].get("asset_aliases", {})
        for band in [*cfg["imagery"]["bands"], "SCL"]:
            out = epoch_dir / f"{epoch['item_id']}_{band}.tif"
            asset_key = aliases.get(band, band)
            asset = signed["assets"][asset_key]
            href = asset["href"]
            with rasterio.open(href) as src:
                geom = transform_geom("EPSG:4326", src.crs, geom_wgs84)
                arr, transform = mask(src, [geom], crop=True, filled=True, nodata=src.nodata or 0)
                profile = src.profile.copy()
                profile.update(height=arr.shape[1], width=arr.shape[2], transform=transform, count=1)
                with rasterio.open(out, "w", **profile) as dst:
                    dst.write(arr)
            if band == "SCL":
                values = arr[0]
                footprint = geometry_mask(
                    [geom], out_shape=values.shape, transform=transform, invert=True
                )
                valid = (values != 0) & footprint
                denom = max(int(valid.sum()), 1)
                cloud = np.isin(values, [3, 8, 9, 10, 11]) & valid
                metrics = {
                    "aoi_valid_pixel_pct": round(
                        100 * float(valid.sum()) / max(int(footprint.sum()), 1), 4
                    ),
                    "aoi_cloud_pixel_pct": round(100 * float(cloud.sum()) / denom, 4),
                }
        records.append({
            **epoch,
            "scene_cloud_pct": item["properties"].get("eo:cloud_cover"),
            **(metrics or {}),
            "source_checksums": {
                band: item["assets"][aliases.get(band, band)].get("file:checksum")
                for band in cfg["imagery"]["bands"]
            },
            "assets": {band: str(epoch_dir / f"{epoch['item_id']}_{band}.tif") for band in cfg["imagery"]["bands"]},
        })
    return records


def acquire_all(project_config: str | Path) -> dict[str, Any]:
    cfg = load_project(project_config)
    aoi = acquire_official_aoi(cfg)
    result = {
        "aoi": {
            "feature_count": len(aoi),
            "area_m2": round(float(aoi.geometry.area.sum()), 1),
            "bounds_wgs84": [round(float(v), 7) for v in aoi.to_crs(4326).total_bounds],
        }
    }
    result["vectors"] = acquire_reference_vectors(cfg, aoi)
    result["building_register"] = acquire_building_register(cfg)
    result["building_permits"] = acquire_building_permits(
        cfg, result["building_register"]["legal_dong_codes"]
    )
    result["imagery"] = acquire_imagery(cfg, aoi)
    out = Path(cfg["paths"]["output_root"]) / "acquisition_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result
