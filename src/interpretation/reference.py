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
