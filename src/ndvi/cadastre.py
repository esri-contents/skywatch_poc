"""연속지적도(VWorld lp_pa_cbnd_*) 확보/정규화 - NDVI 필지 통계의 입력.

실측 확인(2026, 단양 AOI):
- lp_pa_cbnd_bubun : id, pnu, jibun, bchk, std_sggcd, bubun, bonbun, addr, ctp_nm, sig_nm, emd_nm, li_nm,
                     gosi_year, gosi_month, jiga 보유. 본번 단독 필지(bubun 없음)도 포함하는 전체 필지 레이어.
- lp_pa_cbnd_bonbun: id, col_adm_se, bonbun만 존재. PNU/지번이 없고 geometry가 bubun 레이어에 99.7% 포함됨.
  -> PNU 기반 분석에는 bubun만 쓰고, bonbun은 커버리지 QA에만 쓴다. PNU를 임의 생성하지 않는다.

현재 시점의 연속지적도를 과거 영상에 중첩하는 분석이며 과거 필지 분할/합병 이력을 재현하지 않는다.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

logger = logging.getLogger(__name__)

ANALYSIS_CRS = "EPSG:5186"
_JIBUN_RE = re.compile(r"^\s*(산)?\s*(\d+)(?:\s*-\s*(\d+))?\s*(.*)$")


def parse_jibun(raw: str | None) -> dict:
    """'202-14 도' / '산38-1임' / '370천' -> 산여부, 본번, 부번, 지목약칭, 정규화 지번('산 38-1')."""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return {"is_mountain": None, "bonbun_no": None, "bubun_no": None, "jimok_abbr": None, "jibun_norm": None}
    m = _JIBUN_RE.match(str(raw))
    if not m:
        return {"is_mountain": None, "bonbun_no": None, "bubun_no": None, "jimok_abbr": None,
                "jibun_norm": None}
    san, bon, bu, rest = m.groups()
    norm = f"{'산 ' if san else ''}{int(bon)}" + (f"-{int(bu)}" if bu not in (None, "") else "")
    return {"is_mountain": bool(san), "bonbun_no": int(bon), "bubun_no": int(bu) if bu else None,
            "jimok_abbr": rest.strip() or None, "jibun_norm": norm}


def jibun_from_pnu(pnu: str | None) -> str | None:
    """PNU(19자리) 구조: 시군구+읍면동+리(10) + 대지구분(1: 1 일반, 2 산) + 본번(4) + 부번(4)."""
    if not isinstance(pnu, str) or len(pnu) != 19 or not pnu.isdigit():
        return None
    bon, bu = int(pnu[11:15]), int(pnu[15:19])
    return f"{'산 ' if pnu[10] == '2' else ''}{bon}" + (f"-{bu}" if bu else "")


def _polygonal(g):
    """Geometry를 Polygon/MultiPolygon으로 제한(수선 후 선/점 조각 제거)."""
    if g is None or g.is_empty:
        return None
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    if isinstance(g, GeometryCollection):
        polys = [p for p in g.geoms if isinstance(p, (Polygon, MultiPolygon)) and not p.is_empty]
        if not polys:
            return None
        return shapely.union_all(polys)
    return None


def fetch_vworld_layers(bbox_wgs84: tuple[float, float, float, float], cache_dir: Path,
                        layers: list[str]) -> dict[str, Path]:
    """기존 src.data.download.download_vworld_wfs_layer 재사용(페이지네이션/bbox 분할 포함)."""
    from src.data.download import download_vworld_wfs_layer

    out = {}
    for layer in layers:
        p = cache_dir / f"{layer}_raw.geojson"
        download_vworld_wfs_layer(layer, bbox_wgs84, p)
        out[layer] = p
    return out


def normalize_parcels(raw: gpd.GeoDataFrame, aoi: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoSeries, dict]:
    """원본 필지 -> EPSG:5186, 유효 geometry, PNU 문자열 보존, 중복 제거, AOI 교차/면적 분리."""
    qa: dict = {"n_raw": int(len(raw)), "warnings": []}
    gdf = raw.copy()
    if gdf.crs is None:
        qa["warnings"].append("원본 CRS 없음: EPSG:4326으로 가정 (VWorld 기본). 검증 필요")
        gdf = gdf.set_crs(4326)
    qa["source_crs"] = str(gdf.crs)
    gdf = gdf.to_crs(ANALYSIS_CRS)

    # --- 속성 정규화 (PNU는 문자열 보존: 앞자리 0 유지)
    for col in ("pnu", "jibun", "addr", "emd_nm", "li_nm", "jiga", "gosi_year", "gosi_month", "id"):
        if col not in gdf.columns:
            gdf[col] = None
    gdf["pnu"] = gdf["pnu"].astype(object).map(
        lambda v: None if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v) or str(v).strip() == ""
        else str(v).strip())
    parsed = pd.DataFrame([parse_jibun(v) for v in gdf["jibun"]], index=gdf.index)
    gdf = pd.concat([gdf, parsed], axis=1)
    pnu_jibun = gdf["pnu"].map(jibun_from_pnu)
    gdf["jibun_pnu_derived"] = pnu_jibun
    mism = (gdf["jibun_norm"].notna() & pnu_jibun.notna() & (gdf["jibun_norm"] != pnu_jibun))
    qa["jibun_vs_pnu_mismatch"] = int(mism.sum())
    # 지번 표기: 속성 jibun이 있으면 그것을 정규화한 값, 없으면 PNU에서 유도한 값(둘 다 실제 데이터 기반)
    gdf["jibun_display"] = gdf["jibun_norm"].fillna(pnu_jibun)
    gdf["pnu_missing"] = gdf["pnu"].isna()
    gdf["pnu_len_ok"] = gdf["pnu"].map(lambda v: isinstance(v, str) and len(v) == 19 and v.isdigit())
    qa["pnu_missing"] = int(gdf["pnu_missing"].sum())
    qa["pnu_bad_format"] = int((~gdf["pnu_len_ok"] & ~gdf["pnu_missing"]).sum())

    # --- Geometry 유효성
    invalid = ~gdf.geometry.is_valid
    qa["invalid_geometry_repaired"] = int(invalid.sum())
    gdf["geom_repaired"] = invalid.values
    fixed = gdf.geometry.copy()
    fixed[invalid] = fixed[invalid].map(shapely.make_valid)
    gdf["geometry"] = fixed.map(_polygonal)
    empty = gdf.geometry.isna()
    qa["empty_or_nonpolygon_dropped"] = int(empty.sum())
    gdf = gdf[~empty].copy()

    # --- 중복 제거 (PNU 기준, 면적이 큰 것 유지). PNU 없는 필지는 임의 PNU를 만들지 않고 유지.
    gdf["_area"] = gdf.geometry.area
    has = gdf[~gdf["pnu_missing"]].sort_values("_area", ascending=False)
    dup_mask = has.duplicated("pnu", keep="first")
    qa["duplicate_pnu_removed"] = int(dup_mask.sum())
    gdf = pd.concat([has[~dup_mask], gdf[gdf["pnu_missing"]]]).sort_index()
    gdf = gdf.drop(columns="_area")
    qa["n_after_dedup"] = int(len(gdf))

    # --- AOI 교차 및 면적 분리
    aoi_geom = shapely.union_all(aoi.to_crs(ANALYSIS_CRS).geometry.values)
    hit = gdf.geometry.intersects(aoi_geom)
    gdf = gdf[hit].copy()
    clipped = gdf.geometry.intersection(aoi_geom).map(_polygonal)
    keep = clipped.notna() & (clipped.map(lambda g: g.area if g is not None else 0) > 0.01)
    gdf, clipped = gdf[keep].copy(), clipped[keep]
    gdf["area_total_m2"] = gdf.geometry.area
    gdf["area_in_aoi_m2"] = clipped.area
    gdf["parcel_uid"] = gdf["pnu"].fillna(gdf["id"].astype(str))
    gdf = gdf.reset_index(drop=True)
    clipped = gpd.GeoSeries(clipped.values, crs=ANALYSIS_CRS)
    qa["n_in_aoi"] = int(len(gdf))
    qa["area_in_aoi_sum_m2"] = float(gdf["area_in_aoi_m2"].sum())
    qa["aoi_area_m2"] = float(aoi_geom.area)
    qa["aoi_covered_fraction_by_parcels"] = float(shapely.union_all(clipped.values).area / aoi_geom.area)
    qa["parcel_overlap_area_m2"] = float(max(0.0, clipped.area.sum() - shapely.union_all(clipped.values).area))
    return gdf, clipped, qa


def qa_bonbun_coverage(bonbun: gpd.GeoDataFrame, parcels_clipped: gpd.GeoSeries, aoi: gpd.GeoDataFrame) -> dict:
    """bonbun 레이어(PNU 없음)가 PNU 보유 필지로 얼마나 커버되는지 확인(누락 필지 의심 지표)."""
    aoi_geom = shapely.union_all(aoi.to_crs(ANALYSIS_CRS).geometry.values)
    b = bonbun.to_crs(ANALYSIS_CRS)
    b = b[b.geometry.intersects(aoi_geom)]
    if b.empty:
        return {"bonbun_features_in_aoi": 0}
    bu = shapely.union_all(shapely.make_valid(b.geometry.values)).intersection(aoi_geom)
    cov = shapely.union_all(parcels_clipped.values)
    unc = bu.difference(cov)
    return {
        "bonbun_features_in_aoi": int(len(b)),
        "bonbun_area_in_aoi_m2": float(bu.area),
        "bonbun_not_covered_by_pnu_parcels_m2": float(unc.area),
        "bonbun_not_covered_fraction": float(unc.area / bu.area) if bu.area else None,
        "interpretation": "bonbun 레이어에는 PNU가 없어 필지로 쓰지 않았다. 미커버 면적이 크면 PNU 필지 누락을 의심한다.",
    }


def load_cadastre(cfg: dict, aoi: gpd.GeoDataFrame, local_path: str | Path | None = None,
                  offline: bool = False, fetch: bool = False) -> tuple[gpd.GeoDataFrame | None, gpd.GeoSeries | None, dict]:
    """연속지적도를 확보한다. 실패해도 예외 대신 status=unavailable 메타를 돌려준다."""
    c = cfg["ndvi"]["cadastre"]
    cache = Path(c["cache_dir"])
    primary, qa_layer = c["primary_layer"], c.get("qa_layer")
    meta: dict = {"status": "unavailable", "source": None, "layers": [], "notes": []}
    lp = local_path or c.get("local_path")
    raw = bonbun = None
    try:
        if lp:
            lp = Path(lp)
            raw = gpd.read_file(lp)
            meta.update(source=f"local file: {lp}", layers=[lp.name], mtime=datetime.fromtimestamp(lp.stat().st_mtime).isoformat(timespec="seconds"))
        else:
            p_primary = cache / f"{primary}_raw.geojson"
            p_qa = cache / f"{qa_layer}_raw.geojson" if qa_layer else None
            if fetch or (not p_primary.exists() and not offline):
                if offline:
                    raise RuntimeError("--offline 상태에서 --fetch-cadastre 불가")
                a4326 = aoi.to_crs(4326).total_bounds
                pad = float(c.get("fetch_buffer_deg", 0.002))
                bbox = (a4326[0] - pad, a4326[1] - pad, a4326[2] + pad, a4326[3] + pad)
                fetch_vworld_layers(bbox, cache, [primary] + ([qa_layer] if qa_layer else []))
                meta["notes"].append("VWorld WFS에서 이번 실행 중 새로 수집")
            if not p_primary.exists():
                raise FileNotFoundError(f"연속지적도 캐시가 없습니다: {p_primary} (--cadastre 또는 --fetch-cadastre 사용)")
            raw = gpd.read_file(p_primary)
            if p_qa is not None and p_qa.exists():
                bonbun = gpd.read_file(p_qa)
            meta.update(source=f"VWorld WFS(api.vworld.kr) {primary}", layers=[primary] + ([qa_layer] if bonbun is not None else []),
                        retrieved_file_mtime=datetime.fromtimestamp(p_primary.stat().st_mtime).isoformat(timespec="seconds"))
    except Exception as e:  # noqa: BLE001 - 지적도 실패가 NDVI 분석 전체를 막지 않도록 한다
        meta["error"] = f"{type(e).__name__}: {e}"
        logger.warning("[CADASTRE] 확보 실패: %s", meta["error"])
        return None, None, meta

    gdf, clipped, qa = normalize_parcels(raw, aoi)
    if bonbun is not None:
        qa["bonbun_coverage"] = qa_bonbun_coverage(bonbun, clipped, aoi)
    meta["qa"] = qa
    meta["status"] = "ok" if len(gdf) else "empty"
    if "gosi_year" in raw.columns and raw["gosi_year"].notna().any():
        yr = raw["gosi_year"].dropna().astype(str).value_counts()
        mo = raw["gosi_month"].dropna().astype(str).value_counts() if "gosi_month" in raw.columns else None
        meta["gosi_attr"] = {"year": yr.index[0], "month": mo.index[0] if mo is not None and len(mo) else None,
                             "note": "속성 gosi_year/month(공시지가 기준 시점). 지적경계 데이터의 정확한 갱신일과 동일하다고 단정할 수 없음."}
    meta["temporal_caveat"] = "현재 시점 연속지적도를 과거 영상에 중첩한 분석이며 과거 필지 분할·합병 이력을 재현하지 않는다."
    return gdf, clipped, meta
