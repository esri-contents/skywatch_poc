"""철도 인접성 계산과 현장조사 검토 우선순위 점수.

점수/가중치는 검증된 행정적 판단 기준이 아니라 초기 탐색용 설정값이며, 철도선은 OSM 기반 비공식 선형이다.
하위 점수는 모두 0~100으로 정규화하고 종합 점수 = sum(w_i * score_i) / sum(w_i).
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely


def load_railway(path: str, include_values: list[str], official_path: str | None = None,
                 crs: str = "EPSG:5186") -> tuple[gpd.GeoDataFrame, dict]:
    """철도선 로드. official_path가 있으면 OSM 대신 공식 데이터를 쓴다(확장 지점)."""
    src_path, kind = (official_path, "OFFICIAL") if official_path else (path, "OSM_UNOFFICIAL")
    rail = gpd.read_file(src_path)
    n_all = len(rail)
    if kind == "OSM_UNOFFICIAL" and "railway" in rail.columns and include_values:
        rail = rail[rail["railway"].isin(include_values)]
    rail = rail[rail.geometry.notna() & ~rail.geometry.is_empty].to_crs(crs)
    meta = {"kind": kind, "path": str(src_path), "n_features_all": int(n_all), "n_features_used": int(len(rail)),
            "length_km": float(rail.length.sum() / 1000.0),
            "note": "OSM 기반 비공식 철도선. 공식 철도사업구역/보상구역이 아니며 법적 근거로 쓸 수 없는 참고 지표."
            if kind != "OFFICIAL" else "공식 철도 데이터(사용자 제공)"}
    return rail, meta


def rail_distance(parcels: gpd.GeoSeries, rail: gpd.GeoDataFrame, bands_m: list[float]) -> pd.DataFrame:
    """필지(전체 geometry) 경계~철도선 최단거리(m)와 거리 대역 포함 여부."""
    if rail.empty:
        return pd.DataFrame({"rail_distance_m": np.nan}, index=range(len(parcels)))
    u = shapely.union_all(rail.geometry.values)
    d = shapely.distance(parcels.values, u)
    out = pd.DataFrame({"rail_distance_m": d})
    # OSM tunnel=yes 구간만 가장 가까운 경우(지상 선형과 이격) 표시 - 참고 정보
    if "tunnel" in rail.columns:
        surf = rail[rail["tunnel"].astype(str).str.lower() != "yes"]
        if len(surf) and len(surf) < len(rail):
            ds = shapely.distance(parcels.values, shapely.union_all(surf.geometry.values))
            out["rail_distance_surface_m"] = ds
            out["rail_nearest_is_tunnel"] = ds > d + 1.0
        else:
            out["rail_distance_surface_m"] = d
            out["rail_nearest_is_tunnel"] = False
    for b in bands_m:
        out[f"within_{int(b)}m"] = out["rail_distance_m"] <= b
    return out


def priority_scores(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    out = df.copy()
    w = p["weights"]
    ratio = out["decline_ratio"].fillna(0.0).to_numpy(float)
    out["score_decline_ratio"] = np.clip(ratio / p["decline_ratio_full_score"], 0, 1) * 100
    out["score_persistence"] = out["persistence_status"].map(p["persistence_scores"]).fillna(0.0).astype(float)
    d = out["rail_distance_m"].to_numpy(float)
    lo, hi = float(p["rail_full_score_distance_m"]), float(p["rail_zero_score_distance_m"])
    out["score_rail_proximity"] = np.where(np.isfinite(d), np.clip((hi - d) / (hi - lo), 0, 1) * 100, 0.0)
    tw = sum(w.values())
    out["priority_score"] = (w["decline_ratio"] * out["score_decline_ratio"]
                             + w["persistence"] * out["score_persistence"]
                             + w["rail_proximity"] * out["score_rail_proximity"]) / tw
    cand = (out["change_type"].isin(p["review_types"])
            & (out["decline_area_m2"] >= p["min_decline_area_m2"])
            & (out["quality_status"] != "NOT_EVALUABLE"))
    out["is_review_candidate"] = cand
    out["priority_rank"] = pd.array([pd.NA] * len(out), dtype="Int64")
    order = out[cand].sort_values(["priority_score", "decline_area_m2"], ascending=False).index
    out.loc[order, "priority_rank"] = np.arange(1, len(order) + 1)
    tiers = p.get("tiers", {"high": 60, "medium": 40})
    out["priority_tier"] = np.where(~cand, "해당없음",
                                    np.where(out["priority_score"] >= tiers["high"], "상",
                                             np.where(out["priority_score"] >= tiers["medium"], "중", "하")))
    return out
