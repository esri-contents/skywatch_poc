"""판독 기준 건물 레이어 - GIS건물통합정보 + 도로명주소 건물 + 건축물대장.

v1.x는 VWorld 도로명주소 건물(광명시흥 5,107동)을 판독기 학습 정답이자 '등재' 기준으로 썼다. 도로명주소는
대장 없는 건물에도 붙고, 창고·가설물 같은 작은 건물은 빠져 있어 두 역할 모두에 맞지 않았다. v2.0은:

- 윤곽: GIS건물통합정보(국토부 AL_D010, VWorld NED API) 전부 + 그와 겹치지 않는 도로명주소 건물.
  광명시흥 지구 안 11,190동(도로명주소 건물의 73%가 건물통합정보에 있음).
- 대장 연계(reg): 건물통합정보의 대장 연계 번호(buld_idntfc_no)가 있으면 '연계'. 그 번호는 건축HUB의 대장
  관리번호(mgmBldrgstPk)와 체계가 달라 직접 맞지 않으므로, 대장 표제부와는 필지(key9)·건축면적·연면적·사용승인일로
  맞춰 주용도·관리번호를 붙인다(광명시흥 연계 2,775동 중 2,123동이 넷 모두 일치).
  도로명주소에만 있는 건물은 대장 연계 정보가 없어, 필지에 대장이 있으면 '연계'로 본다(v1.x와 같은 가정).
- 위반건축물(violt): 건물통합정보의 위반건축물 여부.

판독기 학습 정답은 윤곽 전부(대장 여부 무관 - 지붕인지가 정답), 미등재 판정은 reg='연계' 윤곽만 등재로 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from .reference import key9

LINKED, UNLINKED = "연계", "미연계"
COLUMNS = ["source", "pnu", "reg", "reg_pk", "reg_purpose", "use_date", "violt", "area_m2", "geometry"]


def _title(title_path) -> pd.DataFrame:
    if not title_path or not Path(title_path).exists():
        return pd.DataFrame(columns=["k", "arch", "tot", "d", "mgmBldrgstPk", "mainPurpsCdNm"])
    t = pd.DataFrame(json.loads(Path(title_path).read_text(encoding="utf-8")))
    return pd.DataFrame({"k": key9(t["pnu"].astype(str)), "arch": pd.to_numeric(t.get("archArea"), errors="coerce"),
                         "tot": pd.to_numeric(t.get("totArea"), errors="coerce"),
                         "d": t.get("useAprDay", pd.Series("", index=t.index)).astype(str).str[:8],
                         "mgmBldrgstPk": t["mgmBldrgstPk"].astype(str),
                         "mainPurpsCdNm": t.get("mainPurpsCdNm", pd.Series("", index=t.index))})


def match_register(gis: pd.DataFrame, title: pd.DataFrame, min_score: int = 2) -> pd.DataFrame:
    """대장 연계 건물을 표제부 행과 맞춘다. 점수 = 건축면적·연면적(±0.5㎡)·사용승인일 일치 수, min_score 이상만.

    반환: gis 색인별 reg_pk, reg_purpose(맞춘 것만). 한 표제부는 점수가 가장 높은 건물 하나에만 붙는다.
    """
    g = gis.assign(k=key9(gis["pnu"].fillna("").astype(str)),
                   d=pd.to_datetime(gis["use_confm_de"], errors="coerce").dt.strftime("%Y%m%d"))
    m = g[["k", "ar", "totar", "d"]].reset_index(names="gi").merge(title, on="k", how="inner", suffixes=("", "_t"))
    if not len(m):
        return pd.DataFrame(columns=["reg_pk", "reg_purpose"])
    m["score"] = (((m["arch"] - m["ar"]).abs() <= 0.5).astype(int) + ((m["tot"] - m["totar"]).abs() <= 0.5).astype(int)
                  + (m["d"] == m["d_t"]).astype(int))
    m = m[m["score"] >= min_score].sort_values("score", ascending=False)
    m = m.drop_duplicates("gi").drop_duplicates("mgmBldrgstPk")
    return m.set_index("gi")[["mgmBldrgstPk", "mainPurpsCdNm"]].rename(
        columns={"mgmBldrgstPk": "reg_pk", "mainPurpsCdNm": "reg_purpose"})


def build(gis_path, road_path, title_path, aoi_path, out_path, crs: str = "EPSG:5186",
          road_overlap_m: float = 0.5) -> gpd.GeoDataFrame:
    """기준 건물 레이어를 만들어 out_path(gpkg)에 쓴다. 지구 경계와 겹치는 건물만."""
    aoi = gpd.read_file(aoi_path).to_crs(crs).union_all()
    # 내려받기(download_vworld_gis_buildings)는 언제나 EPSG:5186 좌표로 쓴다. GeoJSON 읽기가 crs를 4326으로 붙여도 5186으로 본다.
    gis = gpd.read_file(gis_path).set_crs(5186, allow_override=True).to_crs(crs)
    gis = gis[gis.intersects(aoi) & (gis.area > 0)].reset_index(drop=True)
    title = _title(title_path)
    linked = gis["buld_idntfc_no"].notna()
    reg = match_register(gis[linked], title)
    out = gpd.GeoDataFrame({
        "source": "gis", "pnu": gis["pnu"].astype(str), "reg": np.where(linked, LINKED, UNLINKED),
        "reg_pk": reg["reg_pk"].reindex(gis.index).fillna("").to_numpy(),
        "reg_purpose": reg["reg_purpose"].reindex(gis.index).fillna("").to_numpy(),
        "use_date": pd.to_datetime(gis["use_confm_de"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("").to_numpy(),
        "violt": (gis["violt_bild"] == "Y").to_numpy(),
    }, geometry=gis.geometry.values, crs=crs)

    road = gpd.read_file(road_path).to_crs(crs)
    road = road[road.intersects(aoi) & (road.area > 0)]
    covered = road.geometry.representative_point().within(gis.buffer(road_overlap_m).union_all())
    road = road[~covered.to_numpy()].reset_index(drop=True)
    if len(road):
        rk = key9(road["pnu"].fillna("").astype(str)) if "pnu" in road else pd.Series("", index=road.index)
        has_reg = rk.isin(set(title["k"]))
        out = pd.concat([out, gpd.GeoDataFrame({
            "source": "road", "pnu": road.get("pnu", pd.Series("", index=road.index)).astype(str),
            "reg": np.where(has_reg, LINKED, UNLINKED), "reg_pk": "", "reg_purpose": "", "use_date": "",
            "violt": False}, geometry=road.geometry.values, crs=crs)], ignore_index=True)
    out["area_m2"] = out.area.round(1)
    out = gpd.GeoDataFrame(out[COLUMNS], geometry="geometry", crs=crs)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.to_file(out_path, layer="buildings", driver="GPKG")
    return out


def load(path, crs: str = "EPSG:5186") -> gpd.GeoDataFrame:
    b = gpd.read_file(path).to_crs(crs)
    return b[b.area > 0].reset_index(drop=True)


def count_in_parcels(b: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame) -> pd.Series:
    """필지마다 대표점이 들어가는 건물 수(색인 = parcels 색인)."""
    if not len(b):
        return pd.Series(0, index=parcels.index)
    pts = gpd.GeoDataFrame(geometry=b.geometry.representative_point().values, crs=b.crs).to_crs(parcels.crs)
    j = gpd.sjoin(pts, parcels[["geometry"]], predicate="within")
    j = j[~j.index.duplicated()]
    return j.groupby("index_right").size().reindex(parcels.index, fill_value=0)


def summary(b: gpd.GeoDataFrame) -> dict:
    return {"buildings": int(len(b)), "gis": int((b["source"] == "gis").sum()), "road_only": int((b["source"] == "road").sum()),
            "linked": int((b["reg"] == LINKED).sum()), "unlinked": int((b["reg"] == UNLINKED).sum()),
            "register_matched": int((b["reg_pk"] != "").sum()), "violt": int(b["violt"].sum())}
