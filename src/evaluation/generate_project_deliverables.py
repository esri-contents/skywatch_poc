"""Generate project-specific evidence reports from executed outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.data.project_acquisition import load_project


def _markdown_table(frame: pd.DataFrame) -> str:
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "|" + "|".join(["---"] * len(columns)) + "|"]
    for values in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(str(v) for v in values) + " |")
    return "\n".join(lines)


def _comparison_rows(cfg: dict) -> tuple[pd.DataFrame, list[gpd.GeoDataFrame]]:
    epochs = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    frames: list[gpd.GeoDataFrame] = []
    for left, right in cfg["analysis"]["comparison_pairs"]:
        code = f"{left}_{right}"
        path = Path(cfg["paths"]["output_root"]) / "comparisons" / code / "vectors" / "building_change_results.gpkg"
        gdf = gpd.read_file(path)
        gdf["comparison"] = code
        gdf["period"] = f'{epochs[left]["date"]}–{epochs[right]["date"]}'
        frames.append(gdf)
    return pd.concat([pd.DataFrame(x.drop(columns="geometry")) for x in frames], ignore_index=True), frames


def threshold_calibration(cfg: dict) -> tuple[Path, Path, Path]:
    rows, geo_frames = _comparison_rows(cfg)
    output = Path(cfg["paths"]["reports"])
    output.mkdir(parents=True, exist_ok=True)
    records = []
    # has_register_match round-trips through GeoJSON/GPKG as the literal
    # strings "True"/"False" (or NaN when no overlay match was attempted at
    # all), so a plain .astype(bool) treats every non-null string as truthy
    # and silently drops all genuinely-unmatched rows from the heuristic.
    bool_map = {"True": True, "False": False, True: True, False: False}
    for threshold in cfg["analysis"]["threshold_candidates"]:
        for code, group in rows.groupby("comparison"):
            eligible = ~group["change_type"].eq("DEMOLITION")
            unmatched = ~group["has_register_match"].map(bool_map).fillna(False)
            predicted_new = eligible & unmatched & group["change_ratio"].fillna(0).ge(threshold)
            site = group["site_id"].fillna(pd.Series([f"row_{i}" for i in group.index], index=group.index))
            records.append({
                "threshold": threshold,
                "comparison": code,
                "new_building_candidates": int(predicted_new.sum()),
                "expansion_candidates": int((eligible & ~predicted_new).sum()),
                "high_priority_candidates": int(group["inspection_priority"].eq("HIGH").sum()),
                "unique_field_sites": int(site.nunique()),
                "direction_mismatch_or_review": int(group["directional_consistency_flag"].map(bool_map).eq(False).sum()),
            })
    sensitivity = pd.DataFrame(records)
    csv_path = output / "threshold_sensitivity.csv"
    sensitivity.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # One manual-label sheet across all periods.  A stable PNU (or rounded centroid)
    # is the grouping key, so the same site cannot leak between review splits.
    samples = []
    for gdf in geo_frames:
        cent = gdf.to_crs(5186).geometry.centroid
        pnu = gdf.get("pnu", pd.Series(index=gdf.index, dtype="object")).fillna("").astype(str)
        spatial = (cent.x.round(-1).astype(int).astype(str) + "_" + cent.y.round(-1).astype(int).astype(str))
        group_key = pnu.where(pnu.str.len().ge(19), spatial)
        tmp = gdf[["comparison", "site_id", "pnu", "change_type", "change_ratio", "inspection_priority"]].copy()
        tmp["validation_group"] = group_key
        tmp["review_split"] = group_key.map(lambda x: "validation" if int(hashlib.sha256(x.encode()).hexdigest()[:4], 16) % 5 == 0 else "calibration")
        tmp["human_label"] = ""
        tmp["reviewer_note"] = ""
        samples.append(tmp)
    manual = pd.concat(samples, ignore_index=True).sort_values(["review_split", "validation_group"]).drop_duplicates(["validation_group", "comparison"])
    manual_path = output / "manual_validation_sample.csv"
    manual.to_csv(manual_path, index=False, encoding="utf-8-sig")

    chosen = cfg["analysis"]["change_ratio_new_building_min"]
    chosen_rows = sensitivity[sensitivity.threshold.eq(chosen)]
    lines = [
        f"# {cfg['project']['short_name']} 분류 임계값 민감도 분석",
        "",
        f"- 임시 운영값: `change_ratio_new_building_min={chosen}`",
        "- 정답 라벨: 없음. 따라서 정확도 보정 완료가 아니라 **민감도 분석과 임시 운영값 선정**이다.",
        "- 기존 분류기 예측값은 정답으로 사용하지 않았다. 수동 검수표는 동일 PNU/공간그룹이 calibration과 validation에 겹치지 않도록 분리했다.",
        "- 건물 후보 수와 `site_id` 기반 중복 제거 현장 수는 서로 다른 지표다.",
        "- `new_building_candidates`/`expansion_candidates`는 각 threshold에서 change_ratio를 다시 적용해 재계산한 값이라 threshold에 따라 단조 변화한다."
        " 반면 `high_priority_candidates`/`unique_field_sites`/`direction_mismatch_or_review`는 실제 운영 실행(threshold=0.5) 1회에서 나온 값을 그대로 표시한 것이라 threshold marginal 값에서는 변하지 않는다"
        " - 이 세 지표는 threshold sweep이 아니라 우선순위·현장통합·방향성 로직 자체의 결과다.",
        "",
        "## 임시값 실행 요약",
        "",
        _markdown_table(chosen_rows),
        "",
        "## 전체 후보값 비교",
        "",
        _markdown_table(sensitivity),
        "",
        "운영 확정 전 `manual_validation_sample.csv`의 사람 판독 라벨을 채운 뒤 site/PNU 그룹 단위로 독립 검증해야 한다.",
    ]
    md_path = output / "classification_threshold_calibration.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return csv_path, manual_path, md_path


_PRIORITY_COLORS = {"HIGH": "#e63462", "MEDIUM": "#f28a30", "LOW": "#f2c94c"}
_TYPE_ORDER = ["NEW_BUILDING", "EXPANSION_OR_RECONSTRUCTION", "DEMOLITION", "OTHER_CHANGE"]
_TYPE_LABELS = {
    "NEW_BUILDING": "신축 추정",
    "EXPANSION_OR_RECONSTRUCTION": "증축·개축 추정",
    "DEMOLITION": "철거 추정(건물 미교차)",
    "OTHER_CHANGE": "기타 변화",
}
_TYPE_COLORS = {
    "NEW_BUILDING": "#e63462",
    "EXPANSION_OR_RECONSTRUCTION": "#f28a30",
    "DEMOLITION": "#118ab2",
    "OTHER_CHANGE": "#9aa9b5",
}
_TRUE_VALUES = {"True": True, "true": True, True: True, 1: True, "1": True}
_FALSE_VALUES = {"False": False, "false": False, False: False, 0: False, "0": False}

_CSS = """@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;800&display=swap');
*{box-sizing:border-box}
body{margin:0;background:#f2f5f8;color:#17222e;font-family:'Noto Sans KR',sans-serif;line-height:1.65}
header{background:linear-gradient(120deg,#073b4c,#118ab2);color:white;padding:54px max(6vw,34px)}
h1{font-size:34px;margin:5px 0}
header p{max-width:850px;line-height:1.7}
main{max-width:1180px;margin:-24px auto 40px;padding:0 24px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-top:13px}
.grid.first{margin-top:0}
.card,section{background:white;border-radius:14px;padding:20px;box-shadow:0 4px 20px #17324d14}
.card b,.card small{display:block;color:#567}
.card strong{font-size:27px;display:block;margin:8px 0;color:#087e8b}
section{margin-top:18px}
h2{color:#073b4c;margin-top:0}
h3{color:#073b4c;margin:22px 0 6px;font-size:17px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:9px 10px;border-bottom:1px solid #dde5eb;text-align:left;vertical-align:top}
th{color:#345;background:#f6f9fb}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
img{max-width:100%;border-radius:10px}
.warn{border-left:5px solid #ff9f1c;background:#fff8e9}
a{color:#087e8b}
.findings{display:grid;grid-template-columns:repeat(2,1fr);gap:13px}
.finding{border-left:5px solid #118ab2;background:#f3fafd;border-radius:10px;padding:14px 16px}
.finding b{display:block;color:#073b4c;margin-bottom:4px}
.finding span{font-size:14px}
.ins{border-left:5px solid #087e8b;background:#eef8f8;border-radius:8px;padding:10px 14px;margin:14px 0 0;font-size:14.5px}
.ins b{color:#087e8b}
.chart{max-width:680px;display:block}
.two{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}
.figs{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.figs figure{margin:0}
.figs figcaption{font-size:13px;color:#456;margin-top:6px}
.tag{display:inline-block;padding:1px 8px;border-radius:9px;font-size:12px;color:#fff}
.small{font-size:13px;color:#567}
.steps li{margin-bottom:8px}
svg text{font-family:'Noto Sans KR',sans-serif}
.hero-img{background:#081821;padding:46px 0 54px}
.hero-inner{max-width:1920px;margin:0 auto;padding:0 max(3vw,20px)}
.hero-inner h2{color:#fff;text-align:center;font-size:30px;margin:0 0 8px}
.hero-inner .sub{color:#9fc4d1;text-align:center;margin:0 0 24px;font-size:15px}
.hero-inner img{width:100%;display:block;border-radius:16px;box-shadow:0 20px 60px #000a}
.hero-inner .cap{color:#cfe3ea;text-align:center;margin-top:18px;font-size:14px;max-width:900px;margin-left:auto;margin-right:auto;line-height:1.6}
@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}.findings,.two,.figs{grid-template-columns:1fr}.hero-inner h2{font-size:22px}h1{font-size:26px}}"""


def _truthy(series: pd.Series) -> pd.Series:
    return series.map(_TRUE_VALUES).fillna(False).astype(bool)


def _falsy(series: pd.Series) -> pd.Series:
    """Explicit False only - NaN (flag never computed) is not a mismatch."""
    return series.map(_FALSE_VALUES).eq(False) & series.notna()


def _pct(part: float, whole: float, digits: int = 1) -> str:
    return f"{part / whole * 100:.{digits}f}%" if whole else "-"


def _share(value: float | None) -> str:
    return f"{value * 100:.0f}%" if value is not None else "-"


def _cards(rows: list[tuple]) -> str:
    return "".join(f'<div class="card"><b>{a}</b><strong>{b}</strong><small>{c}</small></div>' for a, b, c in rows)


def _source_cell(source: dict) -> str:
    if source.get("url") and source["url"] != "N/A":
        return f'<a href="{source["url"]}">{source["notice_number"]}</a>'
    return str(source["notice_number"])


def _strength(moran: dict) -> str:
    if moran["p_sim"] is None or moran["p_sim"] >= 0.05:
        return "통계적으로 유의하지 않은"
    return "약한 양의" if moran["I"] < 0.3 else "중간 정도의 양의" if moran["I"] < 0.6 else "강한 양의"


def _hbar_svg(items: list[tuple[str, float, str, str]], width: int = 560, label_w: int = 150) -> str:
    """Horizontal bar chart. items = (label, value, color, value_label)."""
    if not items:
        return ""
    row_h, top = 30, 6
    peak = max(v for _, v, _, _ in items) or 1
    plot_w = width - label_w - 170
    rows = []
    for i, (label, value, color, vlabel) in enumerate(items):
        y = top + i * row_h
        w = max(value / peak * plot_w, 2 if value else 0)
        rows.append(
            f'<text x="{label_w - 8}" y="{y + 17}" text-anchor="end" font-size="12.5" fill="#345">{label}</text>'
            f'<rect x="{label_w}" y="{y + 3}" width="{w:.1f}" height="20" rx="4" fill="{color}"/>'
            f'<text x="{label_w + w + 6:.1f}" y="{y + 18}" font-size="12.5" fill="#17222e">{vlabel}</text>'
        )
    height = top * 2 + len(items) * row_h
    return f'<svg viewBox="0 0 {width} {height}" width="100%" class="chart" role="img">{"".join(rows)}</svg>'


def _stack_svg(items: list[tuple[str, list[tuple[float, str]], str]], width: int = 560, label_w: int = 150) -> str:
    """Stacked horizontal bars. items = (label, [(value, color), ...], end_label)."""
    if not items:
        return ""
    row_h, top = 30, 6
    peak = max(sum(v for v, _ in segs) for _, segs, _ in items) or 1
    plot_w = width - label_w - 170
    rows = []
    for i, (label, segs, end_label) in enumerate(items):
        y = top + i * row_h
        x = float(label_w)
        rows.append(f'<text x="{label_w - 8}" y="{y + 17}" text-anchor="end" font-size="12.5" fill="#345">{label}</text>')
        for value, color in segs:
            w = value / peak * plot_w
            if w > 0:
                rows.append(f'<rect x="{x:.1f}" y="{y + 3}" width="{w:.1f}" height="20" fill="{color}"/>')
            x += w
        rows.append(f'<text x="{x + 6:.1f}" y="{y + 18}" font-size="12.5" fill="#17222e">{end_label}</text>')
    height = top * 2 + len(items) * row_h
    return f'<svg viewBox="0 0 {width} {height}" width="100%" class="chart" role="img">{"".join(rows)}</svg>'


def _timeline_svg(t1: date, t2: date, baseline: date | None, events: list[tuple[date, str]]) -> str:
    width, left, right, axis_y = 900, 40, 40, 70
    marks = {t1: "T1 영상", t2: "T2 영상"}
    if baseline:
        marks[baseline] = "기준일(잠정)"
    for day, label in events:
        marks[day] = f"{marks[day]} · 지정고시" if day in marks else label
    days = sorted(marks)
    start, end = days[0], days[-1]
    span = max((end - start).days, 1)

    def px(day: date) -> float:
        return left + (day - start).days / span * (width - left - right)

    parts = [f'<line x1="{left}" y1="{axis_y}" x2="{width - right}" y2="{axis_y}" stroke="#9aa9b5" stroke-width="2"/>']
    if baseline and t1 < baseline < t2:
        parts.append(f'<rect x="{px(t1):.1f}" y="{axis_y - 8}" width="{px(baseline) - px(t1):.1f}" height="16" fill="#ff9f1c" opacity=".85"/>')
        parts.append(f'<rect x="{px(baseline):.1f}" y="{axis_y - 8}" width="{px(t2) - px(baseline):.1f}" height="16" fill="#118ab2" opacity=".85"/>')
    else:
        parts.append(f'<rect x="{px(t1):.1f}" y="{axis_y - 8}" width="{px(t2) - px(t1):.1f}" height="16" fill="#118ab2" opacity=".85"/>')
    for i, day in enumerate(days):
        x, up = px(day), i % 2 == 0
        y_text = axis_y - 44 if up else axis_y + 40
        anchor = "start" if x < 120 else "end" if x > width - 140 else "middle"
        label = marks[day]
        label = label if len(label) <= 20 else label[:19] + "…"
        parts.append(f'<line x1="{x:.1f}" y1="{axis_y - 20 if up else axis_y + 8}" x2="{x:.1f}" y2="{axis_y - 8 if up else axis_y + 22}" stroke="#345"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{axis_y}" r="4" fill="#073b4c"/>')
        parts.append(f'<text x="{x:.1f}" y="{y_text}" text-anchor="{anchor}" font-size="12" fill="#17222e" font-weight="600">{label}</text>')
        parts.append(f'<text x="{x:.1f}" y="{y_text + 14}" text-anchor="{anchor}" font-size="11" fill="#567">{day.isoformat()}</text>')
    return f'<svg viewBox="0 0 {width} 140" width="100%" role="img">{"".join(parts)}</svg>'


def _to_date(value) -> date | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return date.fromisoformat(match.group(0)) if match else None


def _metrics(cfg: dict, summary: dict, main_code: str) -> dict | None:
    """Derive every number the narrative cites from the executed outputs."""
    root = Path(cfg["paths"]["output_root"])
    comp = root / "comparisons" / main_code
    g = gpd.read_file(comp / "vectors" / "building_change_results.gpkg")
    if g.empty:
        return None
    n = len(g)
    m: dict = {"n": n}

    def col(name: str) -> pd.Series:
        return g[name] if name in g.columns else pd.Series([float("nan")] * n, index=g.index)

    change_type, priority = col("change_type"), col("inspection_priority")
    area = pd.to_numeric(col("change_area_m2"), errors="coerce").fillna(0.0)
    footprint = pd.to_numeric(col("building_area_m2"), errors="coerce")
    conf = pd.to_numeric(col("confidence"), errors="coerce")
    delta = pd.to_numeric(col("brightness_delta"), errors="coerce")
    note = col("classification_note").fillna("").astype(str)
    registered = _truthy(col("has_register_match"))
    linked = footprint.notna()
    hot = col("gi_class").fillna("").astype(str).str.startswith("HOT")
    cold = col("gi_class").fillna("").astype(str).str.startswith("COLD")
    high = priority.eq("HIGH")

    m["n_bld"], m["n_poly"] = int(linked.sum()), int((~linked).sum())
    m["area_bld"], m["area_poly"] = float(area[linked].sum()), float(area[~linked].sum())
    m["area_total"] = m["area_bld"] + m["area_poly"]
    m["poly_median_area"] = float(area[~linked].median()) if m["n_poly"] else 0.0
    m["priority"] = {k: int(priority.eq(k).sum()) for k in ("HIGH", "MEDIUM", "LOW")}
    m["registered"], m["registered_of_bld"] = int(registered.sum()), int((registered & linked).sum())

    rows = []
    for ctype in _TYPE_ORDER:
        sel = change_type.eq(ctype)
        if not sel.any():
            continue
        rows.append({
            "type": ctype, "n": int(sel.sum()), "linked": int((sel & linked).sum()),
            "median_area": float(area[sel].median()),
            "median_footprint": float(footprint[sel].median()) if (sel & linked).any() else None,
            "mean_conf": float(conf[sel].mean()),
            "high": int((sel & high).sum()), "medium": int((sel & priority.eq("MEDIUM")).sum()),
            "low": int((sel & priority.eq("LOW")).sum()),
            "registered": int((sel & registered).sum()),
            "confirmed": int((sel & note.str.contains("확정")).sum()),
            "heuristic": int((sel & note.str.contains("휴리스틱")).sum()),
            "mean_delta": float(delta[sel].mean()) if delta[sel].notna().any() else None,
            "mean_area": float(area[sel].mean()),
        })
    m["types"] = {r["type"]: r for r in rows}
    m["type_rows"] = rows

    site = col("site_id")
    m["high_sites"] = int(site[high].nunique())
    m["sites_total"] = int(summary[main_code].get("site_count", site.nunique()))
    sizes = site.value_counts()
    if len(sizes):
        top_site = sizes.index[0]
        in_top = site.eq(top_site)
        dong = col("gu")[in_top].dropna()
        m["top_site"] = {"id": top_site, "n": int(sizes.iloc[0]), "high": int((in_top & high).sum()),
                         "dong": dong.mode().iat[0] if len(dong) else ""}

    centroids = g.geometry.centroid
    cells = pd.DataFrame({"gx": (centroids.x // 1000).astype(int), "gy": (centroids.y // 1000).astype(int)})
    per_cell = cells.groupby(["gx", "gy"]).size().sort_values(ascending=False)
    m["cells_used"], m["top3_cell_share"] = int(len(per_cell)), float(per_cell.head(3).sum() / n)

    # Dong-level rates use the footprint layer as the denominator.
    dongs = []
    try:
        bl = gpd.read_file(cfg["paths"]["buildings"])
        if {"sigungu", "gu"} <= set(bl.columns) and len(bl):
            m["footprints"] = len(bl)
            foot = bl.groupby(["sigungu", "gu"]).size()
            sub = pd.DataFrame({"sigungu": col("sigungu"), "gu": col("gu"), "high": high, "area": area, "linked": linked})
            sub = sub[sub.linked & sub.gu.notna()]
            for (sgg, gu), count in foot.items():
                if count < 50:
                    continue
                sel = sub[(sub.sigungu == sgg) & (sub.gu == gu)]
                dongs.append({"name": f"{sgg} {gu}", "footprints": int(count), "n": len(sel),
                              "high": int(sel.high.sum()), "area": float(sel.area.sum()),
                              "rate": len(sel) / count})
    except Exception:  # noqa: BLE001 - dong table is optional context
        dongs = []
    m["dongs"] = sorted(dongs, key=lambda d: d["rate"], reverse=True)
    m["overall_rate"] = m["n_bld"] / m["footprints"] if m.get("footprints") else None

    m["hot"], m["cold"] = int(hot.sum()), int(cold.sum())
    m["hot_linked"], m["cold_linked"] = int((hot & linked).sum()), int((cold & linked).sum())
    m["high_in_hot"] = int((high & hot).sum())
    m["high_share_hot"] = float((high & hot).sum() / hot.sum()) if hot.any() else None
    others = ~hot & ~cold
    m["high_share_other"] = float((high & others).sum() / others.sum()) if others.any() else None
    m["gi"] = {k: int(col("gi_class").eq(k).sum()) for k in ("HOT_99", "HOT_95", "HOT_90", "COLD_99", "COLD_95", "COLD_90")}
    moran_path = comp / "statistics" / "global_moran.json"
    moran = json.loads(moran_path.read_text(encoding="utf-8")) if moran_path.exists() else None
    m["moran"] = moran if moran and moran.get("I") is not None else None

    heur = note.str.contains("휴리스틱")
    flagged = heur & col("directional_consistency_flag").notna()
    m["flagged"], m["mismatch"] = int(flagged.sum()), int((flagged & _falsy(col("directional_consistency_flag"))).sum())
    new_sel = change_type.eq("NEW_BUILDING")
    m["new_flagged"] = int((new_sel & col("directional_consistency_flag").notna()).sum())
    m["new_mismatch"] = int((new_sel & _falsy(col("directional_consistency_flag"))).sum())

    changes = change_type.isin(["NEW_BUILDING", "EXPANSION_OR_RECONSTRUCTION"])
    m["small_footprint"], m["small_footprint_of"] = int((footprint < 100).sum()), int(linked.sum())
    m["small_change"], m["small_change_of"] = int((area[changes] < 100).sum()), int(changes.sum())
    m["high_median_footprint"] = float(footprint[high].median()) if high.any() else None

    purposes = col("mainPurpsCdNm").where(linked & registered).dropna().value_counts()
    m["purposes"] = [(str(k), int(v)) for k, v in purposes.head(6).items()]
    hazard = col("mainPurpsCdNm") == "위험물저장및처리시설"
    m["hazard_total"] = int(hazard.sum())
    m["hazard_expansions"] = int((hazard & change_type.eq("EXPANSION_OR_RECONSTRUCTION")).sum())
    m["poly_high"] = int((high & ~linked).sum())
    m["high_unmatched_bld"] = int((high & linked & ~registered).sum())
    m["unmatched_bld"] = int((linked & ~registered).sum())

    try:
        aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(g.crs)
        edge = aoi.geometry.union_all().boundary
        dist = centroids.apply(edge.distance)
        m["edge_high_median"] = float(dist[high].median()) if high.any() else None
        m["edge_within_100"] = int((dist < 100).sum())
    except Exception:  # noqa: BLE001
        m["edge_high_median"], m["edge_within_100"] = None, None

    sens_path = Path(cfg["paths"]["reports"]) / "threshold_sensitivity.csv"
    m["sensitivity"] = None
    if sens_path.exists():
        sens = pd.read_csv(sens_path, encoding="utf-8-sig")
        sens = sens[sens["comparison"].eq(main_code)].sort_values("threshold")
        if len(sens):
            m["sensitivity"] = sens.to_dict("records")

    poly = ~linked
    demo = change_type.eq("DEMOLITION")
    m["demo_top"] = [float(v) for v in area[demo].sort_values(ascending=False).head(3)]
    m["poly_bright_neg"] = int((poly & delta.lt(0)).sum())
    m["poly_with_delta"] = int((poly & delta.notna()).sum())
    return m


def _events(cfg: dict) -> list[tuple[date, str]]:
    name = cfg["project"].get("official_name", "")
    events = []
    for source in cfg.get("official_sources", []):
        day = _to_date(source.get("notice_date"))
        if not day:
            continue
        label = re.sub(r"^의\s+", "", str(source["title"]).replace(name, "").strip(" -"))
        events.append((day, label))
        for extra in re.findall(r"\d{4}-\d{2}-\d{2}", str(source["notice_date"]))[1:]:
            events.append((date.fromisoformat(extra), label))
    return events


def _findings(m: dict, ctx: dict) -> list[tuple[str, str]]:
    types = m["types"]
    out = [(
        "변화의 ‘건수’와 ‘면적’은 전혀 다른 이야기다",
        f'후보 {m["n"]}건 중 건물 footprint에 연결된 것은 {m["n_bld"]}건({_pct(m["n_bld"], m["n"], 0)})이지만 변화 면적으로는 '
        f'{m["area_bld"]:,.0f}㎡({_pct(m["area_bld"], m["area_total"])})에 그친다. 건물과 겹치지 않는 폴리곤 {m["n_poly"]}건이 '
        f'{m["area_poly"]:,.0f}㎡({_pct(m["area_poly"], m["area_total"])})를 차지하며 중앙값이 {m["poly_median_area"]:,.0f}㎡(약 {m["poly_median_area"] / 100:.0f}픽셀)다. '
        "Sentinel-2가 실제로 강하게 포착한 것은 개별 건물보다 필지 단위 토지 변화다.",
    ), (
        f'HIGH {m["priority"]["HIGH"]}건은 실제로는 {m["high_sites"]}개 현장이다',
        f'HIGH 후보는 인접 건물이 한 현장으로 묶여 {m["high_sites"]}개 현장으로 줄어든다(중복 제거 전체 {m["sites_total"]}현장).'
        + (f' 가장 큰 묶음은 {m["top_site"]["dong"]} {m["top_site"]["id"]}로 {m["top_site"]["n"]}건(HIGH {m["top_site"]["high"]}건)이 한곳에 모여 있다.' if m.get("top_site") else "")
        + " 조사 인력은 후보 수가 아니라 현장 수 기준으로 산정해야 한다.",
    )]
    if m["dongs"]:
        top = m["dongs"][0]
        out.append((
            f'{top["name"]}이 가장 집중된 지역이다',
            f'건물 연결 후보율이 {top["rate"] * 100:.1f}%({top["footprints"]:,}동 중 {top["n"]}건)로 지구 평균 {m["overall_rate"] * 100:.1f}%의 '
            f'{top["rate"] / m["overall_rate"]:.1f}배이고, 후보 {top["n"]}건 중 {top["high"]}건이 HIGH다.'
            + (" 절대 건수가 가장 많은 동과 후보율이 가장 높은 동이 다르므로 두 지표를 함께 봐야 한다." if max(m["dongs"], key=lambda d: d["n"])["name"] != top["name"] else ""),
        ))
    if m["moran"] and m["hot"]:
        out.append((
            "변화는 무작위가 아니라 군집되어 있다",
            f'Moran\'s I={m["moran"]["I"]:.3f}(z={m["moran"]["z_sim"]:.1f}, p={m["moran"]["p_sim"]:g})는 {_strength(m["moran"])} 공간 군집을 뜻한다. '
            f'Gi* hotspot {m["hot"]}건 중 HIGH 비율은 {_pct(m["high_in_hot"], m["hot"], 0)}로 비군집 후보의 {_share(m["high_share_other"])}보다 높고, '
            f'hotspot의 {_pct(m["hot_linked"], m["hot"], 0)}는 건물 단위 후보다.',
        ))
    if ctx["pre_share"] is not None:
        out.append((
            "관측 구간의 일부는 기준일 이전이다",
            f'비교 구간 {ctx["window_days"]}일 중 {ctx["pre_days"]}일({ctx["pre_share"] * 100:.0f}%)이 기준일({ctx["baseline"]}) 이전이다. '
            "따라서 신축 후보 전체를 기준일 이후 행위로 볼 수 없고, 기준일 부근 영상으로 구간을 좁히기 전에는 보상 판단 근거로 쓰지 않는다.",
        ))
    new = types.get("NEW_BUILDING")
    if new and m["new_flagged"]:
        out.append((
            f'신축 추정의 {_pct(m["new_mismatch"], m["new_flagged"], 0)}는 밝기 방향이 기대와 다르다',
            f'신축 추정 {new["n"]}건 중 {new["heuristic"]}건은 대장 사용승인일이 아닌 휴리스틱 판정이며, 방향을 판정한 {m["new_flagged"]}건 중 {m["new_mismatch"]}건은 '
            "T2가 더 어두워져 신축 기대 방향과 불일치한다. 소규모 footprint와 계절 차이로 인한 오탐 가능성을 열어 두고 육안 확인을 거쳐야 한다.",
        ))
    return out


def _context(cfg: dict, main_left: str, main_right: str) -> dict:
    epochs = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    t1, t2 = _to_date(epochs[main_left]["date"]), _to_date(epochs[main_right]["date"])
    baseline = _to_date(cfg.get("compensation", {}).get("baseline_date"))
    window = (t2 - t1).days
    ctx = {"t1": t1, "t2": t2, "baseline": baseline, "window_days": window, "pre_days": None, "pre_share": None}
    if baseline and t1 < baseline < t2:
        ctx["pre_days"] = (baseline - t1).days
        ctx["pre_share"] = ctx["pre_days"] / window
    return ctx


def _type_table(m: dict) -> str:
    body = ""
    for r in m["type_rows"]:
        size = f'{r["median_footprint"]:,.0f}㎡' if r["median_footprint"] is not None else "-"
        basis = f'대장 근거 {r["confirmed"]} / 휴리스틱 {r["heuristic"]}' if (r["confirmed"] or r["heuristic"]) else "영상 변화 강도"
        body += (
            f'<tr><td><span class="tag" style="background:{_TYPE_COLORS[r["type"]]}">{_TYPE_LABELS[r["type"]]}</span></td>'
            f'<td class="n">{r["n"]}</td><td class="n">{r["linked"]}</td><td class="n">{size}</td><td class="n">{r["median_area"]:,.0f}㎡</td>'
            f'<td class="n">{r["mean_conf"]:.2f}</td><td class="n">{r["high"]} / {r["medium"]} / {r["low"]}</td><td>{basis}</td></tr>'
        )
    return ('<table><thead><tr><th>유형</th><th class="n">후보</th><th class="n">건물 연결</th><th class="n">건물 면적 중앙값</th>'
            '<th class="n">변화 면적 중앙값</th><th class="n">평균 신뢰도</th><th class="n">HIGH / MEDIUM / LOW</th><th>판정 근거</th></tr></thead>'
            f"<tbody>{body}</tbody></table>")


def _dong_table(m: dict) -> str:
    body = "".join(
        f'<tr><td>{d["name"]}</td><td class="n">{d["footprints"]:,}</td><td class="n">{d["n"]}</td><td class="n">{d["rate"] * 100:.1f}%</td>'
        f'<td class="n">{d["high"]}</td><td class="n">{_pct(d["high"], d["n"], 0)}</td><td class="n">{d["area"]:,.0f}㎡</td></tr>'
        for d in m["dongs"]
    )
    return ('<table><thead><tr><th>행정동</th><th class="n">footprint</th><th class="n">건물 연결 후보</th><th class="n">후보율</th>'
            '<th class="n">HIGH</th><th class="n">HIGH 비중</th><th class="n">변화 면적</th></tr></thead>'
            f"<tbody>{body}</tbody></table>")


def _report_sections(cfg: dict, m: dict, ctx: dict, buildings: int, register_n: int, main_code: str, matched: int, total: dict) -> str:
    types, pri = m["types"], m["priority"]
    charts = _hbar_svg([
        (_TYPE_LABELS[r["type"]], r["n"], _TYPE_COLORS[r["type"]], f'{r["n"]}건 · 중앙값 {r["median_area"]:,.0f}㎡') for r in m["type_rows"]
    ], width=620, label_w=170)
    prio_chart = _stack_svg([
        (_TYPE_LABELS[r["type"]], [(r["high"], _PRIORITY_COLORS["HIGH"]), (r["medium"], _PRIORITY_COLORS["MEDIUM"]), (r["low"], _PRIORITY_COLORS["LOW"])],
         f'{r["n"]}건') for r in m["type_rows"]
    ], width=620, label_w=170)
    dong_chart = _stack_svg([
        (d["name"], [(d["high"], _PRIORITY_COLORS["HIGH"]), (d["n"] - d["high"], "#9ec5d3")], f'{d["n"]}건 · 후보율 {d["rate"] * 100:.1f}%') for d in m["dongs"]
    ], width=620, label_w=140) if m["dongs"] else ""
    sens_rows = m["sensitivity"] or []
    sens_chart = _hbar_svg([
        (f'임계값 {r["threshold"]:g}', r["new_building_candidates"], "#118ab2", f'신축 {r["new_building_candidates"]} / 증축 {r["expansion_candidates"]}') for r in sens_rows
    ], width=620, label_w=110) if sens_rows else ""
    events = _events(cfg)
    timeline = _timeline_svg(ctx["t1"], ctx["t2"], ctx["baseline"], events)

    new, exp = types.get("NEW_BUILDING"), types.get("EXPANSION_OR_RECONSTRUCTION")
    demo, other = types.get("DEMOLITION"), types.get("OTHER_CHANGE")
    out = []

    out.append(f'<section><h2>핵심 발견</h2><div class="findings">'
               + "".join(f'<div class="finding"><b>{title}</b><span>{text}</span></div>' for title, text in _findings(m, ctx))
               + "</div></section>")

    pre = ""
    if ctx["pre_share"] is not None:
        pre = (f'<p class="ins"><b>해석</b> 주황색 구간(T1 ~ 기준일, {ctx["pre_days"]}일)은 지구지정 이전에 일어난 변화까지 포함하고, 파란색 구간(기준일 ~ T2, '
               f'{ctx["window_days"] - ctx["pre_days"]}일)만 기준일 이후다. 비교에 쓴 영상이 두 시점뿐이라 두 구간을 분리할 수 없다. 기준일({ctx["baseline"].year}년 {ctx["baseline"].month}월) 전후 영상을 추가하면 신축 후보를 기준일 전후로 나눌 수 있다.</p>')
    out.append(f'<section><h2>분석 범위와 관측 시점</h2><p>공식 위치: {cfg["project"]["location"]} · 사업시행자: {cfg["project"]["operator"]}. '
               f'공식 AOI는 VWorld 국가공간정보 WFS의 <code>lt_c_lhzone</code> 지구코드 <code>{cfg["aoi"]["zonecode"]}</code>를 사용했다.</p>'
               f'<p>영상은 {", ".join(e["date"] for e in cfg["imagery"]["epochs"])}을 선택했다 (선정 근거: 프로젝트 설정 파일의 <code>imagery.selection_note</code> 참고).</p>'
               f'<h3>사업 연혁과 관측 구간</h3>{timeline}{pre}</section>')

    periods = "".join(
        f"<tr><td>{k}</td><td class=\"n\">{v['candidate_count']}</td><td class=\"n\">{v['site_count']}</td><td class=\"n\">{v['register_match_count']}</td>"
        f"<td class=\"n\">{v['alignment']['displacement_m']:.3f} m</td><td class=\"n\">{v['alignment']['ecc_score']:.3f}</td></tr>"
        for k, v in total["_all"].items()
    )
    out.append(f'<section><h2>실행 결과</h2><table><thead><tr><th>비교</th><th class="n">후보</th><th class="n">현장</th><th class="n">대장 매칭 후보</th>'
               f'<th class="n">정합 변위</th><th class="n">ECC 점수</th></tr></thead><tbody>{periods}</tbody></table>'
               f'<p>AOI 내 건물 footprint {buildings:,}동, 수집 건축물대장 {register_n:,}건. 후보 매칭률은 {ctx["date_span"]} 비교 기준 {matched / max(total["candidate_count"], 1) * 100:.1f}%이며 '
               f'전체 footprint 매칭률과 혼동하지 않는다.</p>'
               f'<p class="ins"><b>해석</b> 정합 변위 {total["alignment"]["displacement_m"]:.2f}m는 Sentinel-2 한 픽셀(10m)의 4% 안팎이라 두 시점의 위치 어긋남이 변화 후보를 만들 가능성은 낮다. '
               f'매칭률 {matched / max(total["candidate_count"], 1) * 100:.1f}%는 분모에 건물과 겹치지 않는 폴리곤 {m["n_poly"]}건이 들어 있어 낮게 보인다. '
               f'건물에 연결된 후보만 보면 {m["registered_of_bld"]}/{m["n_bld"]}건({_pct(m["registered_of_bld"], m["n_bld"])})이다.</p></section>')

    out.append(f'<section><h2>후보의 구성: 무엇을 탐지했나</h2><div class="two"><div>{charts}</div><div>{prio_chart}'
               f'<p class="small">막대 색: <span class="tag" style="background:{_PRIORITY_COLORS["HIGH"]}">HIGH</span> '
               f'<span class="tag" style="background:{_PRIORITY_COLORS["MEDIUM"]}">MEDIUM</span> <span class="tag" style="background:{_PRIORITY_COLORS["LOW"]}">LOW</span></p></div></div>'
               f'{_type_table(m)}'
               f'<p class="ins"><b>해석</b> HIGH {pri["HIGH"]}건 중 신축 추정 {new["high"] if new else 0}건, 증축·개축 추정 {exp["high"] if exp else 0}건이며 건물 미교차 폴리곤의 HIGH는 {m["poly_high"]}건이다. '
               + ("변화 면적이 큰 토지 변화보다 건물 단위 후보가 상위로 올라오는 구조로, 우선순위 점수가 건물 관련성과 행정 불확실성을 함께 반영하기 때문이다." if m["poly_high"] == 0 else "") + "</p></section>")

    if new and exp:
        matched_exp = exp["registered"]
        out.append(f'<section><h2>유형별 해석</h2>'
                   f'<h3><span class="tag" style="background:{_TYPE_COLORS["NEW_BUILDING"]}">신축 추정</span> {new["n"]}건</h3>'
                   f'<p>모두 휴리스틱 판정(변화 비율 ≥ {cfg["analysis"]["change_ratio_new_building_min"]})이며 건축물대장 사용승인일로 확정된 건은 없다. 건물 면적 중앙값 {new["median_footprint"]:,.0f}㎡, 평균 신뢰도 {new["mean_conf"]:.2f}. '
                   f'대장 미연결 {new["n"] - new["registered"]}건은 신축 후 대장 등재 전이거나, 미등재 건물일 수 있다 - 어느 쪽인지는 이 분석만으로 구분되지 않는다.</p>'
                   f'<h3><span class="tag" style="background:{_TYPE_COLORS["EXPANSION_OR_RECONSTRUCTION"]}">증축·개축 추정</span> {exp["n"]}건</h3>'
                   f'<p>{exp["confirmed"]}건은 대장 사용승인일이 T1 이전이라 ‘기존 건물에서 변화 신호 발생’으로 분류된 것이고, {exp["heuristic"]}건은 대장 미연결 휴리스틱이다. '
                   f'‘증축 확정’은 라벨 규칙상 표현일 뿐 물리적 증축을 현장에서 확인했다는 뜻이 아니다. 변화 면적 중앙값이 {exp["median_area"]:,.0f}㎡로 픽셀 하나(100㎡)에도 못 미쳐 경계 혼합화소 영향이 크다.'
                   + (f' 위험물 저장·처리시설 {m["hazard_total"]}건 중 {m["hazard_expansions"]}건이 이 유형이라 용도상 우선 확인 대상이다.' if m["hazard_expansions"] else "") + "</p>"
                   + (f'<h3><span class="tag" style="background:{_TYPE_COLORS["DEMOLITION"]}">철거 추정</span> {demo["n"]}건</h3>'
                      f'<p>건물과 겹치지 않는 폴리곤을 영상 변화 강도만으로 분류한 것이라 근거가 약하다. 건당 평균 {demo["mean_area"]:,.0f}㎡로 크고 밝기 변화는 평균 {demo["mean_delta"]:+.0f}이다. '
                      f'건물 레이어가 현재 시점 스냅샷이라 T1에 건물이 있었는지 확인되지 않으므로, 철거보다 토지 피복 변화(경작 상태, 식생, 토공)일 가능성이 열려 있다 - 이 부분은 가설이며 정사영상 확인이 필요하다.</p>' if demo and demo["mean_delta"] is not None else "")
                   + (f'<h3><span class="tag" style="background:{_TYPE_COLORS["OTHER_CHANGE"]}">기타 변화</span> {other["n"]}건</h3>'
                      f'<p>건물 본체와 겹치지 않고 버퍼 안에서만 변화가 있거나 밝기 방향이 철거와 반대인 폴리곤이다. 평균 신뢰도 {other["mean_conf"]:.2f}, HIGH {other["high"]}건 · MEDIUM {other["medium"]}건 · LOW {other["low"]}건으로 현장조사에서는 상대적으로 후순위다.</p>' if other else "")
                   + "</section>")

    if m["dongs"]:
        best_rate, best_count = m["dongs"][0], max(m["dongs"], key=lambda d: d["n"])
        best_share = max(m["dongs"], key=lambda d: d["high"] / d["n"] if d["n"] >= 5 else 0)
        out.append(f'<section><h2>지역별 분포</h2>{dong_chart}<p class="small">붉은색 = HIGH, 청회색 = 그 외. 건물 연결 후보만 집계(footprint 50동 이상 동).</p>'
                   f'{_dong_table(m)}'
                   f'<p class="ins"><b>해석</b> 절대 건수는 {best_count["name"]}({best_count["n"]}건)이 가장 많지만 후보율은 {best_rate["name"]}({best_rate["rate"] * 100:.1f}%)이 가장 높다. '
                   f'HIGH 비중은 {best_share["name"]}이 {_pct(best_share["high"], best_share["n"], 0)}로 가장 높다. 지구 평균 후보율은 {m["overall_rate"] * 100:.1f}%다. '
                   f'상위 3개 1㎢ 격자에 후보 {m["top3_cell_share"] * 100:.0f}%가 몰려 있어({m["cells_used"]}개 격자 사용) 조사 동선은 일부 권역에 집중시킬 수 있다.</p></section>')

    if m["moran"]:
        gi = m["gi"]
        mo = m["moran"]
        out.append(f'<section><h2>공간 군집과 Hotspot</h2><div class="grid first">'
                   f'<div class="card"><b>Global Moran\'s I</b><strong>{mo["I"]:.3f}</strong><small>z={mo["z_sim"]:.2f} · p={mo["p_sim"]:g} · n={mo["n"]} · k={mo["k"]}</small></div>'
                   f'<div class="card"><b>Hot spot (Gi*)</b><strong>{m["hot"]}</strong><small>99% {gi["HOT_99"]} · 95% {gi["HOT_95"]} · 90% {gi["HOT_90"]}</small></div>'
                   f'<div class="card"><b>Cold spot (Gi*)</b><strong>{m["cold"]}</strong><small>99% {gi["COLD_99"]} · 95% {gi["COLD_95"]} · 90% {gi["COLD_90"]}</small></div>'
                   f'<div class="card"><b>Hotspot 내 HIGH</b><strong>{m["high_in_hot"]}/{m["priority"]["HIGH"]}</strong><small>HIGH의 {_pct(m["high_in_hot"], m["priority"]["HIGH"], 0)}가 hotspot</small></div></div>'
                   f'<p class="ins"><b>해석</b> Moran\'s I {mo["I"]:.2f}는 {_strength(mo)} 공간 군집을 뜻하며(p={mo["p_sim"]:g}), 우선순위 점수가 비슷한 후보가 서로 가까이 모여 있다는 신호다. '
                   f'Hotspot {m["hot"]}건 중 {m["hot_linked"]}건이 건물 단위이고, cold spot {m["cold"]}건은 {m["cold_linked"]}건만 건물 단위라 나머지는 대면적 폴리곤이다. '
                   f'Hotspot 안에서 HIGH 비율은 {_pct(m["high_in_hot"], m["hot"], 0)}로, 그 밖 후보({_share(m["high_share_other"])})와 비교하면 ' + (f'{m["high_share_hot"] / m["high_share_other"]:.1f}배다.' if m["high_share_other"] else "비교할 수 없다.") + "</p></section>"
                   if m["high_share_other"] is not None and m["hot"] else "")

    out.append(f'<section><h2>건축물대장 연계</h2>'
               f'<p>건물에 연결된 후보 {m["n_bld"]}건 중 대장 매칭은 {m["registered_of_bld"]}건({_pct(m["registered_of_bld"], m["n_bld"])}), 미매칭은 {m["unmatched_bld"]}건이다. '
               f'미매칭 건물 중 {m["high_unmatched_bld"]}건({_pct(m["high_unmatched_bld"], m["unmatched_bld"], 0)})이 HIGH로 올라와 있어 ‘대장에 없는 건물이 변했다’는 조합이 우선순위를 끌어올리는 핵심 요인이다 (우선순위 점수의 행정 불확실성 항목).</p>'
               + (f'<p>대장 매칭 건물의 주용도: ' + " · ".join(f"{k} {v}건" for k, v in m["purposes"]) + ".</p>" if m["purposes"] else "")
               + f'<p class="ins"><b>해석</b> 미매칭이 곧 불법·미등재를 뜻하지는 않는다. 수집한 대장({register_n:,}건)의 기준 시점과 필지·도로명 매칭 한계가 있어 ‘대장 확인 필요’ 신호로만 쓰고 판정은 사업부서가 한다.</p></section>')

    conf_bits = [f'건물 연결 후보 {m["n_bld"]}건 중 {m["small_footprint"]}건({_pct(m["small_footprint"], m["small_footprint_of"], 0)})은 footprint가 100㎡ 미만으로 Sentinel-2 한 픽셀보다 작다',
                 f'신축·증축 후보 {m["small_change_of"]}건 중 {m["small_change"]}건({_pct(m["small_change"], m["small_change_of"], 0)})은 변화 면적이 100㎡ 미만이다']
    if m["flagged"]:
        conf_bits.append(f'휴리스틱 판정 {m["flagged"]}건 중 {m["mismatch"]}건({_pct(m["mismatch"], m["flagged"], 0)})은 밝기 방향이 기대와 반대다')
    if m["edge_high_median"] is not None:
        conf_bits.append(f'HIGH 후보의 지구 경계까지 거리 중앙값은 {m["edge_high_median"]:,.0f}m로 경계 혼합화소 영향은 크지 않다(경계 100m 이내 후보 {m["edge_within_100"]}건)')
    sens_text = ""
    if sens_rows:
        lo, hi = sens_rows[0], sens_rows[-1]
        sens_text = (f'<h3>임계값 민감도</h3>{sens_chart}<p class="small">신축 후보 수는 임계값 {lo["threshold"]:g}에서 {lo["new_building_candidates"]}건, {hi["threshold"]:g}에서 {hi["new_building_candidates"]}건으로 '
                     f'{_pct(lo["new_building_candidates"] - hi["new_building_candidates"], lo["new_building_candidates"], 0)} 줄어든다. ' + (f'HIGH 수({lo["high_priority_candidates"]}건)와 현장 수는 이 sweep에서 변하지 않는다. ' if len({r["high_priority_candidates"] for r in sens_rows}) == 1 else "")
                     + f'민감도표의 신축 수는 변화 비율만 재적용한 값이라 운영 분류의 {new["n"] if new else "-"}건과 정의가 달라 직접 비교하지 않는다.</p>')
    out.append(f'<section><h2>신뢰도와 한계</h2><ul>' + "".join(f"<li>{b}</li>" for b in conf_bits) + f'</ul>{sens_text}'
               f'<p class="ins"><b>해석</b> 이 결과의 강점은 ‘건물 단위 확정’이 아니라 넓은 지구에서 확인할 곳을 좁히는 데 있다. 개별 건물의 신축·증축 판정은 고해상 영상이나 현장 확인이 있어야 성립한다.</p></section>')

    out.append(f'<section class="warn"><h2>기준일 해석</h2><p>{cfg["compensation"]["baseline_date"]} ({cfg["compensation"]["baseline_type"]})을 업무상 미확정 후보 기준일로 설정했다. '
               f'실제 이주대책기준일/권리산정기준일과 다를 수 있으며, 보상계획 공고 원문을 사업부서가 확인하기 전까지 이 비교를 보상 판정으로 표현하지 않는다.</p></section>')

    out.append(_actions_section(cfg, m, ctx, brief=False))
    return "".join(out)


def _actions_section(cfg: dict, m: dict, ctx: dict, brief: bool) -> str:
    types = m["types"]
    new, exp, demo = types.get("NEW_BUILDING"), types.get("EXPANSION_OR_RECONSTRUCTION"), types.get("DEMOLITION")
    steps = [
        f'<b>1순위 - HIGH {m["priority"]["HIGH"]}건({m["high_sites"]}개 현장) 현장 확인.</b> 신축 추정 {new["n"] if new else 0}건은 건축 허가·대장 등재 여부를, '
        f'증축·개축 추정 {exp["high"] if exp else 0}건은 대장 반영 여부를 본다.',
    ]
    if demo:
        steps.append(f'<b>2순위 - 건물 미교차 대면적 폴리곤 {demo["n"]}건.</b> 정사영상으로 토공·경작·식생 변화를 먼저 구분한다. 철거로 단정하지 않는다.')
    if m["mismatch"]:
        steps.append(f'<b>3순위 - 밝기 방향 불일치 {m["mismatch"]}건.</b> 그림자·식생·계절 요인을 육안으로 재확인한 뒤 유형을 확정한다.')
    questions = cfg.get("compensation", {}).get("open_questions_for_business_team") or []
    baseline_text = f'현재 기준일({ctx["baseline"]})은 잠정값이므로 ' if ctx["baseline"] else ""
    ask = "<b>사업부서 확인 사항.</b> " + baseline_text + "이주대책기준일·권리산정기준일 원문을 확인한다."
    if questions and not brief:
        ask += "<ul>" + "".join(f"<li>{' '.join(str(q).split())}</li>" for q in questions) + "</ul>"
    steps.append(ask)
    if not brief:
        if ctx["pre_share"] is not None:
            steps.append(f'<b>데이터 보강.</b> 기준일({ctx["baseline"]}) 부근 영상 1~2장을 추가해 관측 구간을 기준일 전후로 분리하고, 신축 후보가 많은 동은 고해상 영상으로 재판독한다.')
        steps.append(f'<b>정확도 검증.</b> <code>manual_validation_sample.csv</code>에 사람 판독 라벨을 채워 임계값(현재 임시값 {cfg["analysis"]["change_ratio_new_building_min"]})을 실제 정답 기준으로 보정한다.')
    return '<section><h2>권고 조치</h2><ol class="steps">' + "".join(f"<li>{s}</li>" for s in steps) + "</ol></section>"


def _brief_sections(cfg: dict, m: dict, ctx: dict) -> str:
    types = m["types"]
    new, exp = types.get("NEW_BUILDING"), types.get("EXPANSION_OR_RECONSTRUCTION")
    top = m["dongs"][:3]
    top_table = "".join(
        f'<tr><td>{d["name"]}</td><td class="n">{d["n"]}</td><td class="n">{d["high"]}</td><td class="n">{d["rate"] * 100:.1f}%</td></tr>' for d in top
    )
    findings = "".join(f'<div class="finding"><b>{t}</b><span>{x}</span></div>' for t, x in _findings(m, ctx)[:4])
    out = [
        f'<section><h2>한 줄 결론</h2><p>후보 {m["n"]}건 가운데 우선 확인 대상은 HIGH {m["priority"]["HIGH"]}건이고, 이는 {m["high_sites"]}개 현장에 해당한다. '
        f'변화 면적의 {_pct(m["area_poly"], m["area_total"], 0)}는 건물과 겹치지 않는 폴리곤에서 발생했고, 후보의 {m["top3_cell_share"] * 100:.0f}%는 상위 3개 1㎢ 격자에 모여 있다.</p></section>',
        f'<section><h2>핵심 메시지</h2><div class="findings">{findings}</div></section>',
    ]
    if top:
        out.append(f'<section><h2>우선 조사 지역</h2><table><thead><tr><th>행정동</th><th class="n">후보</th><th class="n">HIGH</th><th class="n">후보율</th></tr></thead>'
                   f"<tbody>{top_table}</tbody></table>"
                   f'<p class="small">후보율 상위 3개 동. 지구 평균 후보율 {m["overall_rate"] * 100:.1f}%. 전체 동별 표는 분석 보고서를 참고한다.</p></section>')
    if m["dongs"]:
        dong_chart = _stack_svg([
            (d["name"], [(d["high"], _PRIORITY_COLORS["HIGH"]), (d["n"] - d["high"], "#9ec5d3")], f'{d["n"]}건 · 후보율 {d["rate"] * 100:.1f}%') for d in m["dongs"]
        ], width=620, label_w=140)
        out.append(f'<section><h2>동별 후보 분포</h2>{dong_chart}<p class="small">붉은색 = HIGH, 청회색 = 그 외(건물 연결 후보).</p></section>')
    out.append(f'<section><h2>변화의 성격: 건수 대비 면적</h2>'
               + _hbar_svg([
                   ("건물 연결 후보", m["n_bld"], "#e63462", f'{m["n_bld"]}건 · {m["area_bld"]:,.0f}㎡'),
                   ("건물 밖 폴리곤", m["n_poly"], "#118ab2", f'{m["n_poly"]}건 · {m["area_poly"]:,.0f}㎡'),
               ], width=620, label_w=140)
               + f'<p class="ins"><b>해석</b> 건수는 건물 연결 후보가 더 많지만 변화 면적의 {_pct(m["area_poly"], m["area_total"], 0)}는 건물 밖 폴리곤이다. '
               '건물 밖 폴리곤은 토지 피복 변화(토공·경작·식생) 가능성이 있어 철거·신축으로 단정하지 않고 정사영상으로 먼저 구분한다.</p></section>')
    if ctx["pre_share"] is not None:
        out.append(f'<section class="warn"><h2>관측 시점 주의</h2><p>비교 구간 {ctx["t1"]} → {ctx["t2"]}({ctx["window_days"]}일) 중 {ctx["pre_days"]}일({ctx["pre_share"] * 100:.0f}%)이 '
                   f'잠정 기준일({ctx["baseline"]}) 이전이다. 신축 후보를 기준일 이후 행위로 해석하려면 기준일 부근 영상으로 구간을 나눠야 하며, 그 전에는 보상 판단 근거로 쓰지 않는다.</p></section>')
    out.append(f'<section><h2>결과를 읽는 방법</h2><ul>'
               f'<li>HIGH = 신축 추정 {new["high"] if new else 0}건 + 증축·개축 추정 {exp["high"] if exp else 0}건. 우선순위 점수 상위 구간.</li>'
               f'<li>신축 추정은 전부 휴리스틱이며 {m["new_mismatch"]}건({_pct(m["new_mismatch"], m["new_flagged"], 0) if m["new_flagged"] else "-"})은 밝기 방향이 기대와 반대다.</li>'
               f'<li>Sentinel-2(10m)로는 100㎡ 미만 변화가 많아({_pct(m["small_change"], m["small_change_of"], 0)}) 건물 단위 확정에는 쓰지 않는다.</li></ul></section>')
    out.append(_actions_section(cfg, m, ctx, brief=True))
    return "".join(out)


def _html(cfg: dict, summary: dict, briefing: bool = False) -> str:
    name = cfg["project"]["short_name"]
    epochs = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    pairs = cfg["analysis"]["comparison_pairs"]
    main_left, main_right = pairs[-1]
    main_code = f"{main_left}_{main_right}"
    total = dict(summary[main_code])
    total["_all"] = summary
    register = json.loads(Path(cfg["paths"]["building_register"]).read_text(encoding="utf-8"))
    buildings = len(gpd.read_file(cfg["paths"]["buildings"]))
    matched = total["register_match_count"]
    aoi = gpd.read_file(cfg["paths"]["aoi"])
    area_m2_calc = float(aoi["area_m2_calc"].sum()) if "area_m2_calc" in aoi.columns else float(aoi.geometry.area.sum())
    official_area = cfg["project"].get("official_area_m2")
    if official_area:
        diff = abs(area_m2_calc - official_area)
        area_note = f"고시(보도) 면적 대비 {diff:,.0f}㎡({diff / official_area * 100:.3f}%) 차이"
        area_value = f"{official_area / 1e6:.3f} km²"
    else:
        area_note = "언론보도 면적은 미검증 - 실측(VWorld) 폴리곤 계산값"
        area_value = f"{area_m2_calc / 1e6:.3f} km²"
    date_span = f'{epochs[main_left]["date"]} → {epochs[main_right]["date"]}'
    title = f"{name} 위성 변화탐지 {'사업 브리핑' if briefing else '분석 보고서'}"
    ctx = _context(cfg, main_left, main_right)
    ctx["date_span"] = date_span
    m = _metrics(cfg, summary, main_code)

    card_rows = [
        ("공식 지구면적", area_value, area_note),
        ("장기 변화후보", total["candidate_count"], f"{date_span}, 건물/비건물 후보"),
        ("중복 제거 현장", total["site_count"], "site_id 기준"),
        ("건축물대장 매칭", f'{matched}/{total["candidate_count"]}', "후보 단위; 행정정보는 판정 보조자료"),
    ]
    if m:
        card_rows += [
            ("HIGH 우선순위", f'{m["priority"]["HIGH"]}건', f'{m["high_sites"]}개 현장 · MEDIUM {m["priority"]["MEDIUM"]} · LOW {m["priority"]["LOW"]}'),
            ("건물 연결 후보", f'{m["n_bld"]}건', f'변화 면적 {m["area_bld"]:,.0f}㎡({_pct(m["area_bld"], m["area_total"])})'),
            ("건물 밖 변화 폴리곤", f'{m["n_poly"]}건', f'변화 면적 {m["area_poly"]:,.0f}㎡({_pct(m["area_poly"], m["area_total"])})'),
            ("공간 군집 (Moran's I)", f'{m["moran"]["I"]:.3f}' if m["moran"] else "-", f'z={m["moran"]["z_sim"]:.1f}, p={m["moran"]["p_sim"]:g}' if m["moran"] else "미산출"),
        ]
    if m:
        grid = f'<div class="grid first">{_cards(card_rows[:4])}</div><div class="grid">{_cards(card_rows[4:])}</div>'
        body = _brief_sections(cfg, m, ctx) if briefing else _report_sections(cfg, m, ctx, buildings, len(register), main_code, matched, total)
    else:
        grid = f'<div class="grid first">{_cards(card_rows)}</div>'
        body = (f'<section><h2>실행 결과</h2><p>후보 {total["candidate_count"]}건, 현장 {total["site_count"]}곳. AOI 내 건물 footprint {buildings:,}동, 수집 건축물대장 {len(register):,}건.</p></section>'
                f'<section class="warn"><h2>기준일 해석</h2><p>{cfg["compensation"]["baseline_date"]} ({cfg["compensation"]["baseline_type"]})은 미확정 후보 기준일이며 보상 판정으로 표현하지 않는다.</p></section>')
    image = f"../comparisons/{main_code}/figures/before_after_change.png"
    figures = ""
    if m and not briefing:
        figs = [("priority_map.png", "우선순위 분포", "HIGH 후보는 소규모 footprint라 지도에서는 작게 보인다."),
                ("hotspots.png", "Getis-Ord Gi* hotspot", "붉은색이 유의한 고밀도 군집, 푸른색이 저밀도 군집이다."),
                ("cadastre_context.png", "지적(필지) 연계", "후보를 필지 경계와 겹쳐 PNU 단위 확인에 쓴다.")]
        cards = "".join(f'<figure><img src="../comparisons/{main_code}/figures/{f}" alt="{t}"><figcaption><b>{t}</b> - {c}</figcaption></figure>' for f, t, c in figs)
        figures = f'<section><h2>공간 분포 지도</h2><div class="figs">{cards}</div></section>'
    checked_at = cfg.get("sources_checked_at", "")
    source_rows = "".join(
        f'<tr><td>{s.get("notice_date", "")}</td><td>{s["title"]}</td><td>{_source_cell(s)}</td></tr>' for s in cfg.get("official_sources", [])
    )
    limits = (f'<section><h2>근거와 한계</h2><table><thead><tr><th>일자</th><th>고시·공고</th><th>번호/출처</th></tr></thead><tbody>{source_rows}</tbody></table>'
              "<p>임계값 0.5는 사람 정답 라벨이 없는 상태의 임시 운영값이며 별도 민감도표와 수동 검수표를 제공한다. "
              "3D는 대상지 및 촬영계획까지만 산출하며 적합 고해상 중복영상이 없어 실제 재구성은 수행하지 않는다.</p></section>")
    hero_cap = "Sentinel-2의 10m 공간해상도 때문에 소규모 증축, 경계부 혼합화소, 수목·토양 계절 변화는 재확인이 필요하다."
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title>
<style>{_CSS}</style></head>
<body><header><small>LH 업무 적용 · 실행 결과 기반 · {checked_at} 확인</small><h1>{title}</h1><p>{name} 공공주택지구의 공식 지구계와 Sentinel-2 L2A {len(cfg['imagery']['epochs'])}개 시점을 이용한 기술 검토다. 자동 결과는 현장 확인 우선순위를 위한 후보이며 불법 여부나 보상 대상 여부를 확정하지 않는다.</p></header>
<section class="hero-img"><div class="hero-inner"><h2>변화탐지 비교 지도</h2><p class="sub">{date_span} · T1 / T2 / Change Probability / Classification</p><img src="{image}" alt="변화탐지 비교 지도 - T1, T2, Change Probability, Classification"><p class="cap">{hero_cap}</p></div></section>
<main>{grid}{body}{figures}{limits}</main></body></html>'''


def generate(cfg: dict) -> dict:
    output = Path(cfg["paths"]["reports"])
    summary = json.loads((Path(cfg["paths"]["output_root"]) / "analysis_summary.json").read_text(encoding="utf-8"))
    pid = cfg["project"]["id"]
    outputs = {}
    for briefing, filename in [(False, f"{pid}_analysis_report.html"), (True, f"{pid}_briefing.html")]:
        path = output / filename
        path.write_text(_html(cfg, summary, briefing), encoding="utf-8")
        outputs[filename] = str(path)
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    cfg = load_project(args.project)
    threshold_calibration(cfg)
    print(json.dumps(generate(cfg), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
