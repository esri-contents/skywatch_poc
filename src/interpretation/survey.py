"""현장조사 연계 - 판독 결과를 조사표(엑셀)·Field Maps용 파일 지오데이터베이스로 내보내고, 조사 결과를 다시 읽는다.

조사 대상과 번호는 보고서와 같다.
  U001…  붙임 1 독립 미등재 구조물 필지(연번 = 붙임 1 연번, 범위 = 필지 폴리곤)
  A01…   붙임 2 신규 의심(A) 객체(면적순)
  G01…   붙임 2 소멸 의심(A·B) 객체(면적순)
내보내기: <out>/interpretation/survey/
  현장조사표.xlsx       대상마다 근거 영상, 결과 칸(드롭다운)
  survey.gdb          survey_points(조사 지점, 결과 필드) · survey_areas(필지·객체 범위)
                        도메인·별칭·사진 첨부는 Pro 안에서 Add-in이 지오프로세싱으로 붙인다(arcpy 라이선스가 Pro 밖에선 안 됨)
  images/<SID>.jpg      근거 영상
가져오기: 채운 엑셀 또는 gdb(Field Maps 동기화본)를 읽어 survey_results.csv로 정리 → 보고서 'Ⅳ-3 현장조사 결과'.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon

from .deliver import appendix1
from .evidence import chip_pair

RESULT_FIELDS = {  # gdb 필드 → (엑셀 열, 허용 값)
    "EXISTS": ("구조물 실재", ["있음", "없음", "확인불가"]),
    "USE_TYPE": ("용도", ["주택", "창고", "축사", "비닐하우스", "컨테이너", "공장·작업장", "기타"]),
    "REGISTERED": ("물건조서 등재", ["예", "아니오", "확인불가"]),
    "SURVEYOR": ("조사자", None),
    "SURVEY_DT": ("조사일", None),
    "PHOTO": ("사진 파일", None),
    "NOTE": ("비고", None),
}
KINDS = {"U": "미등재(독립)", "A": "신규 의심", "G": "소멸 의심"}
# gdb 필드 이름이 내부 이름과 다른 것. EXISTS는 파일 지오데이터베이스 예약어라 GDAL이 EXISTS_로 바꿔 쓰고(실측),
# 그러면 Pro의 도메인 지정·Field Maps 결과 가져오기가 그 필드를 못 찾는다 - gdb에서는 EXIST_YN을 쓴다.
GDB_NAMES = {"EXISTS": "EXIST_YN"}
_FROM_GDB = {"EXIST_YN": "EXISTS", "EXISTS_": "EXISTS"}  # EXISTS_: 이 수정 전에 내보낸 gdb


def _dates(cfg: dict) -> tuple[dict, dict, str, str]:
    ic = cfg["interpretation"]
    t1, t2 = ic["epochs"]
    ep = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    return {e: ep[e]["path"] for e in (t1, t2)}, {e: str(ep[e]["date"]) for e in (t1, t2)}, t1, t2


def targets(cfg: dict) -> gpd.GeoDataFrame:
    """조사 대상(번호·구분·필지 정보·범위 폴리곤)."""
    io = Path(cfg["interpretation"]["out_dir"])
    parcels = gpd.read_file(io / "vectors" / "parcel_interpretation.gpkg")
    objs = gpd.read_file(io / "vectors" / "structure_change_objects.gpkg")
    info = parcels.drop(columns="geometry").drop_duplicates("pnu").set_index("pnu")
    u = appendix1(parcels)
    rows = [{"SID": f"U{r.SEQ:03d}", "KIND": KINDS["U"], "PNU": r.pnu, "AREA_M2": round(float(r.unregistered_m2), 1),
             "geometry": r.geometry} for r in u.itertuples()]
    for code, kind, tiers in (("A", "new", ["A"]), ("G", "gone", ["A", "B"])):
        o = objs[(objs["kind"] == kind) & objs["tier"].isin(tiers)].sort_values("area_m2", ascending=False)
        rows += [{"SID": f"{code}{i + 1:02d}", "KIND": KINDS[code], "PNU": r.pnu, "AREA_M2": round(float(r.area_m2), 1),
                  "geometry": r.geometry} for i, r in enumerate(o.itertuples())]
    t = gpd.GeoDataFrame(rows, geometry="geometry", crs=parcels.crs)
    t["ADDR"] = t["PNU"].map(info["addr"]).fillna("")
    t["JIMOK"] = t["PNU"].map(info["jimok"]).fillna("")
    p = t.geometry.representative_point()
    ll = gpd.GeoSeries(p, crs=t.crs).to_crs(4326)
    t["X"], t["Y"], t["LON"], t["LAT"] = p.x.round(2), p.y.round(2), ll.x.round(6), ll.y.round(6)
    t["IMG"] = t["SID"] + ".jpg"
    return t


def backup_previous(sdir: Path, log=print) -> list[Path]:
    """이전에 내보낸 조사표·gdb는 현장 결과가 적혀 있을 수 있어 덮어쓰지 않고 시각을 붙여 보관한다."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    kept = []
    for p in (sdir / "현장조사표.xlsx", sdir / "survey.gdb"):
        if not p.exists():
            continue
        dst = p.with_name(f"{p.stem}_{stamp}{p.suffix}")
        try:
            p.rename(dst)
        except OSError as e:
            raise RuntimeError(f"{p.name}를 보관하지 못함 - 엑셀이나 ArcGIS Pro에서 열려 있으면 닫거나 지도에서 레이어를 제거한 뒤 "
                               f"다시 실행하세요({e})") from e
        kept.append(dst)
        log(f"   이전 {p.name} → {dst.name}로 보관")
    return kept


def export(cfg: dict, log=print) -> Path:
    """조사표 엑셀과 gdb를 만든다. 반환: survey 폴더."""
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    from PIL import Image

    io = Path(cfg["interpretation"]["out_dir"])
    sdir = io / "survey"
    (sdir / "images").mkdir(parents=True, exist_ok=True)
    backup_previous(sdir, log)
    t = targets(cfg)
    images, dates, t1, _ = _dates(cfg)
    objs = gpd.read_file(io / "vectors" / "structure_change_objects.gpkg")
    unreg = objs[(objs["kind"] == "unregistered") & (objs["tier"] == "U")]
    log(f"   조사 대상 {len(t)}건(미등재 {int((t['KIND'] == KINDS['U']).sum())}, 신규 {int((t['KIND'] == KINDS['A']).sum())}, 소멸 {int((t['KIND'] == KINDS['G']).sum())}) - 근거 영상 작성")
    for i, r in enumerate(t.itertuples()):
        out = sdir / "images" / r.IMG
        if r.SID.startswith("U"):
            inside = unreg[unreg.intersects(r.geometry)].geometry.tolist()
            chip_pair({t1: images[t1]}, dates, r.geometry, inside, out, half_m=25, title=f"{r.SID} {r.JIMOK} {r.ADDR}", parcel=r.geometry)
        else:
            chip_pair(images, dates, r.geometry, [r.geometry], out, title=f"{r.SID} {r.KIND} {r.AREA_M2:.0f}m2")
        if (i + 1) % 50 == 0:
            log(f"   근거 영상 {i + 1}/{len(t)}")

    # 엑셀 조사표
    wb = Workbook()
    ws = wb.active
    ws.title = "조사표"
    head = ["조사번호", "구분", "PNU", "소재지", "지목", "판독 면적(㎡)", "위도", "경도", "근거 영상"] + [v[0] for v in RESULT_FIELDS.values()]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79" if c.column <= 9 else "8A5A00")
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    widths = [9, 12, 21, 30, 6, 10, 10, 11, 44, 11, 12, 12, 9, 11, 14, 24]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    thumbs = sdir / "images" / "_thumb"
    thumbs.mkdir(exist_ok=True)
    for k, r in enumerate(t.itertuples(), start=2):
        ws.append([r.SID, r.KIND, r.PNU, r.ADDR.replace("경기도 ", ""), r.JIMOK, r.AREA_M2, r.LAT, r.LON, ""] + [""] * len(RESULT_FIELDS))
        im = Image.open(sdir / "images" / r.IMG)
        im.thumbnail((310, 160))
        tp = thumbs / r.IMG
        im.save(tp, quality=80)
        xi = XImage(str(tp))
        ws.add_image(xi, f"I{k}")
        ws.row_dimensions[k].height = max(xi.height * 0.75 + 4, 30)
        for c in ws[k]:
            c.alignment = Alignment(vertical="center", wrap_text=True)
    n = len(t) + 1
    for j, (field, (col, allowed)) in enumerate(RESULT_FIELDS.items()):
        if allowed:
            dv = DataValidation(type="list", formula1='"' + ",".join(allowed) + '"', allow_blank=True)
            ws.add_data_validation(dv)
            letter = ws.cell(1, 10 + j).column_letter
            dv.add(f"{letter}2:{letter}{n}")
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{ws.cell(1, len(head)).column_letter}{n}"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"
    g = wb.create_sheet("작성 안내")
    for line in [
        ["현장조사표 작성 안내"],
        ["조사번호", "U = 보고서 붙임 1 독립 미등재 필지(연번 동일), A = 신규 의심, G = 소멸 의심(붙임 2 번호 동일)"],
        ["근거 영상", "노란 선 = 판독된 구조물 덩어리, 흰 선 = 필지 경계. 신규·소멸은 왼쪽 기준 시기, 오른쪽 비교 시기"],
        ["구조물 실재", "미등재·신규: 현장에 구조물이 있으면 '있음'. 소멸: 구조물이 실제로 없어졌으면 '있음'(판독이 맞음)"],
        ["물건조서 등재", "해당 구조물이 물건조서에 이미 있으면 '예'. '있음' + '아니오'가 누락 의심"],
        ["사진 파일", "촬영 사진 파일명(여러 장이면 쉼표로 구분)"],
        ["작성 후", "Add-in [현장조사 결과 가져오기]로 이 파일을 선택하면 보고서에 반영됨"],
    ]:
        g.append(line)
    g.column_dimensions["A"].width, g.column_dimensions["B"].width = 16, 100
    g["A1"].font = Font(bold=True, size=13)
    xlsx = sdir / "현장조사표.xlsx"
    wb.save(xlsx)

    # Field Maps용 gdb(결과 필드는 빈 문자열, 도메인·첨부는 Pro에서)
    gdb = sdir / "survey.gdb"
    base = t.drop(columns="geometry")
    for f in RESULT_FIELDS:
        base[GDB_NAMES.get(f, f)] = ""
    pts = gpd.GeoDataFrame(base, geometry=t.geometry.representative_point(), crs=t.crs)
    pts.to_file(gdb, layer="survey_points", driver="OpenFileGDB")
    multi = t.geometry.apply(lambda g: MultiPolygon([g]) if g.geom_type == "Polygon" else g)  # gdb 레이어는 한 가지 도형형식만
    gpd.GeoDataFrame(t[["SID", "KIND", "PNU", "AREA_M2"]], geometry=multi, crs=t.crs).to_file(gdb, layer="survey_areas", driver="OpenFileGDB")
    log(f"   조사표 {xlsx.name}, {gdb.name}(survey_points·survey_areas) 작성")
    return sdir


def _norm(v, allowed) -> str:
    s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
    if not allowed or not s:
        return s
    alias = {"o": "있음", "y": "예", "yes": "예", "x": "없음", "n": "아니오", "no": "아니오", "?": "확인불가"}
    return alias.get(s.lower(), s)  # 허용 값 밖의 입력은 그대로 두어 결과 표에서 보이게 한다


def import_results(cfg: dict, src: str | Path, log=print) -> Path:
    """채운 조사표(xlsx) 또는 Field Maps gdb(survey_points)를 읽어 survey_results.csv로 정리한다."""
    src = Path(src)
    if src.suffix.lower() in (".xlsx", ".xlsm"):
        x = pd.read_excel(src, sheet_name="조사표", dtype=str)
        t = pd.DataFrame({"SID": x["조사번호"], "KIND": x["구분"], "PNU": x["PNU"]})
        for f, (col, _) in RESULT_FIELDS.items():
            t[f] = x[col] if col in x else ""
    elif src.suffix.lower() == ".csv":
        t = pd.read_csv(src, encoding="utf-8-sig", dtype=str)
    else:
        # Field Maps 수집 후 내려받은 gdb·gpkg·shp - 레이어 이름이 바뀌어 있을 수 있어 SID 필드가 있는 레이어를 찾는다
        layers = gpd.list_layers(src)["name"].tolist() if src.suffix.lower() in (".gdb", ".gpkg") else [None]
        layer = "survey_points" if "survey_points" in layers else next(
            (n for n in layers if "SID" in gpd.read_file(src, layer=n, rows=1).columns), None)
        if layer is None and layers != [None]:
            raise ValueError(f"{src.name}에서 SID 필드가 있는 조사 레이어를 찾지 못함(레이어: {', '.join(layers)})")
        t = pd.DataFrame(gpd.read_file(src, layer=layer).drop(columns="geometry"))
        t = t.rename(columns={k: v for k, v in _FROM_GDB.items() if k in t and v not in t})
    missing = [c for c in ("SID", "KIND", "PNU") if c not in t]
    if missing:
        raise ValueError(f"조사 결과에 {', '.join(missing)} 열이 없음 - 내보낸 조사표·survey.gdb를 쓰세요")
    t = t[["SID", "KIND", "PNU", *[f for f in RESULT_FIELDS if f in t]]].copy()
    for f, (_, allowed) in RESULT_FIELDS.items():
        t[f] = [_norm(v, allowed) for v in t.get(f, pd.Series([""] * len(t)))]
    t = t[t["SID"].notna()]
    out = Path(cfg["interpretation"]["out_dir"]) / "survey" / "survey_results.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    t.to_csv(out, index=False, encoding="utf-8-sig")
    done = int((t["EXISTS"] != "").sum())
    log(f"   조사 결과 {done}/{len(t)}건 입력됨 → {out.name}")
    return out


def summarize(path: Path) -> pd.DataFrame:
    """구분별 조사 완료·실재·없음·확인불가·현장 정밀도(실재/(실재+없음))·물건조서 누락 의심."""
    from .qa import wilson
    t = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    rows = []
    for kind in KINDS.values():
        g = t[t["KIND"] == kind]
        if not len(g):
            continue
        y, n_, u = (int((g["EXISTS"] == v).sum()) for v in ("있음", "없음", "확인불가"))
        lo, hi = wilson(y, y + n_)
        rows.append({"kind": kind, "targets": len(g), "surveyed": y + n_ + u, "exists": y, "absent": n_, "unknown": u,
                     "precision": y / (y + n_) if y + n_ else float("nan"), "ci_lo": lo, "ci_hi": hi,
                     "missing_in_register": int(((g["EXISTS"] == "있음") & (g["REGISTERED"] == "아니오")).sum())})
    return pd.DataFrame(rows)
