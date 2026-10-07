"""한국철도공단 단양 매포읍 매포리·하괴리 Master AOI 확보 - VWorld 행정경계(리) 기반.

AOI 좌표를 임의로 만들지 않는다(#10 원칙). VWorld Data API의 LT_C_ADRI_INFO
(법정리 경계)에서 "충청북도 단양군 매포읍"의 매포리·하괴리 polygon을 내려받아
dissolve한 결과를 AOI로 쓴다. 이 경계는 행정구역 경계일 뿐, 사업 공식 보상/
사업구역 경계가 아니므로 config에는 status=provisional로 기록한다.

사전 준비: .env에 VWORLD_API_KEY (.env.example 참고).

실행:
    python -m src.data.build_kr_rail_danyang_aoi
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import geopandas as gpd
import requests
from dotenv import load_dotenv
from shapely.geometry import LineString, shape

logger = logging.getLogger("build_kr_rail_danyang_aoi")

load_dotenv()

VWORLD_DATA_URL = "https://api.vworld.kr/req/data"
RI_LAYER = "LT_C_ADRI_INFO"
# 단양군 매포읍 일대 탐색 bbox(EPSG:4326) - AOI가 아니라 법정리 조회 범위일 뿐.
SEARCH_BBOX_WGS84 = (128.25, 36.90, 128.40, 37.05)
TARGET_EUP = "매포읍"
TARGET_RI = ("매포리", "하괴리")


def fetch_ri_boundaries(api_key: str, bbox: tuple[float, float, float, float] = SEARCH_BBOX_WGS84) -> gpd.GeoDataFrame:
    """bbox 안의 법정리 경계 전체를 받는다(페이지네이션)."""
    features: list[dict] = []
    page = 1
    while True:
        params = {
            "service": "data", "request": "GetFeature", "data": RI_LAYER, "key": api_key,
            "format": "json", "size": 100, "page": page, "crs": "EPSG:4326", "geometry": "true",
            "geomFilter": "BOX({},{},{},{})".format(*bbox),
        }
        resp = requests.get(VWORLD_DATA_URL, params=params, timeout=60)
        resp.raise_for_status()
        body = json.loads(resp.content.decode("utf-8"))["response"]
        if body["status"] != "OK":
            raise RuntimeError(f"[DATA] VWorld {RI_LAYER} 조회 실패: {body.get('error')}")
        features.extend(body["result"]["featureCollection"]["features"])
        if page >= int(body["page"]["total"]):
            break
        page += 1
    return gpd.GeoDataFrame(
        [f["properties"] for f in features],
        geometry=[shape(f["geometry"]) for f in features], crs="EPSG:4326",
    )


def select_target_ri(all_ri: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """full_nm이 '매포읍'을 포함하고 li_kor_nm이 대상 리인 행만 고른다. 없으면 에러(임의 대체 금지)."""
    sel = all_ri[all_ri["full_nm"].str.contains(TARGET_EUP) & all_ri["li_kor_nm"].isin(TARGET_RI)]
    missing = set(TARGET_RI) - set(sel["li_kor_nm"])
    if missing:
        raise RuntimeError(f"[DATA] 대상 리를 찾지 못했습니다: {sorted(missing)} (조회된 리: {sorted(all_ri['li_kor_nm'])})")
    return sel


OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def fetch_osm_railway(aoi_wgs84: gpd.GeoDataFrame, out_path: str | Path, margin_deg: float = 0.01) -> gpd.GeoDataFrame:
    """AOI 주변 OpenStreetMap 철도(rail/abandoned 등) 선형을 받는다 - 공식 노선 geometry가 아니다.

    railway/service/tunnel/start_date/name/operator 속성을 보존하므로 거리 계산 시 어떤 선을
    쓸지(예: railway=rail 만) 호출 측이 고른다. Overpass는 User-Agent가 없으면 406을 준다.
    """
    minx, miny, maxx, maxy = aoi_wgs84.total_bounds
    bbox = f"{miny - margin_deg},{minx - margin_deg},{maxy + margin_deg},{maxx + margin_deg}"
    query = f'[out:json][timeout:60];way["railway"~"^(rail|abandoned|disused|construction)$"]({bbox});out geom tags;'
    resp = requests.post(
        OVERPASS_URL, data={"data": query}, timeout=120,
        headers={"User-Agent": "skywatch_poc/0.1 (research PoC)"},
    )
    resp.raise_for_status()
    rows = []
    for el in resp.json().get("elements", []):
        if len(el.get("geometry", [])) < 2:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_id": el["id"], **{k: tags.get(k) for k in ("railway", "service", "tunnel", "start_date", "name", "operator")},
            "geometry": LineString([(p["lon"], p["lat"]) for p in el["geometry"]]),
        })
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326") if rows else gpd.GeoDataFrame(
        columns=["osm_id", "railway", "service", "tunnel", "start_date", "name", "operator", "geometry"],
        geometry="geometry", crs="EPSG:4326",
    )
    gdf.to_file(out_path, driver="GeoJSON")
    logger.info("[DATA] OSM 철도 %d건 저장: %s", len(gdf), out_path)
    return gdf


def build_aoi(out_dir: str | Path, analysis_crs: str = "EPSG:5186") -> gpd.GeoDataFrame:
    key = os.getenv("VWORLD_API_KEY")
    if not key:
        raise RuntimeError("[DATA] VWORLD_API_KEY가 .env에 없습니다.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_ri = fetch_ri_boundaries(key)
    all_ri.to_file(out_dir / "danyang_ri_all.geojson", driver="GeoJSON")
    sel = select_target_ri(all_ri)
    sel.to_file(out_dir / "maepo_ri_members.geojson", driver="GeoJSON")

    sel_m = sel.to_crs(analysis_crs)
    aoi = gpd.GeoDataFrame(
        {"name": ["매포리+하괴리"], "area_km2": [round(sel_m.geometry.union_all().area / 1e6, 3)]},
        geometry=[sel_m.geometry.union_all()], crs=analysis_crs,
    )
    aoi.to_file(out_dir / "danyang_master_aoi.geojson", driver="GeoJSON")
    try:
        fetch_osm_railway(aoi.to_crs("EPSG:4326"), out_dir / "osm_railway.geojson")
    except Exception as exc:  # 철도는 보조 context - 실패해도 AOI 생성은 유지(임의 선형을 만들지 않는다)
        logger.warning("[DATA] OSM 철도 조회 실패(철도 필드/그림 생략됨): %s", exc)
    logger.info("[DATA] Master AOI 저장: %.3f km^2 (%s)", aoi["area_km2"].iloc[0], out_dir)
    return aoi


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    build_aoi("data/projects/kr_rail_danyang/aoi")
