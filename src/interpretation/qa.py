"""판독 결과 검수 표본: 층별 무작위 추출, 근거 영상 조각, 라벨 집계.

건물 레이어 정답은 레이어에 없는 창고·가설물을 '구조물 아님'으로 세므로, 미등재 구조물과
신규 구조물의 정밀도는 사람이 원본 영상을 보고 판독한 표본으로 따로 잰다.

  층(stratum)     추출 대상
  new_A           신규 A: 앙상블 변화확률도 높고(두 방법 일치) 건물 레이어와 겹치지 않는 객체
  new_C           신규 C: 판독만 잡은 객체
  unregistered    2022 구조물 확률 ≥ prob_hi, 건물 레이어에서 떨어진 20㎡ 이상 조각

라벨: structure(구조물 맞음) / not_structure(아님) / uncertain(판단 불가).
신규 층에서 structure는 '2022에 없던 구조물이 2024에 실제로 있음'을 뜻한다. 구조물이지만
2022에도 있었으면(분할 차이로 잡힌 것) not_structure로 두고 note에 적는다.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import shapes
from rasterio.windows import from_bounds
from shapely.geometry import shape

from .evidence import chip_pair

LABELS = {"structure": "구조물 맞음", "not_structure": "구조물 아님", "uncertain": "판단 불가"}


def _segment_geom(seg_path: Path, seg_id: int, x: float, y: float, half: float = 60.0):
    with rasterio.open(seg_path) as ds:
        win = from_bounds(x - half, y - half, x + half, y + half, ds.transform).round_offsets().round_lengths()
        a = ds.read(1, window=win, boundless=True, fill_value=0)
        tr = ds.window_transform(win)
    geoms = [shape(g) for g, v in shapes((a == seg_id).astype(np.uint8), mask=a == seg_id, transform=tr)]
    return gpd.GeoSeries(geoms).union_all() if geoms else None


def _not_near_previous(xs: np.ndarray, ys: np.ndarray, qa_root: Path, round_tag: str, dist: float = 5.0) -> np.ndarray:
    """이전 회차 표본에서 dist(m) 이내인 후보를 빼는 마스크. 규칙을 고친 표본으로 다시 평가하지 않기 위함."""
    prev = [pd.read_csv(p, encoding="utf-8-sig")[["x", "y"]] for p in qa_root.glob("*/qa_labels.csv")
            if p.parent.name != round_tag]
    if not prev:
        return np.ones(len(xs), bool)
    p = pd.concat(prev).to_numpy()
    d = np.hypot(xs[:, None] - p[None, :, 0], ys[:, None] - p[None, :, 1]).min(axis=1)
    return d > dist


def build_sample(out_dir: Path, images: dict, dates: dict, objects: gpd.GeoDataFrame, seg_prob: pd.DataFrame,
                 seg_dir: Path, t1: str, prob_hi: float, round_tag: str, n: int = 30, seed: int = 7) -> Path:
    """층별로 n개씩 뽑아 qa/<round_tag>/에 근거 영상과 빈 라벨 표(qa_labels.csv)를 만든다.

    이전 회차 표본 근처의 후보는 뽑지 않는다. 라벨이 하나라도 적힌 회차는 건드리지 않는다.
    """
    rng = np.random.default_rng(seed)
    qa = out_dir / "qa" / round_tag
    path = qa / "qa_labels.csv"
    if path.exists() and pd.read_csv(path, encoding="utf-8-sig")["label"].notna().any():
        return path  # 판독이 끝난 회차는 다시 뽑지 않는다(객체가 바뀌면 라벨이 어긋남)
    rows = []
    objects = objects[_not_near_previous(objects.geometry.centroid.x.to_numpy(), objects.geometry.centroid.y.to_numpy(),
                                         out_dir / "qa", round_tag)]
    new = objects[objects["kind"] == "new"]
    for stratum, sub in [("new_A", new[new["tier"] == "A"]), ("new_C", new[new["tier"] == "C"])]:
        pick = sub.iloc[rng.choice(len(sub), min(n, len(sub)), replace=False)] if len(sub) else sub
        for i, (_, o) in enumerate(pick.iterrows()):
            sid = f"{stratum}_{i:03d}"
            chip_pair(images, dates, o.geometry, [o.geometry], qa / f"{sid}.jpg", title=f"{sid} {o['area_m2']:.0f}m2")
            rows.append({"sample_id": sid, "stratum": stratum, "area_m2": o["area_m2"], "pnu": o.get("pnu"),
                         "x": o.geometry.centroid.x, "y": o.geometry.centroid.y})
    s = seg_prob[(seg_prob["epoch"] == t1) & (seg_prob["prob"] >= prob_hi) & (seg_prob["fp_near"] == 0)
                 & (seg_prob["area_m2"] >= 20)]
    s = s[_not_near_previous(s["x"].to_numpy(), s["y"].to_numpy(), out_dir / "qa", round_tag)]
    pick = s.iloc[rng.choice(len(s), min(n, len(s)), replace=False)]
    for i, (_, r) in enumerate(pick.iterrows()):
        sid = f"unregistered_{i:03d}"
        g = _segment_geom(seg_dir / f"segments_{t1}.tif", int(r["seg_id"]), r["x"], r["y"])
        if g is None:
            continue
        chip_pair({t1: images[t1]}, dates, g, [g], qa / f"{sid}.jpg", half_m=20, title=f"{sid} {r['area_m2']:.0f}m2 p={r['prob']:.2f}")
        rows.append({"sample_id": sid, "stratum": "unregistered", "area_m2": r["area_m2"], "pnu": None, "x": r["x"], "y": r["y"]})
    table = pd.DataFrame(rows)
    table["label"], table["note"] = "", ""
    table.to_csv(path, index=False, encoding="utf-8-sig")
    return path


JIMOK_GROUPS = {"대": "대", "전": "전", "답": "답", "목": "목", "임": "임"}  # 나머지는 '기타'


def _jimok_group(j: pd.Series) -> pd.Series:
    return j.map(JIMOK_GROUPS).fillna("기타")


def build_parcel_round(out_dir: Path, images: dict, dates: dict, table: gpd.GeoDataFrame, objects: gpd.GeoDataFrame,
                       t1: str, round_tag: str, per_group: int = 8, per_linear: int = 20, seed: int = 11,
                       parts: tuple[str, ...] = ("unreg", "linear", "seen", "gone", "new"), per_attached: int = 20) -> Path:
    """필지 단위 검수 회차(qa/<round_tag>/). 라벨이 하나라도 있으면 다시 만들지 않는다.

    층과 라벨 뜻(parts로 고른다)
      unreg_<지목군>   미등재 '독립' 플래그 필지(선형 지목 제외), 지목군(대·전·답·목·임·기타)별 per_group.
                      round3: structure = 노란 객체 중 하나 이상이 실제 구조물(건물·하우스·가설물·컨테이너).
                      round4부터: structure = 노란 객체 중 하나 이상이 등재 건물의 일부가 아닌 구조물(엄격).
      unreg_attached   등재 건물과 이어진 객체만 있는 '부속 의심' 필지(attached). 라벨 뜻은 위와 같다(엄격).
      unreg_linear     미등재 플래그 필지 중 도로·구거·하천·제방·철도 지목.
      seen_linear      '있음(판독만)' 선형 지목 필지. structure = 필지 안에 실제 구조물이 있음.
      gone_AB          소멸 의심 A·B 객체 전수. structure = 2022에 있던 구조물이 2024에 실제로 없어짐.
      new_A            신규 의심 A 객체 중 이전 회차 표본 5m 밖. structure = 2022에 없던 구조물이 2024에 있음.
    모집단 크기(population)는 이전 회차 표본을 빼기 전 크기다(층화 가중용). 표본은 이전 회차 표본 5m 밖에서 뽑는다.
    """
    rng = np.random.default_rng(seed)
    qa = out_dir / "qa" / round_tag
    path = qa / "qa_labels.csv"
    if path.exists() and pd.read_csv(path, encoding="utf-8-sig")["label"].notna().any():
        return path
    t = table.reset_index(drop=True)
    unreg = objects[objects["kind"] == "unregistered"]
    flagged = t[t["unregistered_flag"]].copy()
    conf = flagged.get("unregistered_conf", pd.Series("독립", index=flagged.index))
    # v2.0: '대장 없음'(도면 있음·대장 없음)은 독립(도면·대장 없음)과 정밀도를 따로 재도록 별도 층
    flagged["group"] = np.select([flagged["linear_jimok"], conf == "부속 의심", conf == "대장 없음"],
                                 ["linear", "attached", "noreg"], _jimok_group(flagged["jimok"]))
    strata = []
    if "unreg" in parts:
        strata += [(f"unreg_{g}", flagged[flagged["group"] == g], per_group) for g in [*JIMOK_GROUPS.values(), "기타"]]
        if (flagged["group"] == "attached").any():
            strata.append(("unreg_attached", flagged[flagged["group"] == "attached"], per_attached))
        if (flagged["group"] == "noreg").any():
            strata.append(("unreg_noreg", flagged[flagged["group"] == "noreg"], per_attached))
    if "linear" in parts:
        strata.append(("unreg_linear", flagged[flagged["group"] == "linear"], per_linear))
    if "seen" in parts:
        strata.append(("seen_linear", t[(t[f"status_{t1}"] == "있음(판독만)") & t["linear_jimok"]], per_linear))
    rows = []
    for name, pop, n in strata:
        rp = pop.geometry.representative_point()
        fresh = pop[_not_near_previous(rp.x.to_numpy(), rp.y.to_numpy(), out_dir / "qa", round_tag)] if len(pop) else pop
        pick = fresh.iloc[rng.choice(len(fresh), min(n, len(fresh)), replace=False)] if len(fresh) else fresh
        for i, (idx, r) in enumerate(pick.iterrows()):
            sid = f"{name}_{i:03d}"
            obj = unreg[unreg["parcel_idx"] == idx].geometry.tolist() if name.startswith("unreg") else []
            chip_pair({t1: images[t1]}, dates, r.geometry, obj, qa / f"{sid}.jpg", half_m=25,
                      title=f"{sid} {r['jimok']} {r['addr']}", parcel=r.geometry)
            c = r.geometry.representative_point()
            rows.append({"sample_id": sid, "stratum": name, "population": len(pop), "pnu": r["pnu"], "x": c.x, "y": c.y})
    for kind, tiers, name in [("gone", ["A", "B"], "gone_AB"), ("new", ["A"], "new_A")]:
        if kind not in parts:
            continue
        o = objects[(objects["kind"] == kind) & objects["tier"].isin(tiers)]
        o = o[_not_near_previous(o.geometry.centroid.x.to_numpy(), o.geometry.centroid.y.to_numpy(), out_dir / "qa", round_tag)]
        for i, (_, r) in enumerate(o.iterrows()):
            sid = f"{name}_{i:03d}"
            chip_pair(images, dates, r.geometry, [r.geometry], qa / f"{sid}.jpg", title=f"{sid} {r['area_m2']:.0f}m2")
            c = r.geometry.centroid
            rows.append({"sample_id": sid, "stratum": name, "population": len(o), "pnu": r.get("pnu"), "x": c.x, "y": c.y})
    out = pd.DataFrame(rows)
    out["label"], out["note"] = "", ""
    qa.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, encoding="utf-8-sig")
    return path


EDGE_TAG = "[등재 건물 일부 추정]"  # 실제 구조물이지만 건물 레이어 외곽선보다 큰 지붕의 가장자리로 보이는 표본


def stratified_precision(labels: Path | pd.DataFrame, prefix: str, exclude: tuple[str, ...] = (),
                         strict: bool = False) -> dict:
    """prefix로 시작하는 층들의 층화 가중 정밀도(층 정밀도를 모집단 크기로 가중). 판단 불가 제외.

    strict=True면 note가 EDGE_TAG로 시작하는 '구조물 맞음'을 아님으로 센다(건물 레이어에 아예 없는 구조물만).
    """
    t = labels.copy() if isinstance(labels, pd.DataFrame) else pd.read_csv(labels, encoding="utf-8-sig")
    if strict:
        t.loc[t["note"].fillna("").str.startswith(EDGE_TAG) & (t["label"] == "structure"), "label"] = "not_structure"
    t = t[t["stratum"].str.startswith(prefix) & ~t["stratum"].isin(exclude) & t["label"].isin(LABELS)]
    rows = []
    for s, g in t.groupby("stratum"):
        k = int((g["label"] == "structure").sum())
        m = int(g["label"].isin(["structure", "not_structure"]).sum())
        if m:
            rows.append((s, int(g["population"].iloc[0]), k, m))
    if not rows:
        return {}
    pop = sum(r[1] for r in rows)
    p = sum(r[1] / pop * r[2] / r[3] for r in rows)
    # 층화 비율의 분산: Σ W_h² p_h(1-p_h)/n_h (유한모집단 보정 생략)
    var = sum((r[1] / pop) ** 2 * (r[2] / r[3]) * (1 - r[2] / r[3]) / r[3] for r in rows)
    return {"precision": p, "ci_lo": max(0.0, p - 1.96 * var ** 0.5), "ci_hi": min(1.0, p + 1.96 * var ** 0.5),
            "population": pop, "labeled": sum(r[3] for r in rows),
            "by_stratum": [{"stratum": s, "population": n, "structure": k, "decided": m} for s, n, k, m in rows]}


def summarize(path: Path) -> pd.DataFrame:
    """층별 라벨 분포와 정밀도(판단 불가 제외, Wilson 95% 구간)."""
    t = pd.read_csv(path, encoding="utf-8-sig")
    t = t[t["label"].isin(LABELS)]
    rows = []
    for stratum, g in t.groupby("stratum", sort=False):
        k = int((g["label"] == "structure").sum())
        m = int(g["label"].isin(["structure", "not_structure"]).sum())
        lo, hi = wilson(k, m)
        rows.append({"stratum": stratum, "n": len(g), **{v: int((g["label"] == k_).sum()) for k_, v in LABELS.items()},
                     "precision": k / m if m else np.nan, "ci_lo": lo, "ci_hi": hi})
    return pd.DataFrame(rows)


HUMAN_COLUMNS = ["round", "sample_id", "stratum", "image", "ai_label", "ai_note", "human_label", "human_note", "judge"]


def export_for_human(qa_root: Path, out_path: Path | None = None) -> Path:
    """모든 회차의 표본을 사람이 다시 판독할 표로 모은다(human_review.csv).

    ai_label은 이전 판독(AI)의 결과이고, 사람은 영상(image)을 보고 human_label에 structure /
    not_structure / uncertain 중 하나, judge에 판독자 이름을 적는다. 다시 만들어도 사람 입력은 보존한다.
    """
    out_path = out_path or qa_root / "human_review.csv"
    rows = []
    for p in sorted(qa_root.glob("*/qa_labels.csv")):
        t = pd.read_csv(p, encoding="utf-8-sig")
        rows.append(pd.DataFrame({"round": p.parent.name, "sample_id": t["sample_id"], "stratum": t["stratum"],
                                  "image": [f"{p.parent.name}/{s}.jpg" for s in t["sample_id"]],
                                  "ai_label": t["label"], "ai_note": t["note"]}))
    table = pd.concat(rows, ignore_index=True)
    if out_path.exists():
        old = pd.read_csv(out_path, encoding="utf-8-sig")[["round", "sample_id", "human_label", "human_note", "judge"]]
        table = table.merge(old, on=["round", "sample_id"], how="left")
    for c in ["human_label", "human_note", "judge"]:
        if c not in table:
            table[c] = ""
    table[HUMAN_COLUMNS].to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def cohen_kappa(a: pd.Series, b: pd.Series) -> float:
    """두 판독자의 범주 일치도(Cohen κ). 둘 다 값이 있는 행만 쓴다."""
    m = a.notna() & b.notna()
    a, b = a[m].astype(str), b[m].astype(str)
    if not len(a):
        return np.nan
    po = float((a == b).mean())
    cats = sorted(set(a) | set(b))
    pe = sum(float((a == c).mean()) * float((b == c).mean()) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def agreement(path: Path) -> dict:
    """사람 판독이 들어온 행으로 AI-사람 일치도와 사람 기준 층별 정밀도를 낸다."""
    t = pd.read_csv(path, encoding="utf-8-sig")
    t = t[t["human_label"].isin(LABELS)]
    by = []
    for (rnd, stratum), g in t.groupby(["round", "stratum"], sort=False):
        k = int((g["human_label"] == "structure").sum())
        m = int(g["human_label"].isin(["structure", "not_structure"]).sum())
        lo, hi = wilson(k, m)
        by.append({"round": rnd, "stratum": stratum, "n": len(g), "human_precision": k / m if m else np.nan,
                   "ci_lo": lo, "ci_hi": hi, "kappa": cohen_kappa(g["ai_label"], g["human_label"])})
    return {"reviewed": int(len(t)), "kappa": cohen_kappa(t["ai_label"], t["human_label"]),
            "judges": sorted(t["judge"].dropna().astype(str).unique().tolist()), "by_stratum": by}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return np.nan, np.nan
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, c - h), min(1.0, c + h)
