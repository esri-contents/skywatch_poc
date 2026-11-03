"""보고서 붙임 1(독립 미등재 구조물 필지)을 필지 폴리곤에 조인해 SHP로 내보낸다 - Pro에 바로 올려 보는 용도.

보고서 표와 SHP가 같은 행·같은 연번이 되도록 둘 다 appendix1()을 쓴다. SHP 필드명은 10바이트 제한이라
영문 약어로 두고, 필드 설명을 같은 폴더의 필드설명.txt에 쓴다. 문자 인코딩은 UTF-8(.cpg 포함).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

NAME = "appendix1_unregistered_parcels"
FIELDS = {  # SHP 필드 → (원 열, 설명)
    "SEQ": (None, "연번 - 보고서 붙임 1 표의 연번과 같음(PNU순)"),
    "PNU": ("pnu", "필지고유번호(19자리)"),
    "ADDR": ("addr", "소재지(지번 주소)"),
    "JIMOK": ("jimok", "지목"),
    "N_OBJ": ("unregistered_n", "미등재 판독 덩어리 수(건물 레이어 1m 밖, 등재 건물과 지붕이 이어지지 않는 것)"),
    "UNREG_M2": ("unregistered_m2", "미등재 판독 덩어리 면적 합(㎡)"),
    "REG_M2": ("registered_m2", "필지 내 건물 레이어(등재) 면적(㎡)"),
    "PARCEL_M2": (None, "필지 면적(㎡, EPSG:5186 계산값)"),
    "STATUS": (None, "기준 시기 필지 판독 상태(있음(등재+판독)/있음(등재만)/있음(판독만)/없음/영상 부족)"),
}


def appendix1(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """독립 미등재 필지를 PNU순으로 정렬하고 연번(SEQ)을 붙인다."""
    u = parcels[parcels["unregistered_conf"] == "독립"].sort_values(["pnu"]).reset_index(drop=True)
    return u.assign(SEQ=range(1, len(u) + 1))


def write_appendix1_shp(parcels: gpd.GeoDataFrame, status_field: str, out_dir: Path, note: str = "") -> Path:
    """SHP 묶음(shp·shx·dbf·prj·cpg)과 필드설명.txt, 같은 내용의 CSV, 전부 담은 zip을 만든다. 반환: zip 경로."""
    u = appendix1(parcels)
    t = gpd.GeoDataFrame({"SEQ": u["SEQ"].astype(int)}, geometry=u.geometry.values, crs=parcels.crs)
    for f, (col, _) in FIELDS.items():
        if col:
            t[f] = u[col].to_numpy()
    t["N_OBJ"] = t["N_OBJ"].fillna(0).astype(int)
    for f in ("UNREG_M2", "REG_M2"):
        t[f] = t[f].astype(float).round(1)
    t["PARCEL_M2"] = t.geometry.area.round(1)
    t["STATUS"] = u[status_field].to_numpy()
    t = t[[*FIELDS, "geometry"]]
    out_dir.mkdir(parents=True, exist_ok=True)
    shp = out_dir / f"{NAME}.shp"
    t.to_file(shp, driver="ESRI Shapefile", encoding="UTF-8")
    pd.DataFrame(t.drop(columns="geometry")).to_csv(out_dir / f"{NAME}.csv", index=False, encoding="utf-8-sig")
    desc = ["보고서 붙임 1. 독립 미등재 구조물 필지 - 필지 폴리곤(SHP)", f"필지 {len(t):,}개 · 좌표계 EPSG:5186(KGD2002 Central Belt 2010) · 인코딩 UTF-8", ""]
    desc += [f"{f:<10} {d}" for f, (_, d) in FIELDS.items()]
    if note:
        desc += ["", note]
    (out_dir / "필드설명.txt").write_text("\n".join(desc) + "\n", encoding="utf-8-sig")
    z = out_dir.with_suffix(".zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out_dir.iterdir()):
            zf.write(p, f"{out_dir.name}/{p.name}")
    return z
