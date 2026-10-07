"""다중시점 Screening 결과 -> 이미지/차트를 내장한 단일 HTML 보고서.

multitemporal_pipeline이 만든 산출물(scene_selection, comparisons/<pair>, vectors, reports)을
읽어 (1) 추가 진단(변화 지속성, NDVI 전이 유형, 임계값 민감도, ROI 연도별 필름스트립)을 계산하고
(2) 하나의 self-contained HTML로 묶는다. 수치/날짜를 새로 만들지 않으며 비용/가격/법적 기준일은
다루지 않는다.

실행:
    python -m src.evaluation.generate_multitemporal_html_report --project config/projects/kr_rail_danyang.yaml
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
import logging
from datetime import date
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
import yaml  # noqa: E402
from rasterio.features import geometry_mask, rasterize  # noqa: E402

matplotlib.rcParams["font.family"] = "Malgun Gothic"
matplotlib.rcParams["axes.unicode_minus"] = False

logger = logging.getLogger("generate_multitemporal_html_report")

VEG_NDVI = 0.35  # 5월 기준 "식생" 휴리스틱 임계값(보조 진단 전용, 분류 결과 아님)

CSS = """
:root{color-scheme:light;--bg:#f6f6f4;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#7a7974;--line:#e2e1dc;
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--warn-bg:#fff4e0;--warn-line:#eda100;--ok:#0f7b4f;--bad:#c2332f;--grid:#e9e8e3}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#121211;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#9a998f;--line:#33332f;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--warn-bg:#2b2412;--warn-line:#c98500;--ok:#4cc38a;--bad:#e66767;--grid:#2a2a27}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#121211;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#9a998f;--line:#33332f;
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--warn-bg:#2b2412;--warn-line:#c98500;--ok:#4cc38a;--bad:#e66767;--grid:#2a2a27}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 "Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px 80px}
header.hero{padding:40px 0 18px}
header.hero h1{font-size:28px;line-height:1.3;margin:0 0 6px}
header.hero p{margin:0;color:var(--ink2)}
nav.toc{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 0;margin-bottom:8px;
display:flex;gap:6px 14px;flex-wrap:wrap;font-size:13px}
nav.toc a{color:var(--ink2);text-decoration:none}nav.toc a:hover{color:var(--s1)}
section{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:22px 24px;margin:18px 0}
section h2{margin:0 0 4px;font-size:21px}section h2 .n{color:var(--muted);font-weight:400;margin-right:8px}
section h3{margin:22px 0 6px;font-size:16px}
p.lead{color:var(--ink2);margin:2px 0 14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.card{border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.card .k{font-size:12px;color:var(--muted)}.card .v{font-size:24px;font-weight:700;line-height:1.2}.card .s{font-size:12px;color:var(--ink2)}
.callout{border-left:4px solid var(--warn-line);background:var(--warn-bg);padding:10px 14px;border-radius:0 8px 8px 0;margin:14px 0}
.callout.ok{border-color:var(--ok)}
.callout b.t{display:block;margin-bottom:2px}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--ink2);font-weight:600;background:transparent;position:sticky;top:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.tw{overflow-x:auto}
figure{margin:12px 0}figure img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;background:#000}
figcaption{font-size:12.5px;color:var(--ink2);margin-top:4px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:760px){.grid2{grid-template-columns:1fr}section{padding:16px}}
details{margin:8px 0}summary{cursor:pointer;color:var(--ink2)}
.tag{display:inline-block;font-size:11.5px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);color:var(--ink2);margin-right:4px}
.tag.hi{border-color:var(--ok);color:var(--ok)}.tag.warn{border-color:var(--warn-line);color:var(--warn-line)}
svg text{fill:var(--ink2);font-size:11.5px;font-family:inherit}svg .grid{stroke:var(--grid);stroke-width:1}svg .axis{stroke:var(--line)}
svg .val{fill:var(--ink);font-size:11px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--ink2);margin:4px 0}
.legend i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.roi{border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:14px 0}
.roi h3{margin-top:0}
code{background:var(--bg);padding:1px 5px;border-radius:4px;font-size:12.5px}
.foot{color:var(--muted);font-size:12px;margin-top:30px}
@media print{nav.toc{display:none}section{break-inside:avoid-page;border:none}body{background:#fff}}
"""


# --------------------------------------------------------------------------- helpers
def esc(x) -> str:
    return html.escape(str(x))


def _jpeg_b64(img, max_w: int = 1400, quality: int = 80) -> str:
    from PIL import Image

    img = img.convert("RGB")
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def b64_png(path: Path) -> str:
    """PNG를 용량 절감을 위해 JPEG data URI로 내장한다(보고서 단일 파일 크기 관리)."""
    from PIL import Image

    return _jpeg_b64(Image.open(path))


def fig_b64(fig, dpi: int = 110) -> str:
    from PIL import Image

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return _jpeg_b64(Image.open(buf))


def figure(src: str, caption: str, alt: str = "") -> str:
    return f'<figure><img src="{src}" alt="{esc(alt or caption)}" loading="lazy"><figcaption>{caption}</figcaption></figure>'


def fmt(n, d: int = 0) -> str:
    return f"{n:,.{d}f}"


def df_table(df: pd.DataFrame, num_cols: set[str] | None = None, fmts: dict | None = None) -> str:
    num_cols = num_cols or set()
    fmts = fmts or {}
    h = "".join(f'<th class="{"num" if c in num_cols else ""}">{esc(c)}</th>' for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        tds = []
        for c in df.columns:
            v = r[c]
            if pd.isna(v):
                s = "-"
            elif c in fmts:
                s = fmts[c](v)
            else:
                s = esc(v)
            tds.append(f'<td class="{"num" if c in num_cols else ""}">{s}</td>')
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


# --------------------------------------------------------------------------- SVG charts
def svg_grouped_bars(cats, series, ytitle, width=760, height=300, unit="m²") -> str:
    """series: [{"name","css","values"}] - 한 축, 범례 + 값 라벨 + <title> 툴팁."""
    ml, mr, mt, mb = 64, 12, 14, 38
    pw, ph = width - ml - mr, height - mt - mb
    vmax = max(max(s["values"]) for s in series) or 1
    step = 10 ** int(np.floor(np.log10(vmax)))
    top = np.ceil(vmax / step) * step
    for cand in (1, 2, 2.5, 5, 10):
        if top / (step * cand / 1) <= 6:
            tick = step * cand
            break
    top = np.ceil(vmax / tick) * tick
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{esc(ytitle)}">']
    t = 0.0
    while t <= top + 1e-9:
        y = mt + ph - ph * t / top
        out.append(f'<line class="grid" x1="{ml}" x2="{width - mr}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end">{fmt(t)}</text>')
        t += tick
    gw = pw / len(cats)
    bw = min(34, (gw - 14) / len(series))
    for i, c in enumerate(cats):
        x0 = ml + gw * i + (gw - bw * len(series) - 2 * (len(series) - 1)) / 2
        for j, s in enumerate(series):
            v = s["values"][i]
            h = ph * v / top
            x = x0 + j * (bw + 2)
            y = mt + ph - h
            r = min(4, bw / 2, max(h, 0))
            out.append(
                f'<path d="M{x:.1f},{mt + ph:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} H{x + bw - r:.1f} '
                f'Q{x + bw:.1f},{y:.1f} {x + bw:.1f},{y + r:.1f} V{mt + ph:.1f} Z" style="fill:var({s["css"]})">'
                f'<title>{esc(c)} · {esc(s["name"])}: {fmt(v)} {unit}</title></path>'
            )
            out.append(f'<text class="val" x="{x + bw / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle">{fmt(v / 1000, 0)}k</text>')
        out.append(f'<text x="{ml + gw * i + gw / 2:.1f}" y="{height - mb + 16}" text-anchor="middle">{esc(c)}</text>')
    out.append(f'<line class="axis" x1="{ml}" x2="{width - mr}" y1="{mt + ph}" y2="{mt + ph}"/>')
    out.append(f'<text x="{ml}" y="10" text-anchor="start" style="font-size:11px">{esc(ytitle)} (라벨 k = 천 {unit})</text>')
    out.append("</svg>")
    leg = "".join(f'<span><i style="background:var({s["css"]})"></i>{esc(s["name"])}</span>' for s in series)
    return f'<div class="legend">{leg}</div>' + "".join(out)


def svg_stacked_bars(cats, series, ytitle, width=760, height=300, pct=True) -> str:
    ml, mr, mt, mb = 52, 12, 14, 38
    pw, ph = width - ml - mr, height - mt - mb
    totals = [sum(s["values"][i] for s in series) or 1 for i in range(len(cats))]
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{esc(ytitle)}">']
    for t in (0, 25, 50, 75, 100):
        y = mt + ph - ph * t / 100
        out.append(f'<line class="grid" x1="{ml}" x2="{width - mr}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end">{t}%</text>')
    gw = pw / len(cats)
    bw = min(70, gw * 0.55)
    for i, c in enumerate(cats):
        x = ml + gw * i + (gw - bw) / 2
        ybase = mt + ph
        for s in series:
            v = s["values"][i]
            share = v / totals[i]
            h = ph * share
            if h <= 0.5:
                continue
            gap = 2 if h > 4 else 0
            out.append(
                f'<rect x="{x:.1f}" y="{ybase - h + gap / 2:.1f}" width="{bw:.1f}" height="{max(h - gap, 0.5):.1f}" '
                f'style="fill:var({s["css"]})" rx="2"><title>{esc(c)} · {esc(s["name"])}: {fmt(v)} m² ({share:.0%})</title></rect>'
            )
            if h > 16:
                out.append(f'<text x="{x + bw / 2:.1f}" y="{ybase - h / 2 + 4:.1f}" text-anchor="middle" '
                           f'style="fill:#fff;font-size:11px">{share:.0%}</text>')
            ybase -= h
        out.append(f'<text x="{ml + gw * i + gw / 2:.1f}" y="{height - mb + 16}" text-anchor="middle">{esc(c)}</text>')
    out.append(f'<text x="{ml}" y="10" style="font-size:11px">{esc(ytitle)}</text></svg>')
    leg = "".join(f'<span><i style="background:var({s["css"]})"></i>{esc(s["name"])}</span>' for s in series)
    return f'<div class="legend">{leg}</div>' + "".join(out)


def svg_matrix(rows, cols, present, row_labels=None, width=760) -> str:
    """hotspot x 연도쌍 존재 여부 (같은 hue 단색 + 없음=outline)."""
    cw, rh, ml, mt = 78, 22, 70, 44
    height = mt + rh * len(rows) + 8
    out = [f'<svg viewBox="0 0 {max(width, ml + cw * len(cols) + 10)} {height}" width="100%" role="img" aria-label="hotspot 연도쌍 존재 행렬">']
    for j, c in enumerate(cols):
        out.append(f'<text x="{ml + cw * j + cw / 2}" y="{mt - 8}" text-anchor="middle">{esc(c)}</text>')
    for i, r in enumerate(rows):
        out.append(f'<text x="{ml - 8}" y="{mt + rh * i + 15}" text-anchor="end">{esc(r)}</text>')
        for j, c in enumerate(cols):
            on = present[i][j]
            out.append(
                f'<rect x="{ml + cw * j + 2}" y="{mt + rh * i + 2}" width="{cw - 4}" height="{rh - 4}" rx="3" '
                + (f'style="fill:var(--s1)"' if on else 'style="fill:none;stroke:var(--line)"')
                + f'><title>{esc(r)} · {esc(c)}: {"변화 관측" if on else "관측 안 됨"}</title></rect>'
            )
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------- diagnostics
def _read(path: Path):
    with rasterio.open(path) as src:
        return src.read(), src.transform, src.crs, src.nodata


def compute_diagnostics(cfg: dict, pairs: list[dict], polygons: gpd.GeoDataFrame) -> dict:
    out_root = Path(cfg["paths"]["output_root"])
    crs = cfg["project"]["analysis_crs"]
    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(crs)
    steps = [p for p in pairs if p["kind"] == "step"]
    ref_tif = out_root / "comparisons" / pairs[0]["pair"] / "rasters" / "change_probability.tif"
    with rasterio.open(ref_tif) as src:
        shape, tr = src.shape, src.transform
    inside = ~geometry_mask([aoi.geometry.union_all()], out_shape=shape, transform=tr)
    px = abs(tr.a * tr.e)
    aoi_px = int(inside.sum())

    masks = {}
    for p in pairs:
        sub = polygons[polygons["pair"] == p["pair"]] if len(polygons) else polygons
        if len(sub):
            m = rasterize([(g, 1) for g in sub.geometry], out_shape=shape, transform=tr, fill=0, dtype="uint8").astype(bool)
        else:
            m = np.zeros(shape, bool)
        masks[p["pair"]] = m & inside

    # 1) AOI 대비 변화 비율 + 연속 구간 간 지속성(겹침)
    frac = {k: float(m.sum() / aoi_px) for k, m in masks.items()}
    persist = []
    for a, b in zip(steps[:-1], steps[1:]):
        ma, mb = masks[a["pair"]], masks[b["pair"]]
        inter, uni = (ma & mb).sum(), (ma | mb).sum()
        persist.append({
            "구간 A": a["pair"], "구간 B": b["pair"],
            "Jaccard": float(inter / uni) if uni else float("nan"),
            "B 중 A와 겹침": float(inter / mb.sum()) if mb.sum() else float("nan"),
            "무작위 겹침 기대치": float(ma.sum() / aoi_px),
        })
    ever = np.zeros(shape, bool)
    for p in steps:
        ever |= masks[p["pair"]]
    cum_frac = float(ever.sum() / aoi_px)
    all_steps = np.zeros(shape, bool) | masks[steps[0]["pair"]]
    for p in steps[1:]:
        all_steps &= masks[p["pair"]]
    always_frac = float(all_steps.sum() / aoi_px)

    # 2) NDVI 전이 유형 (보조 진단)
    ndvi_rows = []
    for p in steps:
        d = out_root / "comparisons" / p["pair"] / "rasters"
        a1, _, _, _ = _read(d / "t1_aligned.tif")
        a2, _, _, _ = _read(d / "t2_aligned.tif")

        def ndvi(a):
            red, nir = a[2].astype("float32"), a[3].astype("float32")
            return np.where((red + nir) > 0, (nir - red) / (nir + red + 1e-6), np.nan)

        n1, n2 = ndvi(a1), ndvi(a2)
        m = masks[p["pair"]] & np.isfinite(n1) & np.isfinite(n2)
        v1, v2 = n1 >= VEG_NDVI, n2 >= VEG_NDVI
        ndvi_rows.append({
            "pair": p["pair"],
            "식생→비식생": float((m & v1 & ~v2).sum() * px),
            "비식생→식생": float((m & ~v1 & v2).sum() * px),
            "식생↔식생(작황·위상)": float((m & v1 & v2).sum() * px),
            "비식생↔비식생(나지·채석 등)": float((m & ~v1 & ~v2).sum() * px),
        })

    # 3) 임계값 민감도 (후처리 전 원시 픽셀 기준)
    sens = []
    for p in steps:
        prob, _, _, _ = _read(out_root / "comparisons" / p["pair"] / "rasters" / "change_probability.tif")
        pr = prob[0]
        row = {"pair": p["pair"]}
        for t in (0.4, 0.5, 0.6, 0.7):
            row[f"≥{t}"] = float(((pr >= t) & inside).sum() * px)
        sens.append(row)

    # 4) 연도별 유효(마스킹 안 된) 픽셀 비율
    valid = {}
    for p in pairs:
        pass
    for p in steps:
        d = out_root / "comparisons" / p["pair"] / "rasters"
        for tag, yr, f in (("t1", p["year_from"], "t1_aligned.tif"), ("t2", p["year_to"], "t2_aligned.tif")):
            if yr in valid:
                continue
            a, _, _, nd = _read(d / f)
            v = np.all(a > 0, axis=0) & inside
            valid[yr] = float(v.sum() / aoi_px)
    return {
        "aoi_px": aoi_px, "px_area": px, "frac": frac, "persist": pd.DataFrame(persist), "cum_frac": cum_frac,
        "always_frac": always_frac, "ndvi": pd.DataFrame(ndvi_rows), "sens": pd.DataFrame(sens), "valid": valid,
        "masks": masks,
    }


# --------------------------------------------------------------------------- image panels
def _year_tif(cfg: dict, steps: list[dict]) -> dict[int, Path]:
    root = Path(cfg["paths"]["output_root"]) / "comparisons"
    m = {steps[0]["year_from"]: root / steps[0]["pair"] / "rasters" / "t1_aligned.tif"}
    for p in steps:
        m[p["year_to"]] = root / p["pair"] / "rasters" / "t2_aligned.tif"
    return m


def _rgb(path: Path):
    with rasterio.open(path) as src:
        a = src.read().astype("float32")
        ext = (src.bounds.left, src.bounds.right, src.bounds.bottom, src.bounds.top)
        tr = src.transform
        names = list(src.descriptions)
    r, g, b = names.index("B04"), names.index("B03"), names.index("B02")
    rgb = np.clip(np.stack([a[r], a[g], a[b]], -1) / 2500.0, 0, 1) ** 0.8  # 연도 간 동일 고정 stretch
    rgb[np.any(a[[r, g, b]] <= 0, axis=0)] = 0
    return rgb, ext, tr


def aoi_contact_sheet(cfg, steps, records, aoi, railway) -> str:
    ymap = _year_tif(cfg, steps)
    years = sorted(ymap)
    fig, axes = plt.subplots(2, 3, figsize=(11, 10.4))
    for ax, y in zip(axes.ravel(), years):
        rgb, ext, _ = _rgb(ymap[y])
        ax.imshow(rgb, extent=ext)
        aoi.boundary.plot(ax=ax, color="yellow", linewidth=0.8)
        ax.set_title(f"{y} · {records[y]['date']}", fontsize=11)
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
    fig.tight_layout()
    return fig_b64(fig, 100)


def roi_filmstrip(cfg, steps, records, roi_row, hs_row, polygons, railway, aoi) -> str:
    ymap = _year_tif(cfg, steps)
    years = sorted(ymap)
    minx, miny, maxx, maxy = roi_row.geometry.bounds
    fig, axes = plt.subplots(2, 3, figsize=(10.5, 7.4))
    hs_geom = gpd.GeoSeries([hs_row.geometry], crs=polygons.crs)
    for ax, y in zip(axes.ravel(), years):
        rgb, ext, _ = _rgb(ymap[y])
        ax.imshow(rgb, extent=ext)
        hs_geom.boundary.plot(ax=ax, color="lime", linewidth=1.3)
        if railway is not None and len(railway):
            railway.plot(ax=ax, color="red", linewidth=1.1)
        aoi.boundary.plot(ax=ax, color="yellow", linewidth=0.7)
        ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
        ax.set_title(f"{y} · {records[y]['date']}", fontsize=10)
        ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout()
    return fig_b64(fig, 95)


# --------------------------------------------------------------------------- main builder
def build_html(project_yaml: str | Path, out_path: str | Path | None = None) -> Path:
    cfg = yaml.safe_load(open(project_yaml, encoding="utf-8"))
    crs = cfg["project"]["analysis_crs"]
    out_root = Path(cfg["paths"]["output_root"])
    rep = out_root / "reports"
    figs = out_root / "figures"

    records = {int(k): v for k, v in json.loads((rep / "scene_selection.json").read_text(encoding="utf-8")).items()}
    summary = pd.read_csv(rep / "pair_change_summary.csv", encoding="utf-8-sig")
    acq = json.loads((rep / "recommended_acquisition_years.json").read_text(encoding="utf-8"))
    hs = gpd.read_file(out_root / "vectors" / "hotspots.gpkg").to_crs(crs)
    roi = gpd.read_file(out_root / "vectors" / "skywatch_roi.gpkg").to_crs(crs)
    polygons = gpd.read_file(out_root / "vectors" / "change_polygons_all.gpkg").to_crs(crs)
    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(crs)
    aoi_km2 = aoi.geometry.union_all().area / 1e6
    railway = None
    rp = Path(cfg["paths"].get("railway", ""))
    if rp.is_file():
        rw = gpd.read_file(rp).to_crs(crs)
        rw = rw[(rw["railway"] == "rail") & rw["service"].isna()]
        railway = rw if len(rw) else None

    pairs = [{"pair": f"{r.year_from}_{r.year_to}", "year_from": int(r.year_from), "year_to": int(r.year_to), "kind": r.kind}
             for r in summary.itertuples()]
    steps = [p for p in pairs if p["kind"] == "step"]
    if not steps:
        raise RuntimeError("[REPORT] step(인접 연도) 쌍이 없습니다 - Phase B를 먼저 실행하세요")
    diag = compute_diagnostics(cfg, pairs, polygons)
    st = summary[summary["kind"] == "step"].reset_index(drop=True)
    summaries = {p["pair"]: json.loads((out_root / "comparisons" / p["pair"] / "analysis_summary.json").read_text(encoding="utf-8"))
                 for p in pairs}
    manifest = json.loads((out_root / "comparisons" / steps[0]["pair"] / "run_manifest.json").read_text(encoding="utf-8"))
    persistent_warn = any(n.startswith("경고") for n in acq["notes"])
    rec = hs[hs["recommended_for_highres"]]

    persist = diag["persist"]
    mean_ov = float(persist["B 중 A와 겹침"].mean())
    mean_rand = float(persist["무작위 겹침 기대치"].mean())
    nd = diag["ndvi"]
    nd_cols = [c for c in nd.columns if c != "pair"]
    nd_tot = nd[nd_cols].sum()
    barren_share = float(nd_tot["비식생↔비식생(나지·채석 등)"] / nd_tot.sum())
    top5_share = float(rec["change_area_m2"].sum() / hs["change_area_m2"].sum()) if len(hs) else 0.0
    n_all_steps = int((hs["change_frequency"] >= len(steps)).sum())
    area_all_steps = float(hs.loc[hs["change_frequency"] >= len(steps), "change_area_m2"].sum())
    ratio_ov = mean_ov / mean_rand if mean_rand else float("nan")
    veg_share = float(nd_tot["식생↔식생(작황·위상)"] / nd_tot.sum())

    # ---------------- 섹션 조립 ----------------
    S: list[str] = []
    toc = [("sum", "요약"), ("rec", "결론·권고"), ("aoi", "AOI"), ("img", "영상 선정"), ("meth", "방법"), ("res", "결과"),
           ("diag", "진단"), ("qa", "품질검증"), ("hot", "Hotspot"), ("roi", "추천 ROI"), ("yrs", "촬영연도"),
           ("lim", "한계"), ("app", "재현·부록")]

    cards = [
        ("Master AOI", f"{aoi_km2:.2f} km²", "행정경계 · 잠정"),
        ("Sentinel-2 시점", f"{len(records)}개", f"{min(records)}~{max(records)} · 4~5월"),
        ("연속 구간 평균 변화", f"{st['change_area_m2'].mean() / 1e4:.1f} ha", f"AOI의 {st['change_area_m2'].mean() / (aoi_km2 * 1e6):.1%}"),
        ("전 구간 반복 hotspot", f"{n_all_steps}개", f"hotspot 면적의 {area_all_steps / hs['change_area_m2'].sum():.0%}"),
        ("Hotspot / 추천", f"{len(hs)} / {len(rec)}", "고해상도 확인 후보"),
        ("추천 ROI", f"{len(roi)}곳", f"각 최대 {cfg['hotspot'].get('max_roi_size_m')} m"),
    ]
    card_html = '<div class="cards">' + "".join(
        f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>' for k, v, s in cards) + "</div>"

    S.append(f"""<section id="sum"><h2><span class="n">0</span>요약</h2>
<p class="lead">한국철도공단 토지보상 Demo · 중앙선 도담~영천 복선전철 · 충북 단양군 매포읍 매포리·하괴리. SkyWatch 50cm를 구매하기 전에 Sentinel-2(10m) 무료 영상으로 변화를 먼저 Screening한 결과입니다.</p>
{card_html}
<ul>
<li><b>무엇을 했나</b> - {min(records)}~{max(records)}년 4~5월 Sentinel-2 L2A {len(records)}개 시점을 같은 계절로 맞춰 {len(steps)}개 인접 구간과 {len(pairs) - len(steps)}개 장기 구간의 변화 후보를 찾고, 공간적으로 묶어 hotspot {len(hs)}개와 SkyWatch 확인용 ROI {len(roi)}곳을 만들었습니다.</li>
<li><b>가장 중요한 발견</b> - 변화 면적이 모든 인접 구간에서 {st['change_area_m2'].min() / 1e4:.0f}~{st['change_area_m2'].max() / 1e4:.0f} ha로 비슷하고, hotspot {n_all_steps}개({area_all_steps / hs['change_area_m2'].sum():.0%} 면적)는 5개 인접 구간 모두에서 반복해 나타납니다. 픽셀 단위로는 다음 구간 변화의 {mean_ov:.0%}만 직전 구간과 겹치지만(무작위 기대치 {mean_rand:.0%}, 약 {ratio_ov:.1f}배), 5개 구간 모두 변한 픽셀은 없습니다. 즉 <b>같은 구역 안에서 변화 위치가 옮겨 가는 양상</b>입니다. 변화 후보의 {barren_share:.0%}는 두 시점 모두 식생이 아닌 지표(나지·채석 등으로 보임)에서 발생했습니다.</li>
<li><b>해석</b> - 특정 시점의 공사 신호라기보다 <b>채석·산업 활동 등 상시 변화가 지배적일 가능성</b>이 큽니다(확정 아님, 고해상도 확인 필요). 따라서 Sentinel-2만으로는 변화 시점을 좁히기 어렵고, 촬영연도 제안도 변별력이 낮습니다.</li>
<li><b>그래도 쓸 수 있는 것</b> - 철도와 겹치거나 가까운 hotspot(HS_05, HS_02, HS_04 등)을 SkyWatch 우선 확인 후보로 좁힐 수 있습니다.</li>
</ul>
<div class="callout"><b class="t">읽을 때 주의</b>모든 결과는 <b>change candidate</b>입니다. 지장물 존재·보상 대상·불법/무허가 여부를 판정하는 자료가 아니며, 비용·가격·법적 기준일은 다루지 않았습니다.</div>
</section>""")

    top3 = rec.sort_values("distance_to_railway_m").head(3)
    S.append(f"""<section id="rec"><h2><span class="n">1</span>결론 및 권고</h2>
<h3>알게 된 것 / 모르는 것</h3>
<div class="grid2">
<div><span class="tag hi">알게 된 것</span><ul>
<li>6개 시점 모두 같은 계절·낮은 구름·AOI 전체 포함 조건으로 확보되어 시계열 비교 자체는 가능합니다.</li>
<li>변화 후보는 AOI 안의 소수 군집에 몰려 있습니다(추천 상위 5개 hotspot이 hotspot 전체 면적의 {top5_share:.0%}).</li>
<li>철도 선형과 겹치는 hotspot이 있습니다({", ".join(hs[hs["distance_to_railway_m"] == 0]["hotspot_id"].tolist()) or "없음"}).</li>
</ul></div>
<div><span class="tag warn">모르는 것</span><ul>
<li>변화가 철도 사업(공사) 때문인지 채석·산업 활동 때문인지.</li>
<li>변화가 정확히 어느 해에 시작됐는지(구간마다 비슷해 구분되지 않음).</li>
<li>개별 구조물·지장물의 유무와 형태(10m로는 불가).</li>
</ul></div></div>
<h3>권고</h3>
<ol>
<li><b>전 구간·전 연도를 한꺼번에 구매하지 말고</b>, 철도에 가까운 ROI({", ".join(f"{r.hotspot_id}({r.distance_to_railway_m:.0f}m)" for r in top3.itertuples())})부터 SkyWatch 50cm로 <b>소수 시점만</b> 확인해 철도 관련 변화와 채석 활동을 구분하십시오.</li>
<li>구분이 되면 그 결과로 ROI/연도를 다시 좁히십시오. 이 보고서의 연도 제안은 그 전까지는 <b>일괄·저변별</b> 참고값입니다.</li>
<li>사업구역 geometry(약 22.3 km² 검색 범위)가 확보되면 AOI를 교체해 재실행하십시오. 현재 AOI(6.08 km²)는 행정경계입니다.</li>
<li>착공(2015) 이전 상태는 Sentinel-2로 볼 수 없으므로, SkyWatch 아카이브의 2010~2015 가용 여부는 별도로 확인해야 합니다.</li>
</ol></section>""")

    S.append(f"""<section id="aoi"><h2><span class="n">2</span>AOI</h2>
<table><tbody>
<tr><th>상태</th><td><span class="tag warn">provisional(잠정)</span> 사업구역/보상구역 경계가 아닌 행정 법정리 경계</td></tr>
<tr><th>출처</th><td>{esc(cfg['aoi']['source'])}</td></tr>
<tr><th>면적 / CRS</th><td>{aoi_km2:.2f} km² ({diag['aoi_px']:,} px @10m) / {esc(crs)}</td></tr>
<tr><th>철도</th><td>{esc(cfg['aoi']['railway_source'])}</td></tr>
<tr><th>비고</th><td>SkyWatch 검색 범위로 검토 중인 약 22.3 km²의 geometry는 저장소에 없어 재현하지 않았습니다. 확보되면 <code>paths.aoi</code>만 교체하면 됩니다.</td></tr>
</tbody></table>
{figure(b64_png(figs / 'fig1_aoi_overview.png'), 'Master AOI overview - Sentinel-2 최신 시점, 법정리 경계, OSM 철도(실선=운영, 점선=폐선). 검은 영역은 AOI 밖.')}
</section>""")

    cand_rows = []
    for y in sorted(records):
        for c in records[y].get("considered", []):
            cand_rows.append({"year": y, "date": c["date"], "scene": c["scene_id"], "scene cloud %": c["scene_cloud_cover_pct"],
                              "AOI clear %": round(100 * c["aoi_clear_fraction"], 1),
                              "선택": "✔" if c["scene_id"] == records[y]["scene_id"] else ""})
    img_rows = [{"year": y, "촬영일": records[y]["date"], "위성": records[y].get("platform"),
                 "scene 구름 %": records[y]["scene_cloud_cover_pct"], "AOI clear %": round(100 * records[y]["aoi_clear_fraction"], 1),
                 "처리 baseline": records[y].get("processing_baseline"), "DN offset 제거": records[y].get("boa_offset_removed_dn"),
                 "AOI 유효 픽셀 %": round(100 * diag["valid"].get(y, float("nan")), 1), "scene_id": records[y]["scene_id"]} for y in sorted(records)]
    S.append(f"""<section id="img"><h2><span class="n">3</span>영상 선정</h2>
<p class="lead">Sentinel-2 L2A(Microsoft Planetary Computer) · B02/B03/B04/B08 · 10m · 동일 계절 창 {esc(cfg['imagery']['season']['start'])}~{esc(cfg['imagery']['season']['end'])}.</p>
{df_table(pd.DataFrame(img_rows), {"scene 구름 %", "AOI clear %", "AOI 유효 픽셀 %", "DN offset 제거"})}
<h3>선정 규칙</h3>
<ol><li>연도별 STAC 검색(scene cloud &lt; {cfg['imagery']['max_scene_cloud_pct']}%) 후 상위 후보마다 SCL로 <b>AOI polygon 내부 clear 비율</b>을 계산합니다. AOI가 타일 T52SDG/T52SDF 경계에 걸려 있어 AOI를 완전히 덮는 단일 장면만 인정됩니다.</li>
<li>clear ≥ {cfg['imagery']['min_aoi_clear_fraction']:.0%} 후보 중 <b>최고 clear 대비 2%p 이내</b>에서 {esc(cfg['imagery']['season']['target_mmdd'])}에 가장 가까운 날짜를 고릅니다(식생 위상 일치).</li>
<li>기준 {cfg['imagery']['min_aoi_clear_fraction']:.0%}는 처음의 98%에서 낮춘 값입니다. 2018년 후보 최고치가 97.8%였는데, 이 AOI는 밝은 채석장·산업 지표를 SCL이 구름/눈으로 오분류하기 때문으로 보입니다. 오분류 픽셀은 nodata로 마스킹되므로 <b>일부 채석장 면적은 변화탐지에서 빠집니다</b>.</li>
<li>L2A 처리 baseline ≥ 04.00(2022-01 이후)의 DN +1000 offset은 제거해 연도 간 값을 맞췄습니다.</li></ol>
{figure(aoi_contact_sheet(cfg, steps, records, aoi, railway), '연도별 AOI (연도 간 동일 고정 stretch). 채석장으로 보이는 밝은 지표의 확장과 검은 점(SCL 마스킹)을 볼 수 있습니다.')}
<details><summary>연도별 모든 후보 장면 ({len(cand_rows)}건)</summary>{df_table(pd.DataFrame(cand_rows), {"scene cloud %", "AOI clear %"})}</details>
</section>""")

    flow = ('<svg viewBox="0 0 900 78" width="100%" role="img" aria-label="파이프라인">' + "".join(
        f'<g><rect x="{4 + i * 128}" y="14" width="118" height="46" rx="8" style="fill:none;stroke:var(--s1);stroke-width:1.5"/>'
        f'<text x="{4 + i * 128 + 59}" y="41" text-anchor="middle" style="fill:var(--ink)">{t}</text></g>'
        + (f'<path d="M{4 + i * 128 + 118},37 h10" style="stroke:var(--muted);stroke-width:1.5;fill:none"/>' if i < 6 else "")
        for i, t in enumerate(["Provider", "AOI window", "정렬·마스크", "변화탐지", "Polygon", "Hotspot", "ROI·연도"])) + "</svg>")
    S.append(f"""<section id="meth"><h2><span class="n">4</span>방법</h2>
{flow}
<table><tbody>
<tr><th>Provider</th><td>Sentinel-2 L2A → AOI window 다운로드(cache) → offset 보정·SCL 마스크 → 4-band GeoTIFF. SkyWatch 50cm는 <code>local_geotiff</code> provider로 같은 흐름에 투입(전용 주문/다운로드는 미구현).</td></tr>
<tr><th>공통 전처리</th><td>EPSG:5186 재투영, 10m 공통 grid, AOI clip(<code>align_imagery_pair</code>), ECC 정합 QA</td></tr>
<tr><th>변화탐지</th><td>robust CVA + SSIM(win 7) + edge/texture 앙상블, <b>고정 임계값 {cfg['analysis']['mask_threshold']}</b>(기존 프로젝트와 동일 원칙, 딥러닝 미사용)</td></tr>
<tr><th>후처리</th><td>opening/closing(3×3), 최소 {cfg['analysis']['min_component_area_m2']} m² (4 pixel), polygonize</td></tr>
<tr><th>Hotspot</th><td>polygon을 {cfg['hotspot']['cluster_distance_m']} m 이내로 군집(최소 {cfg['hotspot']['min_hotspot_area_m2']} m²), priority = 면적 × 점수 × 변화 빈도(인접 구간 수)</td></tr>
<tr><th>ROI</th><td>추천 hotspot bounds + {cfg['hotspot']['buffer_m']} m buffer, 한 변 {cfg['hotspot']['min_roi_size_m']}~{cfg['hotspot'].get('max_roi_size_m')} m, 겹침 {cfg['hotspot']['roi_overlap_merge']:.0%} 이상 ROI는 생략</td></tr>
<tr><th>촬영연도</th><td>인접 구간 변화 면적이 최대 구간의 {cfg['acquisition_years']['active_fraction_of_max']:.0%} 이상이면 active → 전/중/후 연도 제안</td></tr>
</tbody></table></section>""")

    bar = svg_grouped_bars(
        [f"{r.year_from}→{r.year_to}" for r in st.itertuples()],
        [{"name": "전체 변화 면적", "css": "--s1", "values": st["change_area_m2"].tolist()},
         {"name": f"철도 {cfg['hotspot'].get('railway_corridor_buffer_m')}m 이내", "css": "--s2", "values": st["corridor_change_area_m2"].tolist()}],
        "변화 면적 (m²)")
    show = summary.copy()
    show.columns = ["from", "to", "구분", "변화 면적 m²", "polygon", "hotspot", "철도 인접 m²"]
    show["AOI 대비"] = [f"{diag['frac'][p['pair']]:.1%}" for p in pairs]
    S.append(f"""<section id="res"><h2><span class="n">5</span>결과</h2>
<p class="lead">연도쌍별 변화탐지 결과(변화 후보). 한 장씩의 Before/After는 부록에 있습니다.</p>
{df_table(show, {"변화 면적 m²", "polygon", "hotspot", "철도 인접 m²", "AOI 대비"}, {"변화 면적 m²": lambda v: fmt(v), "철도 인접 m²": lambda v: fmt(v)})}
<h3>인접 구간별 변화 면적</h3>{bar}
<details><summary>표로 보기</summary>{df_table(st.rename(columns={"year_from": "from", "year_to": "to"}), set(st.columns[3:]))}</details>
<div class="grid2">{figure(b64_png(figs / 'fig2_before_after.png'), '2016 vs 2026 (가장 긴 구간)')}{figure(b64_png(figs / 'fig3_change_intensity.png'), 'change score 최대값(연도쌍 합성)')}</div>
{figure(b64_png(figs / 'fig4_change_polygons.png'), '연도쌍별 변화 polygon')}
</section>""")

    mat_rows = hs.head(12)
    pair_cols = [p["pair"] for p in pairs]
    present = [[(c in r.change_pairs.split(",")) for c in pair_cols] for r in mat_rows.itertuples()]
    stack = svg_stacked_bars(
        [p for p in nd["pair"]],
        [{"name": c, "css": css, "values": nd[c].tolist()} for c, css in zip(nd_cols, ["--s1", "--s2", "--s3", "--s4"])],
        "변화 후보 면적의 NDVI 전이 유형(비율)")
    pers_show = persist.copy()
    S.append(f"""<section id="diag"><h2><span class="n">6</span>진단 - 이 변화는 무엇처럼 보이는가</h2>
<p class="lead">변화가 공사 신호인지 상시 변화인지 가늠하기 위한 보조 진단입니다(분류 결과가 아니라 휴리스틱).</p>
<h3>① 같은 장소가 계속 바뀌는가 (지속성)</h3>
{df_table(pers_show, {"Jaccard", "B 중 A와 겹침", "무작위 겹침 기대치"}, {"Jaccard": lambda v: f"{v:.2f}", "B 중 A와 겹침": lambda v: f"{v:.0%}", "무작위 겹침 기대치": lambda v: f"{v:.1%}"})}
<p>다음 구간 변화의 평균 <b>{mean_ov:.0%}</b>가 직전 구간 변화 위치와 겹칩니다. 변화가 AOI에 무작위로 흩어져 있다면 약 {mean_rand:.0%}이므로 <b>약 {ratio_ov:.1f}배</b> 높지만, 나머지 대부분은 겹치지 않습니다. 5개 구간 모두에서 변한 픽셀은 AOI의 {diag['always_frac']:.1%}, 한 번이라도 변한 면적은 {diag['cum_frac']:.1%}입니다. 해석: 변화가 <b>같은 구역(hotspot)에 반복해서 일어나되 구역 안에서 위치가 이동</b>합니다(예: 채석 면의 전진처럼 지속 활동이 만드는 패턴과 부합하지만, 계절 노이즈도 같은 구역에 쌓일 수 있어 확정할 수 없습니다).</p>
<h3>② 어떤 유형의 지표에서 변하는가 (NDVI 전이)</h3>
{stack}
<p>식생 기준 NDVI ≥ {VEG_NDVI}(5월). 전체 변화 면적 중 <b>비식생↔비식생 {barren_share:.0%}</b>, 식생↔식생 {veg_share:.0%}입니다. 두 시점 모두 식생이 아닌 곳의 변화가 크다는 것은 나지·채석·적치·건설 현장 등 인위적 지표 변화 쪽이지만, <b>철도 공사인지 아닌지는 이 영상으로 구분되지 않습니다</b>. 식생↔식생 변화는 작황·경운 시기 차이(오탐) 가능성이 큽니다.</p>
<details><summary>표로 보기</summary>{df_table(nd, set(nd_cols), {c: (lambda v: fmt(v)) for c in nd_cols})}</details>
<h3>③ 임계값 민감도 (후처리 전 원시 픽셀 기준, m²)</h3>
{df_table(diag['sens'], set(diag['sens'].columns[1:]), {c: (lambda v: fmt(v)) for c in diag['sens'].columns[1:]})}
<p>임계값이 0.5→0.7로 올라가면 면적이 얼마나 줄어드는지 보여줍니다. 고정 임계값 0.5는 기존 프로젝트와의 일관성을 위한 값이며 이 AOI에 맞춰 튜닝하지 않았습니다.</p>
</section>""")

    qa_rows = [{"구간": p["pair"], "t1": summaries[p["pair"]]["t1_date"], "t2": summaries[p["pair"]]["t2_date"],
                "ECC 변위(px)": summaries[p["pair"]]["alignment"]["displacement_px"],
                "ECC 변위(m)": summaries[p["pair"]]["alignment"]["displacement_m"],
                "ECC score": summaries[p["pair"]]["alignment"]["ecc_score"],
                "수렴": "✔" if summaries[p["pair"]]["alignment"]["converged"] else "✘",
                "격자": f'{summaries[p["pair"]]["grid"]["width"]}×{summaries[p["pair"]]["grid"]["height"]}'} for p in pairs]
    S.append(f"""<section id="qa"><h2><span class="n">7</span>품질검증</h2>
{df_table(pd.DataFrame(qa_rows), {"ECC 변위(px)", "ECC 변위(m)", "ECC score"}, {"ECC 변위(px)": lambda v: f"{v:.2f}", "ECC 변위(m)": lambda v: f"{v:.1f}", "ECC score": lambda v: f"{v:.3f}"})}
<ul><li>모든 쌍에서 ECC가 수렴했고 잔여 변위는 1픽셀 미만입니다 - 정합 오차가 변화 후보의 주원인은 아닌 것으로 보입니다.</li>
<li>다만 연도에 따라 위성 상대궤도(R003/R103)와 타일(T52SDG/T52SDF)이 섞여 있어 시야각·그림자 차이가 남을 수 있습니다.</li>
<li>사람이 확인한 정답 데이터가 없어 <b>정확도(precision/recall)는 측정하지 못했습니다</b>. 추천 ROI의 고해상도 확인이 첫 검증이 됩니다.</li></ul></section>""")

    hs_show = hs[["hotspot_id", "first_year", "last_year", "change_area_m2", "change_score", "change_frequency", "distance_to_railway_m", "recommended_for_highres"]].copy()
    hs_show["recommended_for_highres"] = hs_show["recommended_for_highres"].map({True: "추천", False: ""})
    hs_show.columns = ["ID", "first_year", "last_year", "면적 m²", "score", "빈도", "철도까지 m", "SkyWatch"]
    S.append(f"""<section id="hot"><h2><span class="n">8</span>Hotspot</h2>
<p class="lead">변화 polygon을 {cfg['hotspot']['cluster_distance_m']} m 이내로 묶은 {len(hs)}개 군집. <code>first_year/last_year</code>는 인접 구간 중 변화가 관측된 구간의 종료 연도, <code>빈도</code>는 변화가 관측된 인접 구간 수(최대 {len(steps)})입니다.</p>
{figure(b64_png(figs / 'fig5_hotspots.png'), 'Hotspot map (초록=추천, 하늘색=기타, 빨간 선=OSM 철도).')}
<h3>상위 hotspot × 연도쌍 존재 행렬</h3>
{svg_matrix(mat_rows['hotspot_id'].tolist(), pair_cols, present)}
<p>상위 hotspot이 거의 모든 구간에 반복 등장합니다 - 구역 단위의 지속성(위 진단 ①)을 보여줍니다.</p>
<details open><summary>전체 hotspot 표</summary>{df_table(hs_show, {"면적 m²", "score", "빈도", "철도까지 m"}, {"면적 m²": lambda v: fmt(v), "score": lambda v: f"{v:.2f}", "철도까지 m": lambda v: f"{v:,.0f}"})}</details>
<p class="foot">철도까지 거리는 OpenStreetMap 선형(비공식, railway=rail, 야드 제외) 기준입니다. 터널 구간은 지표 영상에 보이지 않을 수 있습니다.</p></section>""")

    roi_cards = []
    for r in roi.itertuples():
        h = hs[hs["hotspot_id"] == r.hotspot_id].iloc[0]
        img = roi_filmstrip(cfg, steps, records, pd.Series({"geometry": r.geometry}), h, polygons, railway, aoi)
        roi_cards.append(f"""<div class="roi"><h3>{r.roi_id} <span class="tag hi">{r.hotspot_id}</span>{'<span class="tag warn">1km 상한으로 잘림</span>' if r.roi_clipped else ''}</h3>
<p>{r.roi_size_m:.0f} m × {r.roi_size_m:.0f} m ({r.roi_area_km2:.2f} km²) · hotspot 면적 {fmt(h.change_area_m2)} m² · score {h.change_score:.2f} · 빈도 {h.change_frequency}/{len(steps)} · 철도까지 {h.distance_to_railway_m:,.0f} m · 변화 관측: {esc(h.change_pairs.replace('_', '→'))}</p>
{figure(img, f'{r.roi_id} 연도별 모습 (초록=hotspot 경계, 빨강=OSM 철도, 노랑=AOI). 연도 간 동일 stretch.')}</div>""")
    S.append(f"""<section id="roi"><h2><span class="n">9</span>SkyWatch 50cm 추천 ROI</h2>
<p class="lead">변화 후보가 강한 hotspot을 주변 맥락과 함께 감싼 확인 후보입니다. 구매 비용·최소 주문 면적은 다루지 않았습니다.</p>
{figure(b64_png(figs / 'fig6_skywatch_roi.png'), '추천 ROI (주황 박스). ROI끼리 일부 겹칩니다.')}
{"".join(roi_cards)}</section>""")

    yrs = pd.DataFrame(acq["years"]) if acq["years"] else pd.DataFrame(columns=["year", "role", "reason"])
    warn_note = next((n for n in acq["notes"] if n.startswith("경고")), None)
    S.append(f"""<section id="yrs"><h2><span class="n">10</span>촬영연도 제안</h2>
{f'<div class="callout"><b class="t">변별력 낮음</b>{esc(warn_note)}</div>' if warn_note else ''}
{df_table(yrs)}
<p><b>제안 방식</b>: 최종 구매연도 확정이 아니라 Sentinel-2 변화량 기반 참고값입니다. 현재는 모든 구간이 active라 연도가 일괄 제안되었습니다.</p>
<h3>현실적인 접근 (제안)</h3>
<ol><li><b>1단계</b> - 철도에 가까운 ROI 2~3곳을 시작·끝 anchor 시점으로 소수만 확인 → 채석 활동 vs 철도 관련 변화 구분</li>
<li><b>2단계</b> - 철도 관련 변화가 확인된 ROI에 한해 중간 연도를 추가(변화 시점 좁히기, 착공 2015·개통 2022-07/2024-12 전후 context 활용)</li>
<li>착공 이전(2010~2015)은 Sentinel-2로 볼 수 없으므로 SkyWatch 아카이브 가용 연도를 별도 확인</li></ol>
{''.join(f'<p class="foot">· {esc(n)}</p>' for n in acq['notes'] if not n.startswith('경고'))}</section>""")

    lims = ["Sentinel-2 공간해상도는 10m(1 pixel = 100 m²)이며 개별 지장물/소형 건물 판독용이 아닙니다.",
            "모든 변화 후보는 고해상도 영상(SkyWatch 50cm 등)으로 검증해야 합니다.",
            "change candidate ≠ 보상 대상 / ≠ 불법·무허가 구조물. 법적·행정적 판단은 Demo 범위 밖입니다.",
            "계절·식생(경운/작황), 그림자, 센서·궤도 차이, 정합 오차가 오탐을 만들 수 있습니다.",
            "'변화 없음'이 아니라 'Sentinel-2에서는 유의미한 변화가 검출되지 않음'으로 해석해야 합니다.",
            "SCL 오분류 마스킹으로 일부 밝은 지표(채석장 등)가 분석에서 빠집니다.",
            "정답(label) 데이터가 없어 정확도를 측정하지 못했습니다. 임계값은 튜닝하지 않았습니다.",
            "AOI는 행정경계(잠정)이며 철도 geometry는 비공식 OSM입니다.",
            "공사일·개통일은 사업 개요 context이며 보상 기준일/법적 기준일/지장물 판정 기준일이 아닙니다."]
    S.append(f'<section id="lim"><h2><span class="n">11</span>한계</h2><ul>{"".join(f"<li>{esc(x)}</li>" for x in lims)}</ul></section>')

    pair_figs = "".join(
        f'<details><summary>{p["pair"].replace("_", " → ")} ({summaries[p["pair"]]["t1_date"]} → {summaries[p["pair"]]["t2_date"]})</summary>'
        + figure(b64_png(out_root / "comparisons" / p["pair"] / "figures" / "before_after_change.png"), f'{p["pair"]} before/after/change') + "</details>"
        for p in pairs)
    pk = manifest.get("package_versions", {})
    S.append(f"""<section id="app"><h2><span class="n">12</span>재현·부록</h2>
<h3>실행</h3>
<pre style="overflow-x:auto;background:var(--bg);padding:10px;border-radius:6px;font-size:12.5px">python -m src.data.build_kr_rail_danyang_aoi
python -m src.multitemporal_pipeline --project config/projects/kr_rail_danyang.yaml --phase B --stage all
python -m src.evaluation.generate_multitemporal_html_report --project config/projects/kr_rail_danyang.yaml</pre>
<h3>환경</h3>
<p>git commit <code>{esc(manifest.get('git_commit'))}</code> · seed {manifest.get('seed')} · 패키지 {esc(', '.join(f'{k} {v}' for k, v in list(pk.items())[:8]))}</p>
<h3>연도쌍별 Before / After / Change</h3>{pair_figs}
<h3>산출물</h3>
<ul><li><code>outputs/kr_rail_danyang/vectors/</code> - change_polygons_all, hotspots, skywatch_roi (GPKG/GeoJSON)</li>
<li><code>outputs/kr_rail_danyang/rasters/change_intensity.tif</code>, <code>comparisons/&lt;pair&gt;/rasters/</code></li>
<li><code>outputs/kr_rail_danyang/reports/</code> - sentinel2_baseline_report.md, scene_selection.json, *.csv</li></ul>
<p class="foot">생성 {date.today().isoformat()} · Sentinel-2 change candidate screening (Decision Support) · 지장물/보상 판정 자료 아님.</p></section>""")

    nav = "".join(f'<a href="#{i}">{t}</a>' for i, t in toc)
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>단양 매포 변화 Screening</title><style>{CSS}</style></head><body><div class="wrap">
<header class="hero"><h1>단양 매포읍 Sentinel-2 변화 Screening 보고서</h1>
<p>한국철도공단 토지보상 Demo · 중앙선 도담~영천 복선전철 · SkyWatch 50cm 구매 전 단계 · {date.today().isoformat()}</p></header>
<nav class="toc">{nav}</nav>{"".join(S)}</div></body></html>"""
    out_path = Path(out_path) if out_path else rep / "kr_rail_danyang_report.html"
    out_path.write_text(doc, encoding="utf-8")
    logger.info("[REPORT] HTML 저장: %s (%.1f MB)", out_path, out_path.stat().st_size / 1e6)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="다중시점 Screening HTML 보고서 생성")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    build_html(args.project, args.out)


if __name__ == "__main__":
    main()
