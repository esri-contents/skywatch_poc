"""후보 판독 - 모델이 낸 변화·미등재 후보를 사람이 전수 육안 판독해 현장조사 대상을 거른다.

모델 결과는 '후보'다. 무작위 표본 검수에서 변화 후보의 50~55%만 실제 건물·구조 변화였고, 오탐(그림자 위치
변화, 경사지붕 밝기 차이, 나지→포장, 차량·적치물, 비닐하우스·컨테이너 색 변화)은 영상 특성이 실제 변화와
비슷해 임계값으로는 다 걸러지지 않는다. 그래서 현장조사 전에 후보를 한 번 육안으로 판독한다(필지당 수 초,
660필지 기준 1인 반나절 이내).

대상과 번호는 현장조사(survey.targets)와 같다: U001…(붙임 1), R001…(붙임 1-2), A01…(신규 A), G01…(소멸 A·B).
<out>/interpretation/screen/
  screen_labels.csv   검수표와 같은 형식(sample_id, stratum, pnu, x, y, label, note, judge, reason) - Add-in 검수 창이 읽고 쓴다
  <SID>.jpg           두 시기 근거 영상(주변 맥락이 보이게 넓게)
label: structure = 현장조사 대상(실제 구조물·변화), not_structure = 오탐(현장조사 제외), uncertain = 판단 보류(현장 확인)
현장조사 내보내기는 not_structure만 뺀다(판독하지 않은 후보와 보류는 그대로 보낸다).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from .evidence import chip_pair

COLUMNS = ["sample_id", "stratum", "kind", "pnu", "addr", "jimok", "area_m2", "x", "y", "label", "note", "judge", "reason"]
LABELS = {"structure": "현장조사 대상", "not_structure": "오탐(제외)", "uncertain": "판단 보류(현장 확인)"}
# 고객 표본 검수에서 확인된 주요 오탐 원인 - 판독 창의 선택 목록. 모아서 모델 개선에 쓴다.
REASONS = ["그림자 위치 변화", "경사지붕 일조·밝기 차이", "나지→포장면", "차량·적치물", "비닐하우스·컨테이너 색·반사", "수목·식생", "기타"]


def path(cfg: dict) -> Path:
    return Path(cfg["interpretation"]["out_dir"]) / "screen" / "screen_labels.csv"


def load(cfg: dict) -> pd.DataFrame | None:
    p = path(cfg)
    if not p.exists():
        return None
    return pd.read_csv(p, encoding="utf-8-sig", dtype=str, keep_default_na=False)


def excluded(cfg: dict) -> set[tuple[str, str]]:
    """오탐으로 판독한 (SID, PNU). PNU까지 보는 이유: 다시 판독 실행하면 같은 SID가 다른 대상일 수 있다."""
    t = load(cfg)
    if t is None:
        return set()
    t = t[t["label"] == "not_structure"]
    return set(zip(t["sample_id"], t["pnu"]))


def export(cfg: dict, log=print) -> Path:
    """판독표와 근거 영상을 만든다. 이미 판독한 표가 있으면 같은 (SID, PNU)의 판정을 이어받는다. 반환: 판독표 경로."""
    from .survey import KINDS, UNREG_TIERS, _dates, targets

    out = path(cfg)
    out.parent.mkdir(parents=True, exist_ok=True)
    t = targets(cfg)
    images, dates, t1, _ = _dates(cfg)
    io = Path(cfg["interpretation"]["out_dir"])
    objs = gpd.read_file(io / "vectors" / "structure_change_objects.gpkg")
    unreg = objs[objs["kind"] == "unregistered"]
    code = {v: k for k, v in KINDS.items()}
    rows = pd.DataFrame({"sample_id": t["SID"], "stratum": "screen_" + t["KIND"].map(code), "kind": t["KIND"],
                         "pnu": t["PNU"].astype(str), "addr": t["ADDR"], "jimok": t["JIMOK"], "area_m2": t["AREA_M2"],
                         "x": t["X"], "y": t["Y"], "label": "", "note": "", "judge": "", "reason": ""})
    old = load(cfg)
    if old is not None and len(old):
        keep = old.set_index(["sample_id", "pnu"])
        for c in ("label", "note", "judge", "reason"):
            if c in keep:
                rows[c] = [keep[c].get((s, p), "") for s, p in zip(rows["sample_id"], rows["pnu"])]
        log(f"   이전 판독 {int((rows['label'] != '').sum())}건 이어받음")
    log(f"   판독 대상 {len(rows)}건 - 근거 영상 작성")
    for i, r in enumerate(t.itertuples()):
        img = out.parent / f"{r.SID}.jpg"
        if img.exists():
            continue
        if r.SID[0] in UNREG_TIERS:
            mine = unreg[unreg["tier"].isin(UNREG_TIERS[r.SID[0]])]
            inside = mine[mine.intersects(r.geometry)].geometry.tolist()
            chip_pair(images, dates, r.geometry, inside, img, half_m=35, title=f"{r.SID} {r.JIMOK} {r.ADDR}", parcel=r.geometry)
        else:
            chip_pair(images, dates, r.geometry, [r.geometry], img, half_m=35, title=f"{r.SID} {r.KIND} {r.AREA_M2:.0f}m2")
        if (i + 1) % 100 == 0:
            log(f"   근거 영상 {i + 1}/{len(t)}")
    tmp = out.with_suffix(".tmp")
    rows[COLUMNS].to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(out)
    return out


def summary(cfg: dict) -> dict | None:
    """보고서용 판독 집계. 판독표가 없거나 아무것도 판독하지 않았으면 None."""
    t = load(cfg)
    if t is None or not (t["label"] != "").any():
        return None
    out = {"total": len(t), "done": int((t["label"] != "").sum())}
    out.update({k: int((t["label"] == k).sum()) for k in LABELS})
    out["by_kind"] = {k: {"total": int(len(g)), **{c: int((g["label"] == c).sum()) for c in LABELS}}
                      for k, g in t.groupby("kind")}
    fp = t[t["label"] == "not_structure"]
    out["reasons"] = fp["reason"].replace("", "미기재").value_counts().to_dict()
    return out
