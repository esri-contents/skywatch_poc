"""연도쌍별 change polygon -> hotspot -> 고해상도(SkyWatch) ROI / 촬영연도 추천.

Sentinel-2(10m) 결과는 "어디가 바뀌었는가"를 좁히는 용도다. 여기서 만드는 hotspot/ROI는
change candidate이며 지장물/보상대상/불법건축물 판정이 아니다.

입력 polygon GeoDataFrame 스키마(projected CRS, m 단위 가정):
    geometry, pair("2016_2018"), year_from, year_to, pair_kind("step"|"long_term"),
    change_area_m2, mean_change_score
변화 polygon이 0건이어도(빈 GeoDataFrame) 모든 함수는 빈 결과를 정상 반환한다.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

POLYGON_COLUMNS = ["pair", "year_from", "year_to", "pair_kind", "change_area_m2", "mean_change_score", "geometry"]

HOTSPOT_COLUMNS = [
    "hotspot_id", "first_year", "last_year", "change_pairs", "change_area_m2", "change_score",
    "change_frequency", "centroid_x", "centroid_y", "distance_to_railway_m", "priority_score",
    "recommended_for_highres", "geometry",
]
ROI_COLUMNS = ["roi_id", "hotspot_id", "roi_size_m", "roi_area_km2", "roi_clipped", "geometry"]


def empty_polygons(crs) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns=POLYGON_COLUMNS, geometry="geometry", crs=crs)


def pair_list(years: list[int], consecutive: bool = True, long_term: list[list[int]] | None = None) -> list[dict]:
    """연도 목록 -> 비교할 연도쌍 목록 (step: 인접 연도, long_term: 장기 비교).

    long_term 중 years에 없는 연도가 들어 있거나 step과 중복되는 쌍은 제외한다.
    """
    years = sorted(set(int(y) for y in years))
    pairs: list[dict] = []
    if consecutive:
        pairs += [{"year_from": a, "year_to": b, "kind": "step"} for a, b in zip(years[:-1], years[1:])]
    seen = {(p["year_from"], p["year_to"]) for p in pairs}
    for a, b in (long_term or []):
        a, b = int(a), int(b)
        if a in years and b in years and a < b and (a, b) not in seen:
            pairs.append({"year_from": a, "year_to": b, "kind": "long_term"})
            seen.add((a, b))
    for p in pairs:
        p["pair"] = f"{p['year_from']}_{p['year_to']}"
    return pairs


def build_hotspots(
    polygons: gpd.GeoDataFrame,
    cluster_distance_m: float = 60.0,
    min_hotspot_area_m2: float = 0.0,
    railway: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """공간적으로 가까운 변화 polygon을 묶어 hotspot을 만든다 (순위 미부여 - rank_hotspots 참고).

    - 묶기: 각 polygon을 cluster_distance_m/2 만큼 buffer해 합친 연결 성분.
    - first_year/last_year/change_frequency는 step(인접 연도) 쌍 기준(해당 hotspot에 step 쌍
      polygon이 없으면 long_term 쌍으로 대체). change_pairs는 관측된 모든 쌍 라벨.
    - change_area_m2: hotspot 내 polygon들의 합집합 면적(여러 쌍에서 같은 곳이 반복되어도
      중복 합산하지 않는다). change_score: 면적 가중 평균 mean_change_score.
    """
    crs = polygons.crs
    if polygons.empty:
        return gpd.GeoDataFrame(columns=HOTSPOT_COLUMNS, geometry="geometry", crs=crs)

    polys = polygons.reset_index(drop=True)
    merged = polys.geometry.buffer(cluster_distance_m / 2.0).union_all()
    comps = gpd.GeoDataFrame(geometry=list(getattr(merged, "geoms", [merged])), crs=crs)
    comps["comp"] = range(len(comps))
    labelled = gpd.sjoin(
        gpd.GeoDataFrame(polys.drop(columns="geometry"), geometry=polys.geometry.representative_point(), crs=crs),
        comps, how="left", predicate="within",
    )
    labelled = labelled[~labelled.index.duplicated(keep="first")]
    polys["comp"] = labelled["comp"].reindex(polys.index).to_numpy()

    rows = []
    for comp, grp in polys.groupby("comp"):
        step = grp[grp["pair_kind"] == "step"]
        basis = step if len(step) else grp
        geom = grp.geometry.union_all()
        area = float(geom.area)
        if area < min_hotspot_area_m2:
            continue
        w = grp["change_area_m2"].to_numpy(dtype=float)
        s = grp["mean_change_score"].to_numpy(dtype=float)
        score = float(np.average(s, weights=w)) if w.sum() > 0 else float("nan")
        c = geom.centroid
        dist = float(geom.distance(railway.geometry.union_all())) if railway is not None and len(railway) else None
        rows.append({
            "first_year": int(basis["year_to"].min()),
            "last_year": int(basis["year_to"].max()),
            "change_pairs": ",".join(sorted(grp["pair"].unique())),
            "change_area_m2": round(area, 1),
            "change_score": round(score, 4),
            "change_frequency": int(basis["pair"].nunique()),
            "centroid_x": round(c.x, 1), "centroid_y": round(c.y, 1),
            "distance_to_railway_m": None if dist is None else round(dist, 1),
            "geometry": geom,
        })
    if not rows:
        return gpd.GeoDataFrame(columns=HOTSPOT_COLUMNS, geometry="geometry", crs=crs)
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    out["priority_score"] = (out["change_area_m2"] * out["change_score"] * out["change_frequency"]).round(1)
    out["hotspot_id"] = ""
    out["recommended_for_highres"] = False
    return out[HOTSPOT_COLUMNS]


def rank_hotspots(
    hotspots: gpd.GeoDataFrame,
    top_n: int = 5,
    recommend_min_area_m2: float = 0.0,
    recommend_min_score: float = 0.0,
) -> gpd.GeoDataFrame:
    """priority_score(= 면적 x 점수 x 빈도) 내림차순으로 HS_01.. ID를 부여하고 추천 여부를 표시한다."""
    if hotspots.empty:
        return hotspots
    out = hotspots.sort_values("priority_score", ascending=False).reset_index(drop=True)
    out["hotspot_id"] = [f"HS_{i:02d}" for i in range(1, len(out) + 1)]
    out["recommended_for_highres"] = (
        (out.index < top_n)
        & (out["change_area_m2"] >= recommend_min_area_m2)
        & (out["change_score"] >= recommend_min_score)
    )
    return out


def build_roi(
    hotspots: gpd.GeoDataFrame,
    buffer_m: float = 300.0,
    min_size_m: float = 500.0,
    overlap_merge: float = 0.5,
    max_size_m: float | None = None,
) -> gpd.GeoDataFrame:
    """추천 hotspot별 SkyWatch ROI(정사각 bbox)를 만든다.

    ROI = hotspot bounds + buffer_m 를 감싸는 정사각형, 한 변이 min_size_m 미만이면 중심 기준으로
    min_size_m 까지 확장(주변 맥락 확보). 이미 선택된 상위 ROI와 면적 겹침이 overlap_merge 이상이면
    중복 구매를 피하기 위해 생략한다. max_size_m이 있으면 한 변이 그 값을 넘지 않게 hotspot 중심
    기준으로 자르고(roi_clipped=True) hotspot 일부가 ROI 밖에 남을 수 있음을 표시한다.
    """
    crs = hotspots.crs
    rec = hotspots[hotspots["recommended_for_highres"]] if len(hotspots) else hotspots
    rows: list[dict] = []
    for _, h in rec.iterrows():
        minx, miny, maxx, maxy = h.geometry.buffer(buffer_m).bounds
        side = max(maxx - minx, maxy - miny, min_size_m)
        clipped = max_size_m is not None and side > max_size_m
        if clipped:
            side = max_size_m
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        roi = box(cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)
        if any(roi.intersection(r["geometry"]).area / roi.area >= overlap_merge for r in rows):
            continue
        rows.append({"hotspot_id": h["hotspot_id"], "roi_size_m": round(side, 1),
                     "roi_area_km2": round(roi.area / 1e6, 3), "roi_clipped": bool(clipped), "geometry": roi})
    if not rows:
        return gpd.GeoDataFrame(columns=ROI_COLUMNS, geometry="geometry", crs=crs)
    out = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    out.insert(0, "roi_id", [f"ROI_{i:02d}" for i in range(1, len(out) + 1)])
    return out[ROI_COLUMNS]


def pair_change_summary(
    polygons: gpd.GeoDataFrame, pairs: list[dict], hotspots: gpd.GeoDataFrame, corridor=None
) -> pd.DataFrame:
    """연도쌍별 year_from/year_to/change_area/polygon_count/hotspot_count 표 (0건인 쌍도 행을 유지).

    corridor(shapely geometry, 예: 철도 선형 buffer)를 주면 그 안쪽 변화 면적을
    corridor_change_area_m2 컬럼으로 추가한다(철도 인접 변화량 - 비공식 OSM 선형 기준).
    """
    rows = []
    for p in pairs:
        sub = polygons[polygons["pair"] == p["pair"]] if len(polygons) else polygons
        hs = 0
        if len(hotspots):
            hs = int(hotspots["change_pairs"].str.split(",").apply(lambda ps: p["pair"] in ps).sum())
        row = {
            "year_from": p["year_from"], "year_to": p["year_to"], "kind": p["kind"],
            "change_area_m2": round(float(sub["change_area_m2"].sum()), 1) if len(sub) else 0.0,
            "polygon_count": int(len(sub)), "hotspot_count": hs,
        }
        if corridor is not None:
            row["corridor_change_area_m2"] = round(float(sub.geometry.intersection(corridor).area.sum()), 1) if len(sub) else 0.0
        rows.append(row)
    return pd.DataFrame(rows)


def recommend_acquisition_years(
    pair_summary: pd.DataFrame,
    timeline_years: list[int],
    active_fraction_of_max: float = 0.25,
) -> dict:
    """연도쌍별 변화량으로 고해상도 영상 "촬영연도 제안"을 만든다 (최종 구매연도 확정 아님).

    step 쌍 중 변화 면적이 최대 쌍의 active_fraction_of_max 이상인 쌍을 active로 본다.
      - 첫 active 쌍의 year_from : 변화 전 baseline
      - 각 active 쌍의 year_to   : 변화 검출/진행 시점
      - 2년 이상 간격 active 쌍의 중간 연도 : 변화 시점을 좁히기 위한 중간 연도
      - 마지막 active 쌍 다음 timeline 연도 : 변화 이후 안정화 확인 (마지막 연도면 이미 포함)
    Sentinel-2에서 변화가 하나도 없으면(Zero) 근거 없이 연도를 만들지 않고 timeline 양 끝
    연도만 "비교 anchor"로 제안한다.
    """
    years = sorted(set(int(y) for y in timeline_years))
    step = pair_summary[pair_summary["kind"] == "step"]
    notes = [
        "Sentinel-2는 2015-06 이후만 존재: 착공(2015) 이전(2010~2015) baseline은 Sentinel-2로 "
        "Screening할 수 없으므로, 필요 시 SkyWatch 아카이브의 가용 연도를 별도로 확인해야 한다.",
        "본 제안은 Sentinel-2 변화량 기반 참고용이며 최종 구매연도 확정이 아니다.",
    ]
    if step.empty:
        return {
            "years": [], "active_pairs": [],
            "notes": notes + ["인접 연도쌍(step)이 없어(예: Phase A 단일 쌍) 촬영연도를 제안하지 않는다 - Phase B 실행 필요."],
        }
    max_area = float(step["change_area_m2"].max())
    if max_area <= 0:
        return {
            "years": [
                {"year": years[0], "role": "비교 anchor", "reason": "Sentinel-2에서는 유의미한 변화가 검출되지 않음 - 시계열 시작 anchor"},
                {"year": years[-1], "role": "비교 anchor", "reason": "Sentinel-2에서는 유의미한 변화가 검출되지 않음 - 시계열 종료 anchor"},
            ],
            "active_pairs": [], "notes": notes,
        }
    active = step[step["change_area_m2"] >= active_fraction_of_max * max_area].sort_values("year_from")
    if len(active) == len(step) and len(step) > 1 and float(step["change_area_m2"].min()) >= 0.6 * max_area:
        notes.append(
            "경고: 변화 면적이 모든 인접 구간에서 비슷한 규모(최소/최대 >= 0.6)로 지속된다. 특정 시점의 사업(공사) "
            "신호라기보다 채석·산업 활동, 계절/식생, 방사 차이 등 상시 변화일 수 있어 Sentinel-2만으로는 변화 시점을 "
            "좁히기 어렵다. 아래 연도는 '구분력 낮은 일괄 제안'이며, corridor_change_area_m2(철도 인접 변화)와 "
            "고해상도 육안 확인으로 연도를 줄여야 한다."
        )
    out: dict[int, dict] = {}

    def add(year: int, role: str, reason: str) -> None:
        if year not in out:
            out[year] = {"year": year, "role": role, "reason": reason}

    first = active.iloc[0]
    add(int(first.year_from), "변화 전 baseline",
        f"첫 active 구간({int(first.year_from)}→{int(first.year_to)}) 시작 - 변화 이전 상태 확인")
    for _, r in active.iterrows():
        a, b = int(r.year_from), int(r.year_to)
        add(b, "변화 검출/진행",
            f"{a}→{b} 변화 후보 {r.change_area_m2:,.0f} m² (polygon {int(r.polygon_count)}건)")
        if b - a >= 2:
            add((a + b) // 2, "중간 연도(변화 시점 좁히기)", f"{a}→{b}는 {b - a}년 간격 - 변화 시점을 좁히기 위함")
    last_b = int(active.iloc[-1].year_to)
    later = [y for y in years if y > last_b]
    if later:
        add(later[0], "변화 후 안정화 확인", f"마지막 active 구간({last_b}) 이후 상태 확인")
    return {
        "years": sorted(out.values(), key=lambda d: d["year"]),
        "active_pairs": [f"{int(r.year_from)}_{int(r.year_to)}" for _, r in active.iterrows()],
        "notes": notes,
    }
