"""보고서 붙임 1(미등재 - 도면·대장 모두 없는 구조물 필지)과 붙임 1-2(미등재 - 도면은 있으나 대장 없는 건물 필지)를
필지 폴리곤에 조인해 SHP로 내보낸다 - Pro에 바로 올려 보는 용도.

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
    "N_OBJ": ("unregistered_n", "미등재 판독 덩어리 수(대장 연계 건물 1m 밖)"),
    "N_NOMAP": ("unregistered_standalone_n", "그중 도면·대장 모두 없는 덩어리 수(어느 건물 윤곽에도 없음)"),
    "N_NOREG": ("unregistered_noreg_n", "그중 도면은 있으나 대장 없는 덩어리 수(대장 미연계 건물 윤곽 위)"),
    "UNREG_M2": ("unregistered_m2", "미등재 판독 덩어리 면적 합(㎡)"),
    "REG_M2": ("registered_m2", "필지 내 대장 연계 건물 윤곽 면적(㎡)"),
    "MAP_M2": ("mapped_m2", "필지 내 건물 윤곽 전체 면적(㎡, 대장 여부 무관 - 건물통합정보·도로명주소 건물)"),
    "VIOLT_N": ("violt_n", "필지 내 위반건축물 수(건물통합정보)"),
    "PARCEL_M2": (None, "필지 면적(㎡, EPSG:5186 계산값)"),
    "STATUS": (None, "기준 시기 필지 판독 상태(있음(등재+판독)/있음(등재만)/있음(판독만)/없음/영상 부족)"),
    # 건축물대장 대조(필지 PNU 단위, 부속지번 포함) - 대조를 하지 않은 결과에는 없음
    "REG_N": ("register_n", "이 필지를 대표·부속지번으로 가진 건축물대장 수(동 단위, 0 = 대장 없음)"),
    "REG_ARCH": ("register_arch_m2", "대장 건축면적 합(㎡, 대표지번 기준. 여러 필지에 걸친 대장은 0일 수 있음)"),
    "REG_CHK": ("register_check", "대장 대조(대장 없음 / 대장 면적 초과 = 판독 면적이 대장 건축면적의 2배·50㎡ 초과 / 대장 있음)"),
}
NAME_NOREG = "appendix1b_mapped_without_register"


def _numbered(parcels: gpd.GeoDataFrame, conf: str) -> gpd.GeoDataFrame:
    if "unregistered_conf" not in parcels:
        return parcels.iloc[:0].assign(SEQ=pd.Series(dtype=int))
    u = parcels[parcels["unregistered_conf"] == conf].sort_values(["pnu"]).reset_index(drop=True)
    return u.assign(SEQ=range(1, len(u) + 1))


def appendix1(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """붙임 1: 도면·대장 모두 없는 구조물이 있는 필지('독립'), PNU순 연번(SEQ)."""
    return _numbered(parcels, "독립")


def appendix1b(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """붙임 1-2: 도면(건물 윤곽)은 있으나 대장이 없는 건물 위 구조물만 있는 필지('대장 없음'), PNU순 연번."""
    return _numbered(parcels, "대장 없음")


def write_appendix1_shp(parcels: gpd.GeoDataFrame, status_field: str, out_dir: Path, note: str = "", name: str = NAME,
                        rows: gpd.GeoDataFrame | None = None,
                        title: str = "보고서 붙임 1. 독립 미등재 구조물 필지") -> Path:
    """SHP 묶음(shp·shx·dbf·prj·cpg)과 필드설명.txt, 같은 내용의 CSV, 전부 담은 zip을 만든다. 반환: zip 경로.

    rows를 주면 그 행(SEQ 포함)을 쓰고, 없으면 붙임 1(appendix1)을 쓴다.
    """
    u = appendix1(parcels) if rows is None else rows
    fields = {f: v for f, v in FIELDS.items() if v[0] is None or v[0] in u}
    t = gpd.GeoDataFrame({"SEQ": u["SEQ"].astype(int)}, geometry=u.geometry.values, crs=parcels.crs)
    for f, (col, _) in fields.items():
        if col:
            t[f] = u[col].to_numpy()
    t["N_OBJ"] = t["N_OBJ"].fillna(0).astype(int)
    for f in ("UNREG_M2", "REG_M2", "REG_ARCH"):
        if f in t:
            t[f] = t[f].astype(float).round(1)
    if "REG_N" in t:
        t["REG_N"] = t["REG_N"].fillna(0).astype(int)
    t["PARCEL_M2"] = t.geometry.area.round(1)
    t["STATUS"] = u[status_field].to_numpy()
    t = t[[*fields, "geometry"]]
    out_dir.mkdir(parents=True, exist_ok=True)
    shp = out_dir / f"{name}.shp"
    t.to_file(shp, driver="ESRI Shapefile", encoding="UTF-8")
    pd.DataFrame(t.drop(columns="geometry")).to_csv(shp.with_suffix(".csv"), index=False, encoding="utf-8-sig")
    desc = [f"{title} - 필지 폴리곤(SHP)", f"필지 {len(t):,}개 · 좌표계 EPSG:5186(KGD2002 Central Belt 2010) · 인코딩 UTF-8", ""]
    desc += [f"{f:<10} {d}" for f, (_, d) in fields.items()]
    if note:
        desc += ["", note]
    (out_dir / "필드설명.txt").write_text("\n".join(desc) + "\n", encoding="utf-8-sig")
    z = out_dir.with_suffix(".zip")
    # 직접 쓴 파일만 담는다 - Pro가 레이어로 열면 같은 폴더에 *.sr.lock 같은 잠긴 파일을 만든다
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(out_dir.iterdir()):
            if p.stem == shp.stem and p.suffix.lower() in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".csv") or p.name == "필드설명.txt":
                zf.write(p, f"{out_dir.name}/{p.name}")
    return z
