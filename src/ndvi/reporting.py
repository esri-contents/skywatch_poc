"""한국어 보고서 생성: 인터랙티브 단일 HTML(외부 CDN/인터넷 불필요) + 정적 PNG/HTML 대체 산출물.

모든 수치는 분석 산출물(GeoPackage/래스터/메타데이터)에서 계산한다. 하드코딩된 결과값 없음.
지도는 순수 SVG(베이스맵 없음)로 그려 오프라인에서도 열린다.
"""

from __future__ import annotations

import base64
import html as _html
import io
import json
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import shapely  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from PIL import Image  # noqa: E402

from .classification import CHANGE_TYPE_KO  # noqa: E402

REQUIRED_NOTICE = ("본 분석은 Sentinel-2 10m 위성영상의 식생지수 변화를 활용한 탐색적 분석이며, 개별 건축물의 신축·철거, "
                   "지장물 존재 여부 또는 토지보상 적격성을 확정하기 위한 자료가 아닙니다.")
TITLE = "Sentinel-2 NDVI 및 연속지적도 기반 철도사업 주변 필지별 토지피복 변화 모니터링"
TEMPLATE = Path(__file__).with_name("report_template.html")

TYPE_COLORS = {
    "PERSISTENT_DECLINE": "#b2182b", "DECLINE_UNCONFIRMED": "#ef8a62", "TEMPORARY_DECLINE": "#f4c542",
    "GRADUAL_DECLINE": "#c9a27a", "FLUCTUATING": "#8e6bbf", "RECOVERY": "#4393c3",
    "STABLE_VEGETATION": "#4a9d5b", "STABLE_NON_VEGETATION": "#9aa3a8", "INSUFFICIENT_DATA": "#d9dde0",
}
PERSIST_COLORS = {"PERSISTENT": "#b2182b", "UNCONFIRMED": "#ef8a62", "TEMPORARY": "#f4c542",
                  "NONE": "#bcd9c0", "NOT_EVALUABLE": "#d9dde0"}


# ----------------------------------------------------------------------------- loading
def load_results(out: str | Path) -> dict:
    """기존 산출물에서 보고서 입력을 복원한다(--report-only / 재실행 스킵)."""
    out = Path(out)
    meta = json.loads((out / "metadata" / "analysis_metadata.json").read_text(encoding="utf-8"))
    years = [s["year"] for s in meta["scenes"]]
    arrs = []
    transform = None
    for y in years:
        with rasterio.open(out / "rasters" / f"ndvi_{y}.tif") as src:
            a = src.read(1).astype("float32")
            a[a == src.nodata] = np.nan
            arrs.append(a)
            transform = src.transform
    with rasterio.open(out / "rasters" / "persistence_class.tif") as src:
        pc = src.read(1)
    gp = out / "vectors" / "parcel_ndvi_stats.gpkg"
    parcels = gpd.read_file(gp) if gp.exists() else None
    if parcels is not None:
        parcels["pnu"] = parcels["pnu"].astype("string")
    rail = gpd.read_file(out / "vectors" / "railway_used.geojson").to_crs(5186)
    aoi = gpd.read_file(out / "vectors" / "aoi.geojson").to_crs(5186)
    return {"ndvi": np.stack(arrs), "transform": transform, "years": years, "parcels": parcels, "meta": meta,
            "rail": rail, "aoi": aoi, "persistence_class": pc,
            "pairs": [(years[i], years[i + 1]) for i in range(len(years) - 1)] + [(years[0], years[-1])]}


# ----------------------------------------------------------------------------- raster -> png
def _png_b64(rgba: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def ndvi_rgba(a: np.ndarray, vmin: float = 0.0, vmax: float = 0.9) -> np.ndarray:
    cm = matplotlib.colormaps["RdYlGn"]
    n = np.clip((np.nan_to_num(a, nan=0.0) - vmin) / (vmax - vmin), 0, 1)
    rgba = (cm(n) * 255).astype("uint8")
    rgba[..., 3] = np.where(np.isfinite(a), 255, 0)
    return rgba


def persistence_rgba(pc: np.ndarray) -> np.ndarray:
    rgba = np.zeros(pc.shape + (4,), dtype="uint8")
    for code, hexc in ((1, "#b2182b"), (2, "#f4c542"), (3, "#ef8a62")):
        c = tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5))
        m = pc == code
        rgba[m] = (*c, 235)
    return rgba


# ----------------------------------------------------------------------------- helpers
def _num(v, nd=2):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) or pd.isna(v) else round(float(v), nd)


def _path_d(geom, x0: float, y0: float) -> str:
    g = shapely.simplify(geom, 0.5, preserve_topology=True)
    polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
    parts = []
    for p in polys:
        if p.geom_type != "Polygon" or p.is_empty:
            continue
        for ring in [p.exterior, *p.interiors]:
            xy = np.asarray(ring.coords)
            seg = " ".join(f"{x - x0:.1f},{y0 - y:.1f}" for x, y in xy)
            parts.append("M" + seg + "Z")
    return "".join(parts)


def _lines_d(gdf: gpd.GeoDataFrame, x0: float, y0: float) -> str:
    parts = []
    for g in gdf.geometry:
        gs = [g] if g.geom_type == "LineString" else list(getattr(g, "geoms", []))
        for ln in gs:
            xy = np.asarray(ln.coords)
            parts.append("M" + " ".join(f"{x - x0:.1f},{y0 - y:.1f}" for x, y in xy))
    return "".join(parts)


def _font() -> None:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", "AppleGothic"):
        if cand in names:
            plt.rcParams["font.family"] = cand
            break
    plt.rcParams["axes.unicode_minus"] = False


# ----------------------------------------------------------------------------- summary
def executive_summary(res: dict) -> dict:
    p = res["parcels"]
    m = res["meta"]
    aoi = m["aoi"]["summary"]
    years = res["years"]
    s = {
        "period": f"{years[0]}–{years[-1]}", "years": years,
        "aoi_area_km2": aoi["aoi_area_m2"] / 1e6,
        "aoi_decline_area_ha": aoi["aoi_decline_area_m2"] / 1e4,
        "aoi_persistent_ha": aoi["aoi_persistent_decline_area_m2"] / 1e4,
        "aoi_decline_ratio": aoi["aoi_decline_ratio"],
        "cadastre_status": m["cadastre"]["status"],
    }
    if p is not None and len(p):
        decl_types = ["PERSISTENT_DECLINE", "TEMPORARY_DECLINE", "DECLINE_UNCONFIRMED"]
        s.update({
            "n_parcels": int(len(p)),
            "n_evaluable": int((p["change_type"] != "INSUFFICIENT_DATA").sum()),
            "n_insufficient": int((p["change_type"] == "INSUFFICIENT_DATA").sum()),
            "n_decline_candidates": int(p["change_type"].isin(decl_types).sum()),
            "n_persistent": int((p["change_type"] == "PERSISTENT_DECLINE").sum()),
            "n_temporary": int((p["change_type"] == "TEMPORARY_DECLINE").sum()),
            "n_unconfirmed": int((p["change_type"] == "DECLINE_UNCONFIRMED").sum()),
            "n_review": int(p["is_review_candidate"].sum()),
            "n_review_within_300m": int((p["is_review_candidate"] & p["within_300m"]).sum()),
            "n_with_any_decline_pixel": int((p["decline_area_m2"] > 0).sum()),
            "type_counts": p["change_type"].value_counts().to_dict(),
        })
    return s


# ----------------------------------------------------------------------------- html
def _fmt_int(v) -> str:
    return f"{int(v):,}"


def build_html(res: dict, cfg: dict, out: Path) -> Path:
    meta, years, p = res["meta"], res["years"], res["parcels"]
    tr = res["transform"]
    H, W = res["ndvi"].shape[1:]
    res_m = tr.a
    x0, y0 = tr.c, tr.f
    summ = executive_summary(res)

    parcels_js, paths = [], []
    if p is not None and len(p):
        rows = p.reset_index(drop=True)
        for i, r in rows.iterrows():
            area = r["area_in_aoi_m2"]
            parcels_js.append({
                "pnu": None if pd.isna(r["pnu"]) else str(r["pnu"]), "j": r.get("jibun_display"),
                "ad": r.get("addr"), "a": _num(area, 1), "at": _num(r["area_total_m2"], 1),
                "n": [_num(r[f"ndvi_mean_{y}"], 3) for y in years],
                "v": [_num(r[f"valid_area_{y}"] / area if area else None, 2) for y in years],
                "ev": _num(r["evaluable_area_m2"], 1), "dl": _num(r["decline_area_m2"], 1),
                "dr": _num(r["decline_ratio"], 4), "pa": _num(r["persistent_decline_area_m2"], 1),
                "ta": _num(r["temporary_decline_area_m2"], 1), "ua": _num(r["unconfirmed_decline_area_m2"], 1),
                "t": r["change_type"], "tk": r["change_type_ko"], "ps": r["persistence_status"],
                "fc": r["first_change_interval"] if isinstance(r["first_change_interval"], str) else None,
                "d": _num(r["ndvi_delta_first_last"], 3), "dd": _num(r.get("ndvi_rel_delta_first_last"), 3),
                "cf": r["analysis_confidence"], "q": r["quality_flags"], "rd": _num(r["rail_distance_m"], 1),
                "rt": bool(r.get("rail_nearest_is_tunnel", False)),
                "s": _num(r["priority_score"], 1), "s1": _num(r["score_decline_ratio"], 1),
                "s2": _num(r["score_persistence"], 1), "s3": _num(r["score_rail_proximity"], 1),
                "rk": None if pd.isna(r["priority_rank"]) else int(r["priority_rank"]), "tr": r["priority_tier"],
                "fr": _num(r["evaluable_fraction"], 3),
            })
            paths.append(_path_d(r.geometry, x0, y0))
    aoi_b = res["aoi"].total_bounds
    data = {
        "title": TITLE, "years": years, "res": res_m, "W": W * res_m, "H": H * res_m,
        "bbox": [aoi_b[0] - x0, y0 - aoi_b[3], aoi_b[2] - x0, y0 - aoi_b[1]],
        "parcels": parcels_js, "paths": paths,
        "aoi_path": "".join(_path_d(g, x0, y0) for g in res["aoi"].geometry),
        "rail_path": _lines_d(res["rail"], x0, y0),
        "ndvi_png": [_png_b64(ndvi_rgba(res["ndvi"][i])) for i in range(len(years))],
        "decl_png": _png_b64(persistence_rgba(res["persistence_class"])),
        "aoi_mean": [meta["aoi"]["summary"]["per_year_aoi_ndvi_mean"][str(y)] for y in years],
        "scenes": meta["scenes"], "type_colors": TYPE_COLORS, "type_ko": CHANGE_TYPE_KO,
        "persist_colors": PERSIST_COLORS, "summary": summ, "notice": REQUIRED_NOTICE,
        "pairs": [f"{a}→{b}" for a, b in res["pairs"]],
    }
    ctx = _context_blocks(res, summ)
    tpl = TEMPLATE.read_text(encoding="utf-8")
    html = (tpl.replace("__TITLE__", _html.escape(TITLE))
            .replace("__NOTICE__", _html.escape(REQUIRED_NOTICE))
            .replace("__STATIC_BLOCKS__", ctx)
            .replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")))
    path = out / "report" / "kr_rail_danyang_ndvi_parcel_report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _kv_table(rows: list[tuple[str, str]]) -> str:
    return "<table class='kv'>" + "".join(f"<tr><th>{_html.escape(k)}</th><td>{v}</td></tr>" for k, v in rows) + "</table>"


def _context_blocks(res: dict, s: dict) -> str:
    """서버 측에서 계산된 값만 쓰는 정적 HTML 블록(요약, 답변, 방법론)을 만든다."""
    meta = res["meta"]
    th = meta["thresholds"]
    d = th["profiles"]["default"]
    cad = meta["cadastre"]
    rail = meta["railway"]
    sens = meta["sensitivity"]
    parts: dict[str, str] = {}

    # KPI
    if "n_parcels" in s:
        k = [("분석 대상 필지(AOI 교차)", _fmt_int(s["n_parcels"]), "연속지적도 PNU 필지"),
             ("분석 가능 필지", _fmt_int(s["n_evaluable"]), f"자료 부족 {_fmt_int(s['n_insufficient'])}개 제외"),
             ("식생 감소 후보 필지", _fmt_int(s["n_decline_candidates"]), "지속·일시·지속성 미확인 합계"),
             ("지속적 식생 감소 필지", _fmt_int(s["n_persistent"]), "이후 관측에서도 감소 유지"),
             ("현장조사 우선 검토 필지", _fmt_int(s["n_review"]), f"철도 300m 이내 {_fmt_int(s['n_review_within_300m'])}개")]
    else:
        k = [("연속지적도", "미확보", cad.get("error") or "필지별 분석 미완료")]
    k += [("AOI 식생 감소 면적", f"{s['aoi_decline_area_ha']:.1f} ha",
           f"AOI 평가가능 면적의 {s['aoi_decline_ratio'] * 100:.1f}%"),
          ("지속적 감소 면적", f"{s['aoi_persistent_ha']:.1f} ha", "AOI 픽셀 기준")]
    parts["kpi"] = "".join(f"<div class='kpi'><div class='kl'>{a}</div><div class='kv2'>{b}</div><div class='ks'>{c}</div></div>"
                           for a, b, c in k)

    # Q&A
    qa_rows = []
    if "n_parcels" in s:
        p = res["parcels"]
        top = p[p["decline_area_m2"] > 0].sort_values("decline_area_m2", ascending=False).head(3)
        topl = ", ".join(f"{r.jibun_display}({r.decline_area_m2:,.0f}㎡)" for r in top.itertuples())
        topr = p[(p["evaluable_area_m2"] >= 400) & (p["decline_area_m2"] > 0)].sort_values("decline_ratio", ascending=False).head(3)
        topratio = ", ".join(f"{r.jibun_display}({r.decline_ratio * 100:.0f}%)" for r in topr.itertuples())
        rv = p[p["is_review_candidate"]].sort_values("priority_rank").head(3)
        rvl = ", ".join(f"{r.jibun_display}(점수 {r.priority_score:.0f})" for r in rv.itertuples())
        qa_rows = [
            ("Q1. 어느 필지에서 식생이 감소했는가?",
             f"감소 픽셀이 1개 이상 있는 필지 {_fmt_int(s['n_with_any_decline_pixel'])}개 중 기준(감소 면적 ≥ "
             f"{th['classification']['decline_area_min_m2']}㎡ 및 비율 ≥ {th['classification']['decline_ratio_min']*100:.0f}%)을 "
             f"넘은 후보는 {_fmt_int(s['n_decline_candidates'])}개. 감소 면적 상위: {topl}."),
            ("Q2. 감소 면적·비율이 큰 필지는?", f"비율 상위(평가가능 면적 400㎡ 이상): {topratio}. 지도의 '식생 감소 비율' 레이어와 하단 표에서 확인."),
            ("Q3. 일시적 감소와 지속적 감소를 구별할 수 있는가?",
             f"감소 이후 관측이 있는 경우 구분 가능: 지속 {_fmt_int(s['n_persistent'])}개, 일시적 감소 후 회복 {_fmt_int(s['n_temporary'])}개, "
             f"후속 관측 부족으로 지속성 미확인 {_fmt_int(s['n_unconfirmed'])}개(대부분 {years_last(res)} 관측 구간)."),
            ("Q4. 철도 주변 현장조사 우선 검토 필지는?",
             f"우선 검토 {_fmt_int(s['n_review'])}개, 상위: {rvl}. 철도 거리는 OSM 비공식 선형 기준 참고값이다."),
            ("Q5. 필지를 선택해 NDVI 이력과 근거를 확인할 수 있는가?",
             "지도에서 필지를 클릭하거나 우선 검토 표의 행을 선택하면 상세 패널에 연도별 NDVI, 시계열 차트, 점수 구성이 표시된다."),
        ]
    else:
        qa_rows = [("필지별 질의(Q1–Q5)", "연속지적도 미확보로 미완료. AOI 수준 NDVI 분석만 수행됨.")]
    parts["qa"] = "<div class='qa'>" + "".join(f"<div><b>{_html.escape(q)}</b><p>{_html.escape(a)}</p></div>" for q, a in qa_rows) + "</div>"

    # 장면 표
    sc = "".join(
        f"<tr><td>{x['year']}</td><td>{x['date']}</td><td>{x['platform']}</td><td class='mono'>{x['scene_id']}</td>"
        f"<td>{x['processing_baseline']}</td><td>{x['boa_offset_removed_dn']}</td><td>{x['scene_cloud_cover_pct']}</td>"
        f"<td>{(x['aoi_clear_fraction'] or 0) * 100:.1f}</td></tr>" for x in meta["scenes"])
    parts["scenes"] = ("<table class='grid'><thead><tr><th>연도</th><th>촬영일</th><th>위성</th><th>Scene ID</th><th>Baseline</th>"
                       "<th>제거된 BOA offset(DN)</th><th>장면 구름(%)</th><th>AOI clear(%)</th></tr></thead><tbody>" + sc + "</tbody></table>")

    # NDVI 연도별 AOI 평균 + 계절 위상 경고
    doy = meta["raster_qa"]["acquisition"]["day_of_year_by_year"]
    parts["aoi_year"] = "".join(
        f"<tr><td>{y}</td><td>{doy.get(str(y))}</td><td>{v:.3f}</td><td>{meta['aoi']['summary']['per_year_aoi_valid_fraction'][str(y)] * 100:.1f}</td></tr>"
        for y, v in meta["aoi"]["summary"]["per_year_aoi_ndvi_mean"].items())

    # 임계값
    prof = "".join(
        f"<tr><td>{n}</td><td>{t['before_min']}</td><td>{t['after_max']}</td><td>{t['delta_max']}</td><td>{t['recovery_ndvi']}</td></tr>"
        for n, t in th["profiles"].items())
    parts["profiles"] = ("<table class='grid'><thead><tr><th>기준</th><th>이전 NDVI ≥</th><th>이후 NDVI ≤</th><th>ΔNDVI ≤</th>"
                         "<th>회복 NDVI ≥</th></tr></thead><tbody>" + prof + "</tbody></table>")
    srows = ""
    for n, v in sens.items():
        pl = v.get("parcel_level") or {}
        srows += (f"<tr><td>{n}</td><td>{v['decline_pixels']:,}</td><td>{v['aoi_decline_area_m2'] / 1e4:.1f}</td>"
                  f"<td>{v['aoi_persistent_area_m2'] / 1e4:.1f}</td><td>{v.get('jaccard_vs_default', 0):.2f}</td>"
                  f"<td>{pl.get('parcels_with_decline', '-')}</td><td>{pl.get('parcels_persistent_decline', '-')}</td></tr>")
    parts["sens"] = ("<table class='grid'><thead><tr><th>기준</th><th>감소 픽셀(격자 전체)</th><th>AOI 감소 면적(ha)</th>"
                     "<th>AOI 지속 감소(ha)</th><th>default와 Jaccard</th><th>감소 픽셀 보유 필지</th><th>지속적 감소 필지</th>"
                     "</tr></thead><tbody>" + srows + "</tbody></table>")

    cq = cad.get("qa", {})
    parts["cad"] = _kv_table([
        ("상태", _html.escape(str(cad.get("status")))), ("출처", _html.escape(str(cad.get("source")))),
        ("사용 레이어", _html.escape(", ".join(cad.get("layers", [])))),
        ("파일 수집 시각", _html.escape(str(cad.get("retrieved_file_mtime") or cad.get("mtime")))),
        ("속성상 기준 시점", _html.escape(f"gosi {cad.get('gosi_attr', {}).get('year')}-{cad.get('gosi_attr', {}).get('month')} "
                                    "(공시지가 기준. 지적경계 갱신일과 동일하다고 단정 불가)")),
        ("원본 필지 수 → AOI 교차", f"{cq.get('n_raw', '-'):,} → {cq.get('n_in_aoi', '-'):,}" if cq else "-"),
        ("PNU 누락 / 형식오류 / 중복제거", f"{cq.get('pnu_missing')} / {cq.get('pnu_bad_format')} / {cq.get('duplicate_pnu_removed')}" if cq else "-"),
        ("Geometry 수선", f"{cq.get('invalid_geometry_repaired')}개" if cq else "-"),
        ("필지 간 중첩 면적", f"{cq.get('parcel_overlap_area_m2', 0):,.0f} ㎡" if cq else "-"),
        ("AOI 커버율", f"{cq.get('aoi_covered_fraction_by_parcels', 0) * 100:.3f}%" if cq else "-"),
        ("시간적 주의", _html.escape(cad.get("temporal_caveat", ""))),
    ])
    pv = meta.get("parcel_validation", {})
    parts["rail"] = _kv_table([
        ("철도 자료", _html.escape(rail["kind"])), ("파일", _html.escape(rail["path"])),
        ("사용/전체 선형 수", f"{rail['n_features_used']} / {rail['n_features_all']}"),
        ("연장", f"{rail['length_km']:.1f} km"), ("주의", _html.escape(rail["note"])),
    ])
    parts["valid"] = _kv_table([
        ("감소 면적 > 평가 면적 필지", str(pv.get("decline_area_exceeds_evaluable"))),
        ("평가 면적 > 분석 면적 필지", str(pv.get("evaluable_exceeds_analysis_area"))),
        ("픽셀 가중치 면적 합 vs 폴리곤 면적(최대 상대오차)", f"{pv.get('pixel_weight_area_vs_polygon_area_max_rel_err', 0):.2e}"),
        ("인접 연도 NDVI 상관", _html.escape(", ".join(f"{k}: {v:.2f}" for k, v in meta['raster_qa']['adjacent_year_ndvi_correlation'].items()))),
        ("원본 격자 동일성(CRS/transform/shape)", str(meta["raster_qa"]["native_grid"]["identical_native_grid"])),
        ("Red DN 0.5% 분위 편차(offset 일관성)", f"{meta['raster_qa']['offset_consistency']['red_dn_p0.5_spread']:.0f} DN (≈1000이면 의심)"),
    ])
    parts["limits"] = "<ul>" + "".join(f"<li>{_html.escape(x)}</li>" for x in meta["limitations"]) + "</ul>"
    parts["meta"] = _kv_table([
        ("분석 시각", _html.escape(meta["generated_at"])),
        ("격자", f"EPSG:5186, {meta['grid']['res_m']:g}m, {meta['grid']['shape'][1]}×{meta['grid']['shape'][0]} px"),
        ("NDVI 방법", _html.escape(meta["ndvi_method"])),
        ("실행 환경", _html.escape(f"Python {meta['environment']['python']} / {meta['environment']['platform']}")),
        ("최소 후속 관측(지속성)", str(th["min_followup_obs"])),
        ("픽셀 분석 최소 유효 관측 수", f"{th['min_valid_obs_pixel']} / {len(res['years'])}"),
    ])
    return json.dumps(parts, ensure_ascii=False).replace("</", "<\\/")


def years_last(res: dict) -> str:
    y = res["years"]
    return f"{y[-2]}→{y[-1]}"


# ----------------------------------------------------------------------------- static fallback
def build_static(res: dict, out: Path) -> list[str]:
    """인터넷/JS 없이 볼 수 있는 정적 PNG와 이를 묶은 HTML."""
    _font()
    sd = out / "report" / "static"
    sd.mkdir(parents=True, exist_ok=True)
    years, p, tr = res["years"], res["parcels"], res["transform"]
    H, W = res["ndvi"].shape[1:]
    ext = (tr.c, tr.c + W * tr.a, tr.f - H * tr.a, tr.f)
    aoi, rail = res["aoi"], res["rail"]
    files = []

    def base(ax, title):
        aoi.boundary.plot(ax=ax, color="#222", lw=1.2)
        rail.plot(ax=ax, color="#1f4e9c", lw=1.3)
        b = aoi.total_bounds
        ax.set_xlim(b[0] - 100, b[2] + 100)
        ax.set_ylim(b[1] - 100, b[3] + 100)
        ax.set_aspect("equal")
        ax.set_title(title, fontsize=11)
        ax.set_xticks([])
        ax.set_yticks([])

    fig, axes = plt.subplots(2, 3, figsize=(13, 9.5))
    for ax, i in zip(axes.ravel(), range(len(years))):
        ax.imshow(res["ndvi"][i], extent=ext, cmap="RdYlGn", vmin=0, vmax=0.9)
        base(ax, f"NDVI {years[i]} ({res['meta']['scenes'][i]['date']})")
    fig.suptitle("연도별 NDVI (Sentinel-2 10m, EPSG:5186) - 파란선: OSM 철도(비공식)", fontsize=12)
    fig.tight_layout()
    f = sd / "ndvi_by_year.png"
    fig.savefig(f, dpi=130)
    plt.close(fig)
    files.append(f)

    if p is not None and len(p):
        for col, fname, cmap, vmin, vmax, ttl in (
                ("decline_ratio", "parcel_decline_ratio.png", "Reds", 0, 0.5, "필지별 식생 감소 비율(평가가능 면적 대비)"),
                ("priority_score", "parcel_priority_score.png", "YlOrRd", 0, 100, "필지별 현장조사 검토 우선순위 점수(검토 후보만)")):
            fig, ax = plt.subplots(figsize=(7.5, 9))
            q = p.copy()
            if col == "priority_score":
                q.loc[~q["is_review_candidate"], col] = np.nan
            p[p["change_type"] == "INSUFFICIENT_DATA"].plot(ax=ax, color="#e6e8ea", lw=0)
            q[q["change_type"] != "INSUFFICIENT_DATA"].plot(ax=ax, column=col, cmap=cmap, vmin=vmin, vmax=vmax, lw=0.1,
                                                            edgecolor="#555", legend=True, missing_kwds={"color": "#f4f4f4"},
                                                            legend_kwds={"shrink": 0.6})
            base(ax, ttl + "\n(회색: 10m 영상으로 판단하기 어려운 소형·결측 필지)")
            fig.tight_layout()
            f = sd / fname
            fig.savefig(f, dpi=130)
            plt.close(fig)
            files.append(f)

        fig, ax = plt.subplots(figsize=(7.5, 9))
        for t, c in TYPE_COLORS.items():
            sub = p[p["change_type"] == t]
            if len(sub):
                sub.plot(ax=ax, color=c, lw=0.1, edgecolor="#555", label=f"{CHANGE_TYPE_KO[t]} ({len(sub)})")
        ax.legend(loc="lower left", fontsize=8, framealpha=0.9)
        base(ax, "필지별 변화 유형(식생·토지피복 패턴, 지장물 판정 아님)")
        fig.tight_layout()
        f = sd / "parcel_change_types.png"
        fig.savefig(f, dpi=130)
        plt.close(fig)
        files.append(f)

        # 상위 필지 NDVI 시계열
        top = p[p["is_review_candidate"]].sort_values("priority_rank").head(8)
        fig, ax = plt.subplots(figsize=(8, 4.8))
        for r in top.itertuples():
            ax.plot(years, [getattr(r, f"ndvi_mean_{y}") for y in years], marker="o", lw=1.4,
                    label=f"#{int(r.priority_rank)} {r.jibun_display}")
        ax.plot(years, res["meta"]["aoi"]["summary"]["per_year_aoi_ndvi_mean"].values(), "k--", lw=1.5, label="AOI 평균")
        ax.set_ylabel("NDVI(면적가중 평균)")
        ax.set_title("우선 검토 상위 필지의 NDVI 시계열")
        ax.legend(fontsize=7, ncol=2)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        f = sd / "top_parcels_ndvi_timeseries.png"
        fig.savefig(f, dpi=130)
        plt.close(fig)
        files.append(f)

    imgs = "".join(
        f"<figure><img src='data:image/png;base64,{base64.b64encode(f.read_bytes()).decode()}' style='max-width:100%'>"
        f"<figcaption>{f.name}</figcaption></figure>" for f in files)
    page = (f"<!doctype html><html lang='ko'><meta charset='utf-8'><title>{_html.escape(TITLE)} - 정적본</title>"
            "<body style='font-family:Malgun Gothic,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px'>"
            f"<h1 style='font-size:20px'>{_html.escape(TITLE)} (정적 대체본)</h1>"
            f"<p style='background:#fff4e5;padding:12px;border-left:4px solid #d97706'>{_html.escape(REQUIRED_NOTICE)}</p>"
            f"{imgs}</body></html>")
    hp = out / "report" / "kr_rail_danyang_ndvi_static_report.html"
    hp.write_text(page, encoding="utf-8")
    return [str(x) for x in files] + [str(hp)]


def build_report(res: dict, cfg: dict, out: str | Path) -> list[str]:
    out = Path(out)
    html_path = build_html(res, cfg, out)
    static = build_static(res, out)
    return [str(html_path)] + static
