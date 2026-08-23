"""Change Polygon과 건물 footprint 공간 Overlay."""

from __future__ import annotations

import logging

import geopandas as gpd

logger = logging.getLogger("overlay")


def overlay_buildings_with_changes(
    buildings: gpd.GeoDataFrame,
    change_polygons: gpd.GeoDataFrame,
    buffer_m: float = 3.0,
) -> gpd.GeoDataFrame:
    """건물별로 change polygon과의 중첩을 계산한다.

    Args:
        buildings: 건물 footprint GeoDataFrame (build_buildings.py 결과).
        change_polygons: postprocess.polygonize_change() 결과.
        buffer_m: "주변 변화"를 판단하기 위한 buffer 거리(m).

    Returns:
        buildings에 다음 컬럼을 추가한 GeoDataFrame:
        building_area_m2, change_area_m2(교차분 합), change_ratio,
        max_change_score(교차 change polygon 중 최댓값),
        near_change(버퍼 내 변화 존재 여부, 건물 본체는 미교차),
        site_id(교차하는 change polygon의 change_id - 큰 change polygon
        하나에 건물 여러 개가 걸치는 경우가 실측으로 흔히 확인되어, 이 값으로
        "서로 다른 건물 수"와 "서로 다른 실제 현장 수"를 구분할 수 있게 한다.
        여러 change polygon과 겹치면 그중 면적이 가장 큰 것을 대표로 사용).
    """
    if buildings.crs != change_polygons.crs:
        change_polygons = change_polygons.to_crs(buildings.crs)

    out = buildings.copy()
    out["building_area_m2"] = out.geometry.area.round(2)

    if change_polygons.empty:
        out["change_area_m2"] = 0.0
        out["change_ratio"] = 0.0
        out["max_change_score"] = None
        out["near_change"] = False
        out["site_id"] = None
        return out

    change_union = change_polygons.geometry.union_all()
    buffered = out.geometry.buffer(buffer_m)

    change_areas = []
    max_scores = []
    near_flags = []
    site_ids = []
    for geom, buf_geom in zip(out.geometry, buffered):
        intersection = geom.intersection(change_union)
        change_areas.append(intersection.area)

        overlapping = change_polygons[change_polygons.geometry.intersects(geom)]
        max_scores.append(overlapping["max_change_score"].max() if len(overlapping) else None)
        if len(overlapping):
            biggest = overlapping.loc[overlapping.geometry.area.idxmax()]
            site_ids.append(biggest["change_id"])
        else:
            site_ids.append(None)

        near = buf_geom.intersects(change_union) and intersection.area == 0
        near_flags.append(bool(near))

    out["change_area_m2"] = [round(a, 2) for a in change_areas]
    out["change_ratio"] = [
        round(a / b, 4) if b > 0 else 0.0
        for a, b in zip(change_areas, out["building_area_m2"])
    ]
    out["max_change_score"] = max_scores
    out["near_change"] = near_flags
    out["site_id"] = site_ids

    n_sites = out.loc[out["change_ratio"] > 0, "site_id"].nunique()
    logger.info(
        "[BUILDING] Overlay 완료: 건물 %d개 중 change_ratio>0 인 건물 %d개 (서로 다른 현장 %d곳)",
        len(out), int((out["change_ratio"] > 0).sum()), n_sites,
    )
    return out
