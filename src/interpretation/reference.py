"""행정자료(건축물대장·건축허가)로 만든 신규 판정 재현율의 정답 대용.

T1~T2 사이에 착공했거나 사용승인된 필지는 그 사이에 구조물이 새로 섰을 가능성이 높다. 신규
객체가 이 필지에서 하나라도 잡혔는지로 재현율을 잰다. 정답이 아니라 대용이다.
- 착공했어도 T2 촬영 전에 지붕이 올라가지 않았을 수 있다(T2 촬영 월일 미상).
- 사용승인일이 기간 안이어도 공사는 T1 전에 끝났을 수 있다.
- 무허가·가설 구조물은 행정자료에 없다.

PNU는 산 여부 자리(11번째)를 뺀 key9로 맞춘다(buildings.validation._join_key9와 같은 방식).
과거에 수집한 대장 PNU는 이 자리 코드가 틀려 있었다.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


def key9(pnu: pd.Series) -> pd.Series:
    s = pnu.astype(str)
    return s.str[:10] + s.str[11:]


def _dates(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df:
        return pd.Series(pd.NaT, index=df.index)
    return pd.to_datetime(df[col].astype(str).str[:8], format="%Y%m%d", errors="coerce")


def construction_events(register_path: str | Path | None, permits_path: str | Path | None,
                        start: str, end: str) -> pd.DataFrame:
    """[start, end] 안의 사용승인(대장·허가)·실착공(허가) 기록. 열: key9, source, event, date, purpose."""
    rows = []
    for path, source, cols in [(register_path, "대장", ["useAprDay"]), (permits_path, "허가", ["realStcnsDay", "useAprDay"])]:
        if not path or not Path(path).exists():
            continue
        df = pd.DataFrame(json.loads(Path(path).read_text(encoding="utf-8")))
        if "pnu" not in df:
            continue
        for col in cols:
            d = _dates(df, col)
            m = (d >= start) & (d <= end)
            rows.append(pd.DataFrame({"key9": key9(df.loc[m, "pnu"]), "source": source,
                                      "event": "착공" if col == "realStcnsDay" else "사용승인", "date": d[m],
                                      "purpose": df.loc[m].get("mainPurpsCdNm", pd.Series(index=df.index[m], dtype=str))}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["key9", "source", "event", "date", "purpose"])


def new_structure_recall(parcels: pd.DataFrame, events: pd.DataFrame) -> dict:
    """행정자료 기준 신축 필지 중 신규 객체(A·B·C)가 잡힌 비율. 지구 필지와 매칭된 것만 센다."""
    p = parcels.assign(key9=key9(parcels["pnu"])).drop_duplicates("key9").set_index("key9")
    out = {}
    for event in ["착공", "사용승인", "전체"]:
        e = events if event == "전체" else events[events["event"] == event]
        keys = sorted(set(e["key9"]) & set(p.index))
        sub = p.loc[keys]
        ab = (sub["new_A_n"] + sub["new_B_n"]) > 0
        abc = ab | (sub["new_C_n"] > 0)
        out[event] = {"parcels": len(keys), "A": int((sub["new_A_n"] > 0).sum()), "A_or_B": int(ab.sum()),
                      "A_B_or_C": int(abc.sum()), "recall_A_or_B": float(ab.mean()) if keys else None,
                      "recall_any": float(abc.mean()) if keys else None, "pnu": sub["pnu"].tolist()}
    return out


# ---------------------------------------------------------------------------
# 건축물대장 필지 연계 - VWorld 건물 윤곽 기준 '미등재' 판정을 대장으로 다시 확인한다.
# 대장에는 도형이 없어 객체 단위로는 못 맞추고, 필지(PNU) 단위로만 맞춘다.
# ---------------------------------------------------------------------------

def _pnu(df: pd.DataFrame, prefix: str = "") -> pd.Series:
    plat = df[f"{prefix}platGbCd"].astype(str).map({"0": "1", "1": "2"}).fillna("1")
    return (df[f"{prefix}sigunguCd"].astype(str) + df[f"{prefix}bjdongCd"].astype(str) + plat
            + df[f"{prefix}bun"].astype(str).str.zfill(4) + df[f"{prefix}ji"].astype(str).str.zfill(4))


def _load(path) -> pd.DataFrame:
    if not path or not Path(path).exists():
        return pd.DataFrame()
    return pd.DataFrame(json.loads(Path(path).read_text(encoding="utf-8")))


def register_by_parcel(title_path, basis_path=None, atch_path=None) -> pd.DataFrame:
    """필지(key9)별 건축물대장 요약. 열: register_n, register_arch_m2, register_purpose, register_multi_lot.

    - register_n: 이 필지를 대표지번(표제부·일반건축물) 또는 부속지번(종류 무관)으로 가진 대장 수
    - register_arch_m2: 대표지번이 이 필지인 대장 건축면적 합(표제부·일반건축물)
    - register_multi_lot: 여러 필지에 걸친 대장이 있음(부속지번 연계) - 이 필지의 건축면적 비교는 믿을 수 없다
    대장 PNU의 산 여부 자리는 과거 수집분에서 틀린 적이 있어 key9로 맞춘다.
    """
    title, basis, atch = _load(title_path), _load(basis_path), _load(atch_path)
    rows = []
    if len(title):
        t = title.assign(key9=key9(_pnu(title)), arch=pd.to_numeric(title.get("archArea"), errors="coerce").fillna(0))
        rows.append(t[["key9", "mgmBldrgstPk", "arch"]].assign(main=True, purpose=t.get("mainPurpsCdNm")))
    if len(basis):  # 표제부 수집에서 빠진 대장(기본개요에만 있는 일반·표제부)을 보탠다
        b = basis[basis["regstrKindCd"].astype(str).isin(["2", "3"])]
        rows.append(pd.DataFrame({"key9": key9(_pnu(b)), "mgmBldrgstPk": b["mgmBldrgstPk"], "arch": 0.0,
                                  "main": True, "purpose": None}))
    if len(atch):
        a = atch[atch["atchBun"].astype(str).str.strip() != ""]
        a_key = key9(
            a["atchSigunguCd"].astype(str) + a["atchBjdongCd"].astype(str)
            + a["atchPlatGbCd"].astype(str).map({"0": "1", "1": "2"}).fillna("1")
            + a["atchBun"].astype(str).str.zfill(4) + a["atchJi"].astype(str).str.zfill(4))
        main_key = key9(_pnu(a))
        rows.append(pd.DataFrame({"key9": a_key, "mgmBldrgstPk": a["mgmBldrgstPk"], "arch": 0.0,
                                  "main": False, "purpose": None}))
        multi = set(a_key) | set(main_key)
    else:
        multi = set()
    if not rows:
        return pd.DataFrame(columns=["register_n", "register_arch_m2", "register_purpose", "register_multi_lot"])
    r = pd.concat(rows, ignore_index=True)
    # 같은 대장이 표제부와 기본개요에 모두 있으면 건축면적이 있는 행(표제부)을 남긴다
    r = r.sort_values("arch", ascending=False).drop_duplicates(["key9", "mgmBldrgstPk"])
    g = r.groupby("key9")
    out = pd.DataFrame({"register_n": g.size(), "register_arch_m2": g["arch"].sum().round(1),
                        "register_purpose": g["purpose"].agg(lambda s: s.dropna().mode().iloc[0] if s.notna().any() else "")})
    out["register_multi_lot"] = out.index.isin(multi)
    return out


REGISTER_CHECK = {
    "missing": "대장 없음",        # 판독 구조물이 있는데 대장이 없는 필지 - 무허가·미등재 의심
    "excess": "대장 면적 초과",    # 판독 구조물 면적이 대장 건축면적을 크게 넘음 - 증축·가설물 의심
    "ok": "대장 있음",
}


def attach_register(table: pd.DataFrame, reg: pd.DataFrame, t1: str, cfg: dict) -> pd.DataFrame:
    """필지 표에 대장 열(register_*)과 판정(register_check)을 붙인다.

    register_check는 판독 구조물이 min_struct_m2 이상이고 PNU가 있는 필지에만 붙는다(PNU가 없으면 대조할 수 없다).
    면적 초과: 판독 면적 > max(대장 건축면적 x excess_ratio, 대장 건축면적 + excess_min_m2).
    여러 필지에 걸친 대장(register_multi_lot)이나 건축면적 미상(0)인 필지는 면적을 비교하지 않는다.
    """
    pnu = table["pnu"].fillna("").astype(str).str.strip()
    k = key9(pnu)
    out = table.copy()
    j = reg.reindex(k.to_numpy())
    out["register_n"] = j["register_n"].fillna(0).astype(int).to_numpy()
    out["register_arch_m2"] = j["register_arch_m2"].fillna(0).to_numpy()
    out["register_purpose"] = j["register_purpose"].fillna("").to_numpy()
    out["register_multi_lot"] = j["register_multi_lot"].fillna(False).astype(bool).to_numpy()
    seen = (out[f"struct_{t1}_m2"] >= cfg["min_struct_m2"]) & (pnu.str.len() == 19).to_numpy()
    arch = out["register_arch_m2"]
    limit = np.maximum(arch * cfg.get("register_excess_ratio", 2.0), arch + cfg.get("register_excess_min_m2", 50))
    comparable = (arch > 0) & ~out["register_multi_lot"]
    out["register_check"] = np.select(
        [~seen, out["register_n"] == 0, comparable & (out[f"struct_{t1}_m2"] > limit)],
        ["", REGISTER_CHECK["missing"], REGISTER_CHECK["excess"]], REGISTER_CHECK["ok"])
    return out


def register_area_ratio(table: pd.DataFrame, t1: str) -> pd.Series:
    """면적 비교가 가능한 필지(대장 건축면적 있음, 단일 필지 대장, 구조물 판독)의 판독 면적 / 대장 건축면적."""
    m = (table["register_check"] != "") & (table["register_arch_m2"] > 0) & ~table["register_multi_lot"].astype(bool)
    return table.loc[m, f"struct_{t1}_m2"] / table.loc[m, "register_arch_m2"]
