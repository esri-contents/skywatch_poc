"""채석장/토지이용 참조자료 확보(OSM Overpass). 결과는 참고자료이며 공인 경계가 아니다.

수집한 요소의 출처/기준시각/라이선스(ODbL)/좌표계를 provenance JSON에 기록한다. 아무 것도 없으면 빈 결과를
기록하고 채석장을 가정하지 않는다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import LineString, Polygon

logger = logging.getLogger(__name__)
OVERPASS = "https://overpass-api.de/api/interpreter"

QUARRY_FILTERS = ['["landuse"="quarry"]', '["industrial"~"quarry|mine"]', '["man_made"="mineshaft"]',
                  '["resource"~"limestone|stone|gravel|sand"]', '["natural"="bare_rock"]', '["landuse"="brownfield"]']
CONTEXT_FILTERS = ['["landuse"~"farmland|orchard|farmyard|meadow|vineyard|plant_nursery"]',
                   '["landuse"~"industrial|construction|landfill|retail|commercial"]']


def _to_gdf(elements: list[dict]) -> gpd.GeoDataFrame:
    rows, geoms = [], []
    for el in elements:
        tags = el.get("tags", {})
        g = None
        if el["type"] == "way" and el.get("geometry"):
            pts = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(pts) >= 4 and pts[0] == pts[-1]:
                g = Polygon(pts)
            elif len(pts) >= 2:
                g = LineString(pts)
        elif el["type"] == "relation":
            outers = []
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    pts = [(p["lon"], p["lat"]) for p in m["geometry"]]
                    if len(pts) >= 4 and pts[0] == pts[-1]:
                        outers.append(Polygon(pts))
            if outers:
                from shapely.ops import unary_union
                g = unary_union(outers)
        if g is None or g.is_empty:
            continue
        rows.append({"osm_type": el["type"], "osm_id": el["id"], **{k: tags.get(k) for k in (
            "landuse", "industrial", "natural", "resource", "name", "operator", "man_made", "disused", "start_date")}})
        geoms.append(g)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=4326)


def fetch_osm(bbox: tuple[float, float, float, float], filters: list[str], timeout: int = 120) -> gpd.GeoDataFrame:
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    parts = "".join(f"nwr{f}({s},{w},{n},{e});" for f in filters)
    q = f"[out:json][timeout:90];({parts});out geom;"
    r = requests.post(OVERPASS, data={"data": q}, timeout=timeout, headers={"User-Agent": "skywatch-poc-ndvi/1.0"})
    r.raise_for_status()
    return _to_gdf(r.json().get("elements", []))


def ensure_reference(aoi: gpd.GeoDataFrame, ref_dir: str | Path, offline: bool = False, refresh: bool = False) -> dict:
    ref_dir = Path(ref_dir)
    ref_dir.mkdir(parents=True, exist_ok=True)
    prov_p = ref_dir / "reference_provenance.json"
    qp, cp = ref_dir / "osm_quarry_candidates.geojson", ref_dir / "osm_landuse_context.geojson"
    if prov_p.exists() and qp.exists() and not refresh:
        return json.loads(prov_p.read_text(encoding="utf-8"))
    prov: dict = {"source": "OpenStreetMap via Overpass API", "license": "ODbL 1.0 (c) OpenStreetMap contributors",
                  "crs_native": "EPSG:4326", "accuracy": "자원봉사 기반 참고자료. 공인 경계/최신성/정확도 보증 없음",
                  "status": "UNAVAILABLE"}
    if offline:
        prov["reason"] = "--offline 이고 로컬 참조자료 없음"
        return prov
    try:
        b = aoi.to_crs(4326).total_bounds
        pad = 0.01
        bbox = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)  # 인접 채석장 연속성 판단을 위해 AOI 밖도 포함
        quarry = fetch_osm(bbox, QUARRY_FILTERS)
        context = fetch_osm(bbox, CONTEXT_FILTERS)
        quarry.to_file(qp, driver="GeoJSON") if len(quarry) else qp.write_text('{"type":"FeatureCollection","features":[]}')
        context.to_file(cp, driver="GeoJSON") if len(context) else cp.write_text('{"type":"FeatureCollection","features":[]}')
        prov.update(status="OK", retrieved_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                    bbox_wgs84=list(bbox), n_quarry_candidates=int(len(quarry)), n_context=int(len(context)),
                    quarry_tag_counts={"landuse": quarry["landuse"].value_counts().to_dict() if len(quarry) else {},
                                       "natural": quarry["natural"].value_counts().to_dict() if len(quarry) else {}},
                    queries={"quarry": QUARRY_FILTERS, "context": CONTEXT_FILTERS})
    except Exception as e:  # noqa: BLE001
        prov["reason"] = f"{type(e).__name__}: {e}"
        logger.warning("[REF] 참조자료 확보 실패: %s", prov["reason"])
    prov_p.write_text(json.dumps(prov, ensure_ascii=False, indent=2), encoding="utf-8")
    return prov
