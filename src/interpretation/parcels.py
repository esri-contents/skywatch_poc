"""조각 확률을 화소로 펼쳐 필지별 구조물 현황과 기준일 전후 변화를 판정한다.

판정 단위
- 화소: 조각의 구조물 확률을 그 조각의 모든 화소에 칠한다(0.5m).
- 객체: 2024에 확률이 높고(≥ prob_hi) 2022에 낮은(< prob_lo) 화소의 연결 성분 = 신규 구조물 후보,
  반대 방향 = 소멸 후보. 두 기준 사이(애매한 화소)는 어느 쪽으로도 세지 않는다(이력 판정).
  정합 오차(RMSE 0.42m)만큼 구조물 가장자리가 신규로 잡히지 않도록 상대 시기 마스크를
  tolerance_m만큼 넓혀 뺀다.
- 필지: 화소·객체를 지적 필지로 집계한다.

기존 변화탐지(앙상블 변화확률, 1m)는 독립된 두 번째 증거로 쓴다. 객체 안 평균 변화확률이
corroborate_min 이상이면 '두 방법 일치'로 표시한다.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from scipy import ndimage
from shapely.geometry import shape


# 도로·구거·하천·제방·철도 등 선형 지목. 미등재·판독만 판정에서 아스팔트·포장면 오탐이 몰리는
# 것으로 보여(검증 시 미등재 75필지, 판독만 101필지) 별도 층으로 검수한다 - 규칙으로 지우지 않는다.
LINEAR_JIMOK = ["도", "도로", "구", "천", "제", "철"]


def paint(seg_path, seg_ids: np.ndarray, prob: np.ndarray) -> tuple[np.ndarray, dict]:
    """조각 라벨 래스터에 조각 확률을 칠한 float32 배열(영상 없음 = nan)."""
    with rasterio.open(seg_path) as ds:
        labels = ds.read(1)
        profile = ds.profile
    order = np.argsort(seg_ids)
    ids, p = seg_ids[order], prob[order]
    flat = labels.ravel()
    out = np.full(flat.shape, np.nan, np.float32)
    m = flat > 0
    pos = np.clip(np.searchsorted(ids, flat[m]), 0, len(ids) - 1)
    found = ids[pos] == flat[m]
    vals = np.full(pos.shape, np.nan, np.float32)
    vals[found] = p[pos[found]]
    out[m] = vals
    return out.reshape(labels.shape), profile


def write_prob(path, prob: np.ndarray, profile: dict) -> None:
    p = {**profile, "dtype": "uint8", "nodata": 255}
    with rasterio.open(path, "w", **p) as ds:
        ds.write(np.where(np.isnan(prob), 255, np.round(prob * 100)).astype(np.uint8), 1)


def _resample_to(path, profile: dict) -> np.ndarray:
    """다른 격자(예: 1m 변화확률)를 판독 격자로 읽는다."""
    from rasterio.vrt import WarpedVRT
    with rasterio.open(path) as src, WarpedVRT(src, crs=profile["crs"], transform=profile["transform"],
                                               width=profile["width"], height=profile["height"],
                                               resampling=Resampling.nearest) as vrt:
        a = vrt.read(1).astype(np.float32)
        if src.nodata is not None:
            a[a == src.nodata] = np.nan
    return a


def change_objects(p_from: np.ndarray, p_to: np.ndarray, cfg: dict, res: float, transform,
                   crs, change_prob: np.ndarray | None, kind: str,
                   shadow: np.ndarray | None = None) -> gpd.GeoDataFrame:
    """p_from에서 낮고 p_to에서 높은 화소의 연결 성분(신규 또는 소멸 구조물 후보).

    shadow(어느 한 시기라도 그림자인 화소)는 판정에서 뺀다. 1차 육안 검수에서 신규 A 오탐
    23건 중 9건이 촬영 시각 차이로 그림자 위치가 바뀐 곳이었다. 남은 성분은 면적이 최소
    회전 사각형의 min_rect_fill 이상인 것만 둔다(실제 신규 구조물은 사각형, 그림자·포장
    오탐은 불규칙한 모양).
    """
    hi, lo = cfg["prob_hi"], cfg["prob_lo"]
    tol = max(1, int(round(cfg["tolerance_m"] / res)))
    was = ndimage.binary_dilation(np.nan_to_num(p_from, nan=1.0) >= lo, iterations=tol)
    now = np.nan_to_num(p_to, nan=0.0) >= hi
    mask = now & ~was
    if shadow is not None:
        mask &= ~shadow
    gdf, lab = _components(mask, cfg, res, transform, crs, kind)
    if not len(gdf):
        return gdf
    idx = gdf["obj"].to_numpy()
    gdf["prob_to"] = ndimage.mean(np.nan_to_num(p_to), lab, idx).round(3)
    gdf["prob_from"] = ndimage.mean(np.nan_to_num(p_from), lab, idx).round(3)
    if change_prob is not None:
        gdf["change_prob"] = ndimage.mean(np.nan_to_num(change_prob), lab, idx).round(3)
        gdf["corroborated"] = gdf["change_prob"] >= cfg["corroborate_min"]
    return gdf.drop(columns="obj").reset_index(drop=True)


def _components(mask: np.ndarray, cfg: dict, res: float, transform, crs, kind: str
                ) -> tuple[gpd.GeoDataFrame, np.ndarray]:
    """마스크의 연결 성분 중 min_object_m2 이상이고 사각 채움률이 min_rect_fill 이상인 것을 폴리곤으로.

    1px 띠·점은 3x3 열림으로 지운다(사각형 모서리는 유지). 반환하는 라벨 배열은 남은 성분만 담는다.
    """
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3), bool))
    lab, n = ndimage.label(mask)
    empty = gpd.GeoDataFrame(columns=["obj", "kind", "area_m2", "rect_fill", "geometry"], geometry="geometry", crs=crs)
    if n == 0:
        return empty, lab
    area = ndimage.sum(np.ones_like(lab, np.float32), lab, np.arange(1, n + 1)) * res * res
    keep = np.flatnonzero(area >= cfg["min_object_m2"]) + 1
    lab = np.where(np.isin(lab, keep), lab, 0)
    rows = [{"obj": int(v), "geometry": shape(g)} for g, v in shapes(lab.astype(np.int32), mask=lab > 0, transform=transform)]
    if not rows:
        return empty, lab
    gdf = gpd.GeoDataFrame(rows, crs=crs).dissolve("obj").reset_index()
    gdf["kind"] = kind
    gdf["area_m2"] = (ndimage.sum(np.ones_like(lab, np.float32), lab, gdf["obj"].to_numpy()) * res * res).round(1)
    gdf["rect_fill"] = (gdf.geometry.area / gdf.geometry.minimum_rotated_rectangle().area).round(3)
    gdf = gdf[gdf["rect_fill"] >= cfg.get("min_rect_fill", 0.0)]
    lab = np.where(np.isin(lab, gdf["obj"].to_numpy()), lab, 0)
    return gdf.reset_index(drop=True), lab


def footprint_masks(buildings: gpd.GeoDataFrame, profile: dict, tolerance_m: float) -> tuple[np.ndarray, np.ndarray]:
    """건물 레이어 footprint(버퍼 없음)와 tolerance_m 버퍼 마스크.

    등재 판정은 footprint 그대로 쓴다 - 버퍼를 쓰면 이웃 필지 건물의 띠만으로 '등재'가 된다(571필지).
    버퍼는 미등재 판정에서 건물 가장자리·정합 오차를 빼는 데만 쓴다.
    """
    shp = (profile["height"], profile["width"])
    if not len(buildings):  # 예: 위반건축물이 없는 지구
        return np.zeros(shp, bool), np.zeros(shp, bool)
    fp = rasterize(((g, 1) for g in buildings.geometry), out_shape=shp, transform=profile["transform"],
                   fill=0, dtype="uint8").astype(bool)
    near = rasterize(((g, 1) for g in buildings.geometry.buffer(tolerance_m)), out_shape=shp,
                     transform=profile["transform"], fill=0, dtype="uint8").astype(bool)
    return fp, near


def unregistered_objects(p_t1: np.ndarray, near: np.ndarray, cfg: dict, res: float, transform, crs,
                         fp: np.ndarray | None = None, unreg_map: np.ndarray | None = None,
                         violt_map: np.ndarray | None = None) -> gpd.GeoDataFrame:
    """등재 건물(대장 연계 윤곽) 버퍼 밖의 구조물 화소 연결 성분(미등재 구조물 객체).

    v2.0: near·fp는 대장 연계 윤곽만이다. unreg_map(대장 미연계 윤곽 버퍼)과 겹치는 비율을 map_frac으로,
    violt_map(위반건축물 윤곽)과 겹치는 비율을 violt_frac으로 남긴다 - parcel_table이 '대장 없음(R)'을 나눈다.

    신규 객체와 같은 크기·채움률 조건을 쓴다. 이전에는 필지 안 미등재 화소 '합계'가 20㎡ 이상이면
    플래그했는데, 검수는 20㎡ 이상 단일 조각에서 뽑아 두 모집단이 달랐다(1,028필지 중 485곳만 겹침).

    attached: 버퍼를 빼기 전 구조물 화소의 연결 성분이 건물 footprint에 닿는 객체. round3 필지 검수에서
    실제 구조물로 판독된 미등재 객체의 상당수가 footprint보다 큰 등재 건물 지붕의 가장자리·부속부였다
    (엄격 정밀도 55%). 같은 지붕 덩어리로 이어진 객체를 '등재 건물 부속 의심'으로 나눈다.
    """
    struct = np.nan_to_num(p_t1) >= cfg["prob_hi"]
    gdf, lab = _components(struct & ~near, cfg, res, transform, crs, "unregistered")
    if not len(gdf):
        return gdf.drop(columns="obj").assign(attached=pd.Series(dtype=bool))
    idx = gdf["obj"].to_numpy()
    gdf["prob_to"] = ndimage.mean(np.nan_to_num(p_t1), lab, idx).round(3)
    gdf["map_frac"] = ndimage.mean(unreg_map.astype(np.float32), lab, idx).round(3) if unreg_map is not None else 0.0
    gdf["violt_frac"] = ndimage.mean(violt_map.astype(np.float32), lab, idx).round(3) if violt_map is not None else 0.0
    if fp is not None:
        whole, _ = ndimage.label(struct | fp, structure=np.ones((3, 3), bool))
        touching = np.unique(whole[fp & (whole > 0)])
        comp_of_obj = ndimage.maximum(whole, lab, idx)  # 객체는 한 성분 안에 있으므로 최대값 = 그 성분 번호
        gdf["attached"] = np.isin(comp_of_obj, touching)
    else:
        gdf["attached"] = False
    return gdf.drop(columns="obj").reset_index(drop=True)


def footprint_recall(buildings: gpd.GeoDataFrame, prob: np.ndarray, profile: dict, prob_hi: float,
                     covered_min: float = 0.5) -> dict:
    """건물 레이어의 건물 중 footprint의 covered_min 이상이 구조물로 판독된 비율(건물 단위 재현율)."""
    b = buildings[buildings.area >= 20].reset_index(drop=True)
    shp = (profile["height"], profile["width"])
    bid = rasterize(((g, i + 1) for i, g in enumerate(b.geometry)), out_shape=shp,
                    transform=profile["transform"], fill=0, dtype="int32")
    n = len(b) + 1
    tot = np.bincount(bid.ravel(), minlength=n)[1:]
    hit = np.bincount(bid.ravel(), weights=(np.nan_to_num(prob) >= prob_hi).ravel(), minlength=n)[1:]
    ok = tot > 0
    frac = hit[ok] / tot[ok]
    area = b.area.to_numpy()[ok]
    bins = {"20-50㎡": (20, 50), "50-100㎡": (50, 100), "100-300㎡": (100, 300), "300㎡+": (300, np.inf)}
    return {"buildings": int(ok.sum()), "recall": float((frac >= covered_min).mean()),
            "by_size": {k: float((frac[(area >= lo) & (area < hi)] >= covered_min).mean()) for k, (lo, hi) in bins.items()}}


def drop_aggregate_parcels(cad: gpd.GeoDataFrame, covered_min: float = 0.9) -> gpd.GeoDataFrame:
    """더 작은 필지들로 covered_min 이상 덮이는 상위 폴리곤(예: 본번 레이어)을 뺀다.

    VWorld 본번(lp_pa_cbnd_bonbun)과 부번(lp_pa_cbnd_bubun)을 합친 지적도는 같은 땅을 두 번
    담는다(광명시흥: 부번 16,637필지가 모두 본번 4,811개 안에 있고 부번만으로 지구의 99.9%를
    덮음). 그대로 래스터화하면 나중에 그린 상위 폴리곤이 부번 필지를 덮어 화소가 0이 된다.
    레이어 이름이 아니라 포함 관계로 판단하므로 본번만 있는 지역의 필지는 남는다.
    """
    cad = cad.reset_index(drop=True)
    area = cad.area
    # 부번이 하나뿐인 본번은 본번·부번 폴리곤이 같은 모양이다. 면적이 같으면(0.1% 이내) PNU가 있는
    # 쪽, 그다음 앞 번호를 남긴다 - 상대가 '우선'이면 그 상대가 나를 덮는 것으로 센다.
    rank = cad["pnu"].notna().astype(int).to_numpy() if "pnu" in cad else np.zeros(len(cad), int)
    j = gpd.sjoin(cad[["geometry"]], cad[["geometry"]], predicate="intersects")
    me, other = j.index.to_numpy(), j["index_right"].to_numpy()
    a_me, a_ot = area.to_numpy()[me], area.to_numpy()[other]
    tie = np.abs(a_ot - a_me) <= 1e-3 * a_me
    preferred = (rank[other] > rank[me]) | ((rank[other] == rank[me]) & (other < me))
    j = j[(me != other) & ((a_ot < a_me) & ~tie | tie & preferred)]
    inter = [cad.geometry.iloc[a].intersection(cad.geometry.iloc[b]).area for a, b in zip(j.index, j["index_right"])]
    covered = pd.Series(inter, index=j.index).groupby(level=0).sum().reindex(cad.index, fill_value=0)
    return cad[(covered / area.clip(lower=1e-9)) < covered_min].reset_index(drop=True)


def parcel_table(cadastre: gpd.GeoDataFrame, probs: dict[str, np.ndarray], profile: dict, res: float,
                 buildings: gpd.GeoDataFrame, objects: gpd.GeoDataFrame, cfg: dict,
                 t1: str, t2: str, masks: tuple[np.ndarray, np.ndarray] | None = None,
                 map_fp: np.ndarray | None = None) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """필지별 구조물 면적(시기별), 미등재 객체, 신규·소멸 객체를 집계하고 판정한다.

    buildings: 기준 건물 레이어 전체(신규·소멸이 건물 지붕 위인지 - B등급 - 판단). masks: 등재(대장 연계) 윤곽의
    (footprint, 버퍼) - 등재 면적·상태에 쓴다. map_fp: 윤곽 전체(대장 여부 무관) - 도면 면적(mapped_m2).
    objects에는 신규(new)·소멸(gone)·미등재(unregistered) 객체가 함께 들어온다. 반환: (필지 표, 필지가 붙은 객체).
    미등재 객체 등급: R = 대장 미연계 윤곽 위(map_frac ≥ 0.5, 도면 있음·대장 없음), UA = 등재 지붕에 붙음(부속 의심),
    U = 어느 윤곽에도 없음(도면·대장 모두 없음).
    """
    shp = (profile["height"], profile["width"])
    fp, near = masks if masks is not None else footprint_masks(buildings, profile, cfg["tolerance_m"])
    cad = cadastre.reset_index(drop=True)
    pid = rasterize(((g, i + 1) for i, g in enumerate(cad.geometry)), out_shape=shp,
                    transform=profile["transform"], fill=0, dtype="int32")
    n = len(cad) + 1
    px = res * res

    def area(mask):
        return np.bincount(pid[mask], minlength=n)[1:] * px

    hi = cfg["prob_hi"]
    imaged = ~np.isnan(probs[t1])
    out = cad[["pnu", "addr", "jibun", "geometry"]].copy()
    out["parcel_m2"] = cad.area.round(1)
    out["imaged_ratio"] = (area(imaged) / np.maximum(area(pid > 0), px)).round(3)
    for e, p in probs.items():
        s = np.nan_to_num(p) >= hi
        out[f"struct_{e}_m2"] = area(s).round(1)
        out[f"unregistered_{e}_m2"] = area(s & ~near).round(1)
    out["registered_m2"] = area(fp & (pid > 0)).round(1)
    out["mapped_m2"] = area(map_fp & (pid > 0)).round(1) if map_fp is not None else out["registered_m2"]
    out["jimok"] = cad["jibun"].str.extract(r"([가-힣]+)$")[0]
    out["linear_jimok"] = out["jimok"].isin(LINEAR_JIMOK)

    obj = objects.copy()
    if len(obj):
        bu = buildings.geometry.union_all()
        obj["fp_overlap"] = (obj.geometry.intersection(bu).area / obj.geometry.area).round(3)
        corr = obj["corroborated"].fillna(False).astype(bool) if "corroborated" in obj else pd.Series(False, index=obj.index)
        attached = obj["attached"].fillna(False).astype(bool) if "attached" in obj else pd.Series(False, index=obj.index)
        unreg = obj["kind"] == "unregistered"
        mapped = obj["map_frac"].fillna(0).to_numpy() >= 0.5 if "map_frac" in obj else np.zeros(len(obj), bool)
        obj["tier"] = np.select(
            [unreg & mapped, unreg & attached, unreg, corr & (obj["fp_overlap"] < 0.5), corr],
            ["R", "UA", "U", "A", "B"], "C")
        rp = obj.geometry.representative_point()
        j = gpd.sjoin(gpd.GeoDataFrame(geometry=rp, crs=obj.crs), out[["geometry"]], predicate="within")
        j = j[~j.index.duplicated()]  # 지적도에 서로 겹치는 필지가 있어 대표점이 둘에 들 수 있음
        obj["parcel_idx"] = j["index_right"].reindex(obj.index)
        obj["pnu"] = out["pnu"].reindex(obj["parcel_idx"]).to_numpy()
    else:
        obj = obj.assign(kind=pd.Series(dtype=str), tier=pd.Series(dtype=str), parcel_idx=pd.Series(dtype=float))
    for kind in ("new", "gone", "unregistered"):
        k = obj[obj["kind"] == kind]
        out[f"{kind}_n"] = k.groupby("parcel_idx").size().reindex(out.index, fill_value=0)
        out[f"{kind}_m2"] = k.groupby("parcel_idx")["area_m2"].sum().reindex(out.index, fill_value=0).round(1)
        if kind == "unregistered":
            out["unregistered_standalone_n"] = k[k["tier"] == "U"].groupby("parcel_idx").size().reindex(out.index, fill_value=0)
            out["unregistered_noreg_n"] = k[k["tier"] == "R"].groupby("parcel_idx").size().reindex(out.index, fill_value=0)
        else:
            for tier in "ABC":
                out[f"{kind}_{tier}_n"] = k[k["tier"] == tier].groupby("parcel_idx").size().reindex(out.index, fill_value=0)

    m = cfg["min_struct_m2"]
    seen, reg = out[f"struct_{t1}_m2"] >= m, out["registered_m2"] >= m
    out[f"status_{t1}"] = np.select(
        [out["imaged_ratio"] < 0.5, seen & reg, reg, seen], ["영상 부족", "있음(등재+판독)", "있음(등재만)", "있음(판독만)"], "없음")
    out["unregistered_flag"] = out["unregistered_n"] > 0
    # round3 필지 검수에서 선형 지목 플래그는 20필지 중 실제 구조물 2곳(아스팔트·차량 오탐)이라 저신뢰로 둔다.
    # 독립 객체 없이 등재 건물과 이어진 객체(UA)만 있는 필지는 '부속 의심'으로 나눈다(round4로 평가).
    # v2.0: '독립' = 도면·대장 모두 없는 구조물(U)이 있음, '대장 없음' = 대장 미연계 윤곽 위 구조물(R)만 있음
    out["unregistered_conf"] = np.select(
        [~out["unregistered_flag"], out["linear_jimok"], out["unregistered_standalone_n"] > 0, out["unregistered_noreg_n"] > 0],
        ["", "저신뢰", "독립", "대장 없음"], "부속 의심")
    # 판독만 잡은 신규(C)는 2차 검수 30건이 모두 2022에도 있던 구조물이라(정밀도 0%) 변화로 세지
    # 않는다. 객체와 new_C_n 열에는 남겨 둔다.
    out["change"] = np.select(
        [out["new_A_n"] > 0, out["new_B_n"] > 0, out["gone_A_n"] + out["gone_B_n"] > 0],
        ["신규 의심 A(육안 확인)", "신규 B(등재 건물 위 - 지붕 교체·재건축 가능)", "소멸 의심(미검증)"],
        "변화 없음")
    return out, obj
