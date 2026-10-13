"""채석장 관련 후보 분류(참조 폴리곤 + 복합 변화 결과).

- 참조 채석장 폴리곤(OSM landuse=quarry)은 비공인 참고자료다. 폴리곤 내부 전체가 노출지라는 뜻이 아니다.
- 노출지 면적을 채석장 면적으로 표시하지 않는다. 채석장 면적 변화 이력은 신뢰할 수 있는 시계열 경계가 없어 산출하지 않는다.
- 자동으로 채석장 확장을 '확정'하지 않는다: POTENTIAL_QUARRY_EXPANSION은 공간적 연결성에 근거한 의심 후보다.

quarry_class 코드:
  0 해당없음 | 1 VERIFIED_QUARRY_AREA(참조 폴리곤 내부, 변화 이벤트 없음) | 2 POTENTIAL_QUARRY_EXPANSION
  3 EXPOSED_SURFACE_CHANGE | 4 POSSIBLE_REVEGETATION | 5 UNCLASSIFIED | 6 QUARRY_INTERNAL_CHANGE(참조 폴리곤 내부 노출지 증가)
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import shapely
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from scipy import ndimage

QUARRY_CODES = {"VERIFIED_QUARRY_AREA": 1, "POTENTIAL_QUARRY_EXPANSION": 2, "EXPOSED_SURFACE_CHANGE": 3,
                "POSSIBLE_REVEGETATION": 4, "UNCLASSIFIED": 5, "QUARRY_INTERNAL_CHANGE": 6}
QUARRY_NAMES = {v: k for k, v in QUARRY_CODES.items()}
QUARRY_KO = {"VERIFIED_QUARRY_AREA": "기존 채석장 영역(참조자료)", "POTENTIAL_QUARRY_EXPANSION": "채석장 확장 의심 후보",
             "EXPOSED_SURFACE_CHANGE": "노출지 변화(채석장 연계 미확인)", "POSSIBLE_REVEGETATION": "식생 회복 가능 후보",
             "UNCLASSIFIED": "원인 판단 어려움", "QUARRY_INTERNAL_CHANGE": "기존 채석장 내부 노출지 증가"}


def load_quarry_reference(ref_dir, tags: dict, crs: str = "EPSG:5186") -> tuple[gpd.GeoDataFrame, dict]:
    """참조 폴리곤 로드. tags={'landuse':[...]} 에 해당하는 폴리곤만 '채석장 참조'로 사용한다."""
    from pathlib import Path

    p = Path(ref_dir) / "osm_quarry_candidates.geojson"
    meta = {"path": str(p), "reference_level": "OSM_UNOFFICIAL", "used_tags": tags}
    if not p.exists():
        return gpd.GeoDataFrame(geometry=[], crs=crs), {**meta, "status": "NO_REFERENCE"}
    g = gpd.read_file(p)
    if g.empty:
        return gpd.GeoDataFrame(geometry=[], crs=crs), {**meta, "status": "NO_REFERENCE"}
    keep = np.zeros(len(g), dtype=bool)
    for k, vals in tags.items():
        if k in g.columns:
            keep |= g[k].isin(vals).to_numpy()
    g = g[keep & g.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).to_numpy()].to_crs(crs)
    return g.reset_index(drop=True), {**meta, "status": "OK" if len(g) else "NO_REFERENCE", "n_polygons": int(len(g))}


def classify_quarry(comp: dict, quarry: gpd.GeoDataFrame, transform: Affine, shape: tuple[int, int],
                    adjacency_m: float, min_component_pixels: int) -> dict:
    h, w = shape
    res = abs(transform.a)
    if len(quarry):
        qmask = ~geometry_mask(list(quarry.geometry), out_shape=shape, transform=transform, all_touched=False)
        dist = ndimage.distance_transform_edt(~qmask) * res if qmask.any() else np.full(shape, np.inf)
    else:
        qmask = np.zeros(shape, dtype=bool)
        dist = np.full(shape, np.inf, dtype="float64")
    expo = comp["expo_any"] & comp["evaluable"]
    lab, n = ndimage.label(expo, structure=np.ones((3, 3), dtype=int))
    connected = np.zeros(shape, dtype=bool)
    if n:
        sizes = ndimage.sum(expo, lab, index=np.arange(1, n + 1))
        mind = ndimage.minimum(dist, lab, index=np.arange(1, n + 1))
        ok = (sizes >= min_component_pixels) & (mind <= adjacency_m) & (len(quarry) > 0)
        connected = np.isin(lab, np.where(ok)[0] + 1)
    cls = np.zeros(shape, dtype="uint8")
    cls[qmask] = QUARRY_CODES["VERIFIED_QUARRY_AREA"]
    other_change = (comp["loss_any"] | comp["unc_any"]) & ~expo
    cls[other_change] = QUARRY_CODES["UNCLASSIFIED"]
    cls[expo & ~connected & ~qmask] = QUARRY_CODES["EXPOSED_SURFACE_CHANGE"]
    cls[expo & ~connected & qmask] = QUARRY_CODES["QUARRY_INTERNAL_CHANGE"]
    cls[expo & connected & qmask] = QUARRY_CODES["QUARRY_INTERNAL_CHANGE"]
    cls[expo & connected & ~qmask] = QUARRY_CODES["POTENTIAL_QUARRY_EXPANSION"]
    cls[comp["rec_from_exposed"] & ~expo] = QUARRY_CODES["POSSIBLE_REVEGETATION"]
    cls[~comp["evaluable"] & ~qmask] = 0
    return {"quarry_class": cls, "quarry_mask": qmask, "dist_to_quarry_m": dist.astype("float32"),
            "n_components": int(n), "n_connected_pixels": int(connected.sum())}
