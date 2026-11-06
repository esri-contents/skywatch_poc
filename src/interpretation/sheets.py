"""붙임 1 필지 도면 PDF - 독립 미등재 필지마다 A4 1쪽(위: 기준 시기, 아래: 비교 시기, 필지 정보 칸, 축척·방위).

Pro 레이아웃·지도 시리즈로 만들던 것을 분석 Python으로 옮겼다(Pro 안에서 300쪽을 내보내다 Pro가 종료되는 문제).
쪽 순서·연번은 보고서 붙임 1과 같다(appendix1). 쪽마다 150dpi 영상으로 그려 JPEG로 PDF에 넣는다(320쪽 약 2.5분, 약 115MB).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image, ImageDraw

from .deliver import appendix1
from .evidence import _font, _rings
from .obia import read_tile

DPI = 150
PAGE_MM = (210, 297)
FRAME_MM = (180, 102)                       # 지도 칸 가로·세로
FRAMES_TOP_MM = {"t0": 35, "t2": 143}       # 위쪽 여백 기준 지도 칸 위치(왼쪽 15mm)
MARGIN = 1.8                                # 필지 범위 대비 지도 범위 배율
MIN_WIDTH_M = 60                            # 작은 필지도 주변이 보이게 최소 지도 가로(m)
NICE_M = (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000)
OBJ_WHERE = {"t0": lambda o: (o["kind"] == "unregistered") & (o["tier"] == "U"),
             "t2": lambda o: o["kind"].isin(["unregistered", "new", "gone"])}


def _px(mm: float) -> int:
    return int(round(mm * DPI / 25.4))


F_TITLE, F_BODY, F_SMALL = _font(_px(5.3)), _font(_px(3.4)), _font(_px(3.0))


def _extent(geom) -> tuple[tuple[float, float, float, float], float]:
    """필지가 지도 칸 가운데 들어오는 범위와 1mm당 m."""
    x0, y0, x1, y1 = geom.bounds
    mpp = max((x1 - x0) / FRAME_MM[0], (y1 - y0) / FRAME_MM[1], MIN_WIDTH_M / MARGIN / FRAME_MM[0]) * MARGIN
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = FRAME_MM[0] / 2 * mpp, FRAME_MM[1] / 2 * mpp
    return (cx - hw, cy - hh, cx + hw, cy + hh), mpp


def _frame(path: str, b, parcels: gpd.GeoDataFrame, objs: gpd.GeoDataFrame, target, label: str) -> Image.Image:
    w, h = _px(FRAME_MM[0]), _px(FRAME_MM[1])
    rgb = read_tile(path, b, (b[2] - b[0]) / w)
    im = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize((w, h))
    dr = ImageDraw.Draw(im)
    sx, sy = w / (b[2] - b[0]), h / (b[3] - b[1])
    tf = lambda xy: ((xy[0] - b[0]) * sx, (b[3] - xy[1]) * sy)  # noqa: E731
    for g in parcels.geometry.iloc[parcels.sindex.query(_box(b))]:
        for ring in _rings(g):
            dr.line([tf(p) for p in ring], fill=(255, 255, 255), width=1)
    for g in objs.geometry.iloc[objs.sindex.query(_box(b))]:
        for ring in _rings(g):
            dr.line([tf(p) for p in ring], fill=(255, 225, 0), width=3)
    for ring in _rings(target):
        dr.line([tf(p) for p in ring], fill=(215, 40, 50), width=4)
    tw = dr.textlength(label, font=F_SMALL)
    dr.rectangle((0, 0, tw + 12, _px(5)), fill=(0, 0, 0))
    dr.text((6, 3), label, fill=(255, 255, 255), font=F_SMALL)
    return im


def _box(b):
    from shapely.geometry import box
    return box(*b)


def _north(dr: ImageDraw.ImageDraw, x: int, y: int) -> None:
    """지도 칸 오른쪽 위 방위 표시(좌표계 EPSG:5186 - 위가 북쪽)."""
    r = _px(5)
    dr.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255), outline=(0, 0, 0), width=2)
    dr.polygon([(x, y - r + 6), (x - r // 2, y + r // 2), (x, y + r // 5), (x + r // 2, y + r // 2)], fill=(0, 0, 0))
    dr.rectangle((x - _px(2), y + r + 1, x + _px(2), y + r + _px(4.5)), fill=(255, 255, 255))
    dr.text((x - _px(1.2), y + r + 2), "N", fill=(0, 0, 0), font=F_BODY)


def _scalebar(dr: ImageDraw.ImageDraw, right_mm: float, top_mm: float, mpp: float) -> None:
    length = max((m for m in NICE_M if m / mpp <= 50), default=NICE_M[0])
    mm = length / mpp
    x0, y0, hgt = _px(right_mm - mm), _px(top_mm), _px(1.6)
    for i in range(4):
        xa, xb = x0 + _px(mm * i / 4), x0 + _px(mm * (i + 1) / 4)
        dr.rectangle((xa, y0, xb, y0 + hgt), fill=(0, 0, 0) if i % 2 == 0 else (255, 255, 255), outline=(0, 0, 0))
    dr.text((x0 - _px(1), y0 + hgt + 3), "0", fill=(0, 0, 0), font=F_SMALL)
    lab = f"{length:,} m"
    dr.text((x0 + _px(mm) - dr.textlength(lab, font=F_SMALL), y0 + hgt + 3), lab, fill=(0, 0, 0), font=F_SMALL)


def page(r, images: dict, dates: dict, parcels, objs: dict, total: int, title: str) -> Image.Image:
    pg = Image.new("RGB", (_px(PAGE_MM[0]), _px(PAGE_MM[1])), "white")
    dr = ImageDraw.Draw(pg)
    L = _px(15)
    dr.text((L, _px(10)), f"독립 미등재 구조물 필지 도면 · 연번 {r.SEQ}", fill=(0, 0, 0), font=F_TITLE)
    dr.text((L, _px(19)), f"{title} · 보고서 붙임 1과 같은 연번 · 빨간 선 = 대상 필지, 노란 선 = 판독 덩어리, 흰 선 = 필지 경계",
            fill=(60, 60, 60), font=F_SMALL)
    b, mpp = _extent(r.geometry)
    for e, top in FRAMES_TOP_MM.items():
        name = "기준 시기" if e == "t0" else "비교 시기"
        dr.text((L, _px(top - 6)), f"{name} 영상", fill=(0, 0, 0), font=F_BODY)
        im = _frame(images[e], b, parcels, objs[e], r.geometry, f"{name} {dates[e]}")
        pg.paste(im, (L, _px(top)))
        dr.rectangle((L, _px(top), L + im.width, _px(top) + im.height), outline=(0, 0, 0), width=2)
    _north(dr, L + _px(FRAME_MM[0]) - _px(9), _px(FRAMES_TOP_MM["t0"]) + _px(10))

    top = 251
    dr.rectangle((L, _px(top), L + _px(180), _px(top + 32)), fill=(244, 246, 248), outline=(0, 0, 0), width=2)
    lines = [f"PNU  {r.pnu}        지목  {r.jimok}        판독 상태  {r.status}",
             f"소재지  {r.addr}",
             f"필지 면적  {r.geometry.area:,.1f}㎡     등재 건물 면적  {r.registered_m2:,.1f}㎡     "
             f"미등재 판독 면적  {r.unregistered_m2:,.1f}㎡ ({int(r.unregistered_n)}개 덩어리)",
             "현장 확인:  □ 있음  □ 없음  □ 확인불가        물건조서:  □ 등재  □ 미등재        확인자 __________"]
    for i, t in enumerate(lines):
        dr.text((L + _px(4), _px(top + 3 + i * 7)), t, fill=(0, 0, 0), font=F_BODY)
    _scalebar(dr, 195, 286, mpp)
    dr.text((L, _px(287)), f"{r.SEQ} / {total}  ·  축척 약 1:{round(mpp * 1000, -1):,.0f}(A4 인쇄 기준)  ·  좌표계 EPSG:5186",
            fill=(90, 90, 90), font=F_SMALL)
    return pg


def export(cfg: dict, pdf: Path, log=print) -> tuple[Path, int]:
    """붙임 1 필지마다 1쪽인 PDF를 쓴다. 반환: (PDF 경로, 쪽수)."""
    ic = cfg["interpretation"]
    io = Path(ic["out_dir"])
    t1, t2 = ic["epochs"]
    ep = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    images = {"t0": ep[t1]["path"], "t2": ep[t2]["path"]}
    dates = {"t0": str(ep[t1]["date"]), "t2": str(ep[t2]["date"])}
    parcels = gpd.read_file(io / "vectors" / "parcel_interpretation.gpkg")
    o = gpd.read_file(io / "vectors" / "structure_change_objects.gpkg")
    objs = {e: o[f(o)].reset_index(drop=True) for e, f in OBJ_WHERE.items()}
    u = appendix1(parcels).assign(status=lambda d: d[f"status_{t1}"])
    if not len(u):
        raise RuntimeError("붙임 1(독립 미등재 필지)이 없어 도면을 만들 것이 없습니다.")
    title = cfg["project"].get("official_name", cfg["project"]["id"])
    log(f"   필지 {len(u)}쪽 작성(A4, {DPI}dpi)")
    part = pdf.with_name(pdf.stem + ".작성중.pdf")  # 취소·실패 시 완성본처럼 보이는 파일이 남지 않게
    try:
        for i, r in enumerate(u.itertuples(), 1):
            page(r, images, dates, parcels, objs, len(u), title).save(part, "PDF", resolution=DPI, quality=75, append=i > 1)
            if i % 25 == 0 or i == len(u):
                log(f"   {i}/{len(u)}쪽")
        part.replace(pdf)
    finally:
        part.unlink(missing_ok=True)
    log(f"   저장: {pdf} ({pdf.stat().st_size / 1e6:.0f}MB)")
    return pdf, len(u)
