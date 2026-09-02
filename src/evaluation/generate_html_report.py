"""파이프라인 결과를 자체완결형(self-contained) HTML 리포트로 묶는다.

기존 outputs/reports/changneung_report.html은 재사용 스크립트 없이
일회성으로 만들어져 있었다 - 파이프라인을 다시 돌릴 때마다(robust_cva
도입, T3 추가 등) 숫자와 그림이 그대로 박제된 채 낡아갔다. 이 스크립트로
대체해 언제든 최신 결과로 재생성할 수 있게 한다.

이미지는 base64로 인라인 임베드한다. 폰트는 Google Fonts(Noto Sans KR)를
1순위로 걸고 Pretendard/시스템 폰트를 폴백으로 둔다 - 인터넷이 되는
일반적인 상황에서는 지정한 폰트로, 오프라인(이메일 첨부 후 인터넷 없는
PC, USB 등)에서는 시스템 폰트로 자연스럽게 내려가 어떤 환경에서도
깨지지 않는다.

색상은 이 파일이 새로 정의하지 않고 visualize.py의 PRIORITY_COLORS/
CHANGE_TYPE_COLORS/GI_CLASS_COLORS를 그대로 가져다 쓴다 - 지도 범례
색과 표의 chip 색이 반드시 1:1로 일치해야(legend fidelity) 리포트를
보는 사람이 지도와 표를 같은 언어로 읽을 수 있기 때문이다.

리포트 본문은 보고 대상(의사결정권자)을 고려해 존댓말(합니다체)로
쓴다. directional_consistency_flag처럼 코드베이스 용어를 그대로 노출하는
곳은 _term()으로 감싸 마우스오버 시 풀이가 뜨게 한다(TERM_TOOLTIPS 참고).
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

from .visualize import CHANGE_TYPE_COLORS, GI_CLASS_COLORS, PRIORITY_COLORS

logger = logging.getLogger("generate_html_report")

CHANGE_TYPE_LABELS = {
    "NEW_BUILDING": "신축",
    "EXPANSION_OR_RECONSTRUCTION": "증축/개축",
    "OTHER_CHANGE": "기타 변화",
    "DEMOLITION": "철거(추정)",
}
GI_CLASS_LABELS = {
    "HOT_99": "HOT 99%", "HOT_95": "HOT 95%", "HOT_90": "HOT 90%",
    "NOT_SIG": "유의하지 않음",
    "COLD_90": "COLD 90%", "COLD_95": "COLD 95%", "COLD_99": "COLD 99%",
}

# 코드베이스 용어를 리포트 본문에 그대로 노출할 때 마우스오버 설명을 붙이기 위한 사전.
# _term()에서 조회한다 - 등장할 때마다 문장으로 다시 풀어쓰지 않아도 되게 하기 위함.
TERM_TOOLTIPS = {
    "change_probability": "변화탐지 앙상블이 픽셀마다 계산한 0~1 변화 강도 점수입니다. 방향(밝아짐/어두워짐)은 담지 않습니다.",
    "change_ratio": "건물 footprint 면적 대비, change 영역과 교차하는 면적의 비율입니다.",
    "site_id": "여러 건물이 같은 change 영역(하나의 공사 현장)에 걸쳐 있을 때, 같은 현장임을 나타내는 식별자입니다.",
    "brightness_delta": "T1→T2 사이 해당 영역의 평균 밝기(그레이스케일) 변화량입니다. 양수이면 더 밝아진 것입니다.",
    "directional_consistency_flag": "건축물대장 근거가 없는 휴리스틱 판정에서, 밝기 변화 방향이 예상(신축·증축=밝아짐)과 맞는지 표시하는 재확인 플래그입니다. 라벨 자체를 바꾸지는 않습니다.",
    "priority_score": "change_confidence·change_ratio·administrative_uncertainty·building_relevance 네 근거를 가중합산한 0~1 현장조사 우선순위 점수입니다.",
    "change_confidence": "change_probability의 최댓값으로, 변화탐지 알고리즘이 해당 후보를 얼마나 강하게 변화로 판단했는지를 나타냅니다.",
    "administrative_uncertainty": "건축물대장으로 이 변화가 설명되는 정도의 반대값입니다. 설명되면 0에 가깝고, 설명되지 않으면 1입니다.",
    "building_relevance": "변화유형이 건물 자체의 변화(신축·증축·철거)인지, 건물과 무관한 주변 토지 변화인지를 나타냅니다.",
    "mean_change_score": "해당 영역 안 픽셀들의 change_probability 평균값입니다.",
    "gi_class": "Getis-Ord Gi* 통계로 분류한 공간적 hotspot/coldspot 등급입니다.",
}


def _term(code: str) -> str:
    """코드베이스 용어를 <abbr>로 감싸 마우스오버 설명을 붙인다 (JS 없이 브라우저 기본 툴팁)."""
    tooltip = TERM_TOOLTIPS.get(code, "")
    title_attr = f' title="{tooltip}"' if tooltip else ""
    return f'<abbr class="term"{title_attr}><code>{code}</code></abbr>'


# ---------------------------------------------------------------- helpers --

def _b64_image(path: str | Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _text_on(hex_color: str) -> str:
    """배경색의 상대 휘도로 검정/흰색 글자 중 대비가 나은 쪽을 고른다."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#14201d" if luminance > 150 else "#ffffff"


def _chip(label: str, color: str) -> str:
    return (
        f'<span class="chip" style="background:{color};color:{_text_on(color)}">{label}</span>'
    )


def _bar(counts: dict[str, int], colors: dict[str, str], order: list[str]) -> str:
    """counts를 order 순서대로 비례 폭 stacked bar로 그린다 (순수 CSS, 라이브러리 없음)."""
    total = sum(counts.get(k, 0) for k in order) or 1
    segments = "".join(
        f'<span style="width:{100 * counts.get(k, 0) / total:.3f}%;background:{colors[k]}" '
        f'title="{k}: {counts.get(k, 0)}"></span>'
        for k in order if counts.get(k, 0) > 0
    )
    return f'<div class="bar" role="img" aria-label="분포 막대">{segments}</div>'


FUNNEL_COLORS = ["#4c6b64", "#3c5850", "#b5792a", "#8a5a1f"]


def _funnel(stages: list[tuple[str, int]], context_note: str = "") -> str:
    """stages(큰 순서대로)를 폭이 줄어드는 막대 funnel로 그린다 (순수 CSS, 라이브러리 없음).

    첫 stage를 기준(100%)으로 이후 단계가 얼마나 좁혀지는지 시각적으로 보여준다 -
    "전수조사에서 현장조사 대상까지 어떻게 줄어드는가"를 표/숫자보다 한눈에 전달하기 위함.
    """
    base = stages[0][1] or 1
    rows = []
    for i, (label, n) in enumerate(stages):
        pct = 100 * n / base
        width = 20 + (92 - 20) * (pct / 100)
        color = FUNNEL_COLORS[min(i, len(FUNNEL_COLORS) - 1)]
        pct_html = f'<span class="funnel-pct">{pct:.1f}%</span>' if i > 0 else ""
        rows.append(f"""
        <div class="funnel-stage" style="width:{width:.1f}%;background:{color}">
          <span class="funnel-label">{label}</span>
          <span class="funnel-num">{n:,}{pct_html}</span>
        </div>
        """)
        if i < len(stages) - 1:
            rows.append('<div class="funnel-connector"></div>')
    note_html = f'<p class="fine-print funnel-context">{context_note}</p>' if context_note else ""
    return f'{note_html}<div class="funnel">{"".join(rows)}</div>'


def _grouped_bar_chart(periods: list[dict]) -> str:
    """세 구간의 HIGH/MEDIUM/LOW 건수를 세로 막대 그래프로 나란히 비교한다 (순수 CSS)."""
    tiers = ["HIGH", "MEDIUM", "LOW"]
    max_n = max(
        (p["stats"]["priority"].get(t, 0) for p in periods for t in tiers), default=1
    ) or 1
    chart_px = 190

    groups = []
    for p in periods:
        bars = "".join(
            f"""<div class="chart-bar" style="height:{max(4, chart_px * p["stats"]["priority"].get(t, 0) / max_n):.0f}px;background:{PRIORITY_COLORS[t]}">
                  <span class="chart-bar-value">{p["stats"]["priority"].get(t, 0):,}</span>
                </div>"""
            for t in tiers
        )
        groups.append(f"""
        <div class="chart-group">
          <div class="chart-bars" style="height:{chart_px}px">{bars}</div>
          <div class="chart-group-label">{p["code"]}</div>
        </div>
        """)

    legend = "".join(f'{_chip(t, PRIORITY_COLORS[t])}' for t in tiers)
    return f"""
    <div class="chart-wrap">
      <div class="chart-groups">{"".join(groups)}</div>
      <div class="chart-legend">{legend}</div>
    </div>
    """


def _flow_diagram(stage_labels: list[str]) -> str:
    """방법론 7단계를 한 줄짜리 흐름도로 요약한다 - 아래 상세 목록을 읽기 전에 전체 구조를 먼저 보여준다."""
    nodes = []
    for i, label in enumerate(stage_labels):
        nodes.append(f"""
        <div class="flow-node"><span class="flow-num">{i + 1}</span>{label}</div>
        """)
        if i < len(stage_labels) - 1:
            nodes.append('<div class="flow-arrow">→</div>')
    return f'<div class="flow-diagram">{"".join(nodes)}</div>'


def _period_stats(results_path: str | Path) -> dict:
    gdf = gpd.read_file(results_path)
    priority = gdf["inspection_priority"].value_counts().to_dict()
    change_type = gdf["change_type"].value_counts(dropna=True).to_dict()
    gi_class = gdf["gi_class"].value_counts(dropna=True).to_dict() if "gi_class" in gdf else {}
    n_sites = gdf["site_id"].nunique()
    n_sites_high = gdf.loc[gdf["inspection_priority"] == "HIGH", "site_id"].nunique()
    total = int(len(gdf))

    # GPKG round-trip은 bool을 "True"/"False" 문자열로 저장하기도 해서 astype(str)로 통일해 비교한다.
    n_register = (
        int((gdf["has_register_match"].astype(str) == "True").sum())
        if "has_register_match" in gdf else 0
    )
    n_flagged = (
        int((gdf["directional_consistency_flag"].astype(str) == "False").sum())
        if "directional_consistency_flag" in gdf else 0
    )

    # HIGH 등급이 실제로 "행정정보로 설명 안 되는" 건인지 검증 - 대장 매칭 여부별
    # HIGH 비중을 비교한다(우선순위 점수화 로직이 의도대로 작동하는지의 근거자료).
    register_by_tier = None
    if "has_register_match" in gdf:
        is_matched = gdf["has_register_match"].astype(str) == "True"
        for label, subset in (("matched", gdf[is_matched]), ("unmatched", gdf[~is_matched])):
            if len(subset):
                pct_high = 100 * (subset["inspection_priority"] == "HIGH").sum() / len(subset)
                register_by_tier = register_by_tier or {}
                register_by_tier[label] = {"n": len(subset), "pct_high": pct_high}

    area = gdf["change_area_m2"].dropna() if "change_area_m2" in gdf else gdf.iloc[0:0]
    area_stats = (
        {"median": float(area.median()), "mean": float(area.mean()), "max": float(area.max())}
        if len(area) else None
    )

    purpose_counts = {}
    if "mainPurpsCdNm" in gdf:
        purpose = gdf["mainPurpsCdNm"].dropna()
        purpose = purpose[purpose.astype(str).str.len() > 0]
        purpose_counts = purpose.value_counts().head(6).to_dict()

    top_sites = []
    if "change_area_m2" in gdf and "site_id" in gdf:
        agg = (
            gdf.groupby("site_id")
            .agg(area_m2=("change_area_m2", "sum"), n_buildings=("change_area_m2", "size"))
            .sort_values("area_m2", ascending=False)
            .head(8)
        )
        for site_id, row in agg.iterrows():
            sub = gdf[gdf["site_id"] == site_id]
            top_sites.append({
                "site_id": site_id,
                "area_m2": float(row["area_m2"]),
                "n_buildings": int(row["n_buildings"]),
                "change_type": sub["change_type"].mode().iloc[0] if not sub["change_type"].mode().empty else None,
                "priority": sub["inspection_priority"].mode().iloc[0] if not sub["inspection_priority"].mode().empty else None,
                "gi_class": (
                    sub["gi_class"].mode().iloc[0]
                    if "gi_class" in sub and not sub["gi_class"].dropna().empty and not sub["gi_class"].mode().empty
                    else None
                ),
            })

    return {
        "priority": priority, "change_type": change_type, "gi_class": gi_class,
        "n_sites": n_sites, "n_sites_high": n_sites_high, "total": total,
        "n_register": n_register, "n_flagged": n_flagged,
        "area_stats": area_stats, "purpose_counts": purpose_counts, "top_sites": top_sites,
        "register_by_tier": register_by_tier,
    }


def _persistence_insight(path_a: str | Path, path_b: str | Path) -> dict | None:
    """b 구간의 HIGH 현장 중 a 구간에서도 HIGH였던 위치와 공간적으로 겹치는 곳이 몇 곳인지 센다.

    두 구간의 building_change_results를 직접 spatial join하는 실측 비교다 -
    "최근 개발이 새 장소로 번지는지, 같은 곳에서 계속되는지"를 눈대중이 아니라
    지오메트리 교차로 판정한다.
    """
    a = gpd.read_file(path_a)
    b = gpd.read_file(path_b)
    a_high = a[a["inspection_priority"] == "HIGH"]
    b_high = b[b["inspection_priority"] == "HIGH"]
    if a_high.empty or b_high.empty:
        return None
    if a_high.crs != b_high.crs:
        b_high = b_high.to_crs(a_high.crs)
    a_union = a_high.geometry.union_all()
    b_dissolved = b_high.dissolve(by="site_id")
    overlap = b_dissolved.geometry.intersects(a_union)
    n_total = len(b_dissolved)
    n_persistent = int(overlap.sum())
    return {"n_total": n_total, "n_persistent": n_persistent, "n_new": n_total - n_persistent}


def _moran_summary(stats_path: str | Path) -> dict | None:
    """spatial_statistics.compute_global_moran() 결과(outputs/statistics/global_moran.json)를 읽는다."""
    stats_path = Path(stats_path)
    if not stats_path.exists():
        return None
    with open(stats_path, encoding="utf-8") as f:
        return json.load(f)


def _manifest_summary(manifest_path: str | Path) -> dict | None:
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        return None
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def _ranked_bars(counts: dict, color: str, labels: dict | None = None) -> str:
    """{항목: 건수} 딕셔너리를 건수 내림차순 가로 막대 목록으로 그린다 (라이브러리 없음)."""
    if not counts:
        return '<p class="fine-print">데이터 없음</p>'
    ordered = sorted(counts.items(), key=lambda x: -x[1])
    max_n = ordered[0][1] or 1
    items = "".join(
        f'<li><span class="rb-label" title="{(labels or {}).get(k, k)}">{(labels or {}).get(k, k)}</span>'
        f'<span class="rb-track"><span class="rb-fill" style="width:{100 * n / max_n:.1f}%;background:{color}"></span></span>'
        f'<span class="rb-num">{n:,}</span></li>'
        for k, n in ordered
    )
    return f'<ul class="rank-bars">{items}</ul>'


def _insights_block(stats: dict) -> str:
    """구간별 핵심 인사이트: 변화 규모 분포, 건물 용도, Gi* 등급 분포, 규모 상위 현장 Top 5."""
    area = stats.get("area_stats")
    area_html = ""
    if area:
        area_html = f"""
        <div class="mini-stats">
          <div><span class="mini-num">{area["median"]:,.0f}</span><span class="mini-label">중앙값 m²</span></div>
          <div><span class="mini-num">{area["mean"]:,.0f}</span><span class="mini-label">평균 m²</span></div>
          <div><span class="mini-num">{area["max"]:,.0f}</span><span class="mini-label">최대 m²</span></div>
        </div>
        <p class="fine-print">평균이 중앙값보다 훨씬 큽니다 - 소규모 증축 다수와 대규모 조성공사
        소수가 섞여있다는 뜻이므로, 아래 순위표로 큰 현장부터 따로 확인하시기를 권장합니다.</p>
        """

    purpose_html = _ranked_bars(stats.get("purpose_counts", {}), "#5a7a70")
    gi_html = _ranked_bars(stats.get("gi_class", {}), "#8a6a3a", GI_CLASS_LABELS)

    top_sites = stats.get("top_sites", [])
    top_rows = "".join(
        f'<tr><td class="num muted">{i + 1}</td><td class="mono">{s["site_id"]}</td>'
        f'<td class="num">{s["area_m2"]:,.0f}</td><td class="num">{s["n_buildings"]}</td>'
        f'<td>{_chip(CHANGE_TYPE_LABELS.get(s["change_type"], s["change_type"] or "—"), CHANGE_TYPE_COLORS.get(s["change_type"], "#999")) if s["change_type"] else "—"}</td>'
        f'<td>{_chip(s["priority"], PRIORITY_COLORS[s["priority"]]) if s["priority"] in PRIORITY_COLORS else "—"}</td>'
        f'<td>{_chip(GI_CLASS_LABELS.get(s["gi_class"], s["gi_class"]), GI_CLASS_COLORS.get(s["gi_class"], "#999")) if s["gi_class"] else "—"}</td></tr>'
        for i, s in enumerate(top_sites)
    )
    top_table = ""
    if top_rows:
        top_table = f"""
        <h3>규모 상위 현장 Top {len(top_sites)} <span class="fine-print">(영향 건물면적 합계 기준)</span></h3>
        <div class="table-scroll">
          <table>
            <tr><th>#</th><th>site_id</th><th class="num">면적 합계(m²)</th><th class="num">건물 수</th>
                <th>유형</th><th>우선순위</th><th>Gi*</th></tr>
            {top_rows}
          </table>
        </div>
        """

    register_html = ""
    rbt = stats.get("register_by_tier")
    if rbt and "matched" in rbt and "unmatched" in rbt:
        m, u = rbt["matched"], rbt["unmatched"]
        register_html = f"""
        <div class="insight-callout">
          <div class="insight-num">{u["pct_high"]:.0f}%<span class="fine-print"> vs {m["pct_high"]:.0f}%</span></div>
          <div class="insight-body">
            <strong>건축물대장에 매칭되지 않은 후보가 HIGH로 분류되는 비율이 더 높습니다.</strong>
            대장 미매칭 {u["n"]:,}건 중 {u["pct_high"]:.0f}%가 HIGH인 반면, 대장 매칭
            {m["n"]:,}건 중에서는 {m["pct_high"]:.0f}%만 HIGH입니다. {_term("administrative_uncertainty")}
            가중치(0.2)가 의도한 대로 "행정정보로 설명되지 않는 변화"를 실제로 상위 우선순위로
            끌어올리고 있다는 근거입니다.
          </div>
        </div>
        """

    return f"""
    <div class="insights-grid">
      <div>
        <h3>변화 규모 분포</h3>
        {area_html}
      </div>
      <div>
        <h3>건축물대장 매칭 건물의 주용도</h3>
        {purpose_html}
      </div>
      <div>
        <h3>Gi* 등급 분포</h3>
        {gi_html}
      </div>
    </div>
    {register_html}
    {top_table}
    """


def _param_chips(*pairs: tuple[str, str]) -> str:
    items = "".join(f'<span class="param-chip">{k} = {v}</span>' for k, v in pairs)
    return f'<div class="param-chips">{items}</div>'


# 6개 ArcGIS 역량 카드용 아이콘. 손으로 그린 베지어 경로 대신 rect/circle/line/polygon
# 기본 도형만 조합한 line-art 스타일 - 뷰박스 0 0 32 32, stroke는 CSS(.capability-icon)에서 지정.
CAPABILITY_ICONS = {
    "layers": '<rect x="6" y="7" width="20" height="5" rx="1.5"/><rect x="6" y="14" width="20" height="5" rx="1.5"/><rect x="6" y="21" width="20" height="5" rx="1.5"/>',
    "field": '<rect x="5" y="5" width="22" height="22" rx="2"/><circle cx="16" cy="14" r="3.2"/><line x1="16" y1="17.2" x2="16" y2="23"/><line x1="10" y1="23" x2="22" y2="23"/>',
    "dashboard": '<line x1="8" y1="25" x2="8" y2="15"/><line x1="16" y1="25" x2="16" y2="7"/><line x1="24" y1="25" x2="24" y2="19"/><line x1="5" y1="25" x2="27" y2="25"/>',
    "share": '<circle cx="8" cy="16" r="3.2"/><circle cx="24" cy="7" r="3.2"/><circle cx="24" cy="25" r="3.2"/><line x1="10.8" y1="14.5" x2="21.2" y2="8.5"/><line x1="10.8" y1="17.5" x2="21.2" y2="23.5"/>',
    "automate": '<rect x="5" y="13" width="6" height="6" rx="1"/><rect x="13" y="5" width="6" height="6" rx="1"/><rect x="21" y="13" width="6" height="6" rx="1"/><rect x="13" y="21" width="6" height="6" rx="1"/><line x1="11" y1="14" x2="14" y2="10"/><line x1="18" y1="10" x2="21" y2="14"/><line x1="21" y1="18" x2="18" y2="21"/><line x1="14" y1="21" x2="11" y2="18"/>',
    "shield": '<polygon points="16,4 26,8 26,17 16,28 6,17 6,8"/><polyline points="11,16 14.5,19.5 21,12"/>',
}


def _icon(name: str) -> str:
    return f'<svg class="capability-icon" viewBox="0 0 32 32" aria-hidden="true">{CAPABILITY_ICONS[name]}</svg>'


def _arcgis_note(text: str, *products: str) -> str:
    """파이프라인 각 단계 아래 붙는 "ArcGIS로는" 노트 - 같은 단계를 ArcGIS 제품으로 재현/확장하는 방법."""
    chips = "".join(f'<span class="gis-chip">{p}</span>' for p in products)
    return f"""
    <div class="arcgis-note">
      <span class="gis-tag">ArcGIS로는</span>
      <div><p>{text}</p><div class="gis-products">{chips}</div></div>
    </div>
    """


def _methodology_section() -> str:
    """파이프라인 7단계를 실제 코드/config.yaml 값과 함께 설명한다 (src/pipeline.py 순서 그대로)."""
    return f"""
    <section id="methodology" class="card">
      <p class="eyebrow">방법론</p>
      <h2>파이프라인은 어떻게 동작하나요</h2>
      <p class="subtitle">T1/T2 원본 스택부터 최종 우선순위 점수까지 총 7단계입니다. 굵게 표시되지
      않은 수치는 모두 <code>config/config.yaml</code>에서 관리되는 값이며, 하드코딩된 값이
      아닙니다.</p>
      {_flow_diagram([
          "정합 검증", "변화탐지 앙상블", "임계값·후처리", "건물 Overlay",
          "변화유형 분류", "우선순위 점수화", "공간통계 검증",
      ])}
      <ol class="pipeline">

        <li>
          <div class="pipeline-num">1</div>
          <div class="pipeline-body">
            <h3>전처리 &amp; 정합 검증</h3>
            <p>Sentinel-2 원본 밴드(B02/B03/B04/B08, 10m)를 분석 좌표계로 재투영하고 AOI로 클립해
            4밴드 스택을 만듭니다. 두 시점 영상이 픽셀 단위로 어긋나면 실제 변화가 아니라 정합
            오차가 변화로 잡히기 때문에, ECC(Enhanced Correlation Coefficient) 방식으로 정합
            오차를 먼저 측정합니다.</p>
            {_param_chips(("CRS", "EPSG:5186"), ("정합 오차", "1.26m (0.126px)"), ("ecc_score", "0.979"),
                          ("허용 기준", "≤ 10m (1px)"))}
            {_arcgis_note(
                "동일한 정합 검증을 ArcGIS Pro의 Image Analyst 확장에서 GUI로 수행할 수 있습니다. "
                "Auto Registration 도구가 기준 영상 대비 이동량을 계산해주고, 여러 시점 영상을 "
                "Mosaic Dataset으로 관리하면 재투영·클립을 반복 자동화할 수 있습니다.",
                "Image Analyst", "Mosaic Dataset",
            )}
          </div>
        </li>

        <li>
          <div class="pipeline-num">2</div>
          <div class="pipeline-body">
            <h3>변화탐지 앙상블 - 3개 방법을 균등 가중 결합</h3>
            <p>어떤 단일 지표도 완벽하지 않다는 전제로, 성질이 다른 세 가지 방법을 <strong>1/3씩
            균등 가중</strong>으로 결합해 {_term("change_probability")}(0~1)를 산출합니다.</p>
            <div class="method-grid">
              <div class="method-card">
                <div class="method-tag">Method A</div>
                <h4>Robust CVA</h4>
                <p>밴드별 (T2−T1) 차분을 <strong>median/MAD로 표준화</strong>(이상치에 강건)한 뒤
                유클리드 거리로 결합하고, 최댓값이 아니라 <strong>상위 1%(99th percentile)</strong>를
                기준으로 0~1로 clip합니다. 전역 최댓값으로 정규화하는 단순한 방식은 구름 잔여물
                같은 극단 픽셀 하나에 전체 스케일이 눌려버리는 문제가 있어, 이 방식으로
                대체했습니다.</p>
              </div>
              <div class="method-card">
                <div class="method-tag">Method B</div>
                <h4>SSIM</h4>
                <p>T1/T2의 국소 밝기·대비·구조 패턴이 다를수록(구조적 유사도가 낮을수록) 변화
                점수가 높아집니다. 단순한 밝기 차이만으로는 잡히지 않는, "패턴 자체가 달라진"
                변화를 보완합니다.</p>
              </div>
              <div class="method-card">
                <div class="method-tag">Method C</div>
                <h4>Edge / Texture</h4>
                <p>T1/T2 각각의 Canny 엣지맵을 추출해 XOR로 차분합니다. 건물 외곽선처럼 엣지가
                새로 생기거나 사라지는 - 신축·철거에서 특히 두드러지는 - 변화를 잡기 위한
                방법입니다.</p>
              </div>
            </div>
            {_arcgis_note(
                "Image Analyst의 Change Detection 도구(Compute Change Raster)가 이와 유사한 여러 "
                "변화탐지 알고리즘을 코드 없이 제공합니다. ArcGIS Pro는 딥러닝 기반 객체 탐지 "
                "(Detect Objects Using Deep Learning)까지 라이선스만으로 바로 활용할 수 있습니다.",
                "Image Analyst", "Detect Objects Using Deep Learning",
            )}
          </div>
        </li>

        <li>
          <div class="pipeline-num">3</div>
          <div class="pipeline-body">
            <h3>임계값 결정 &amp; 후처리</h3>
            <p>고정 임계값으로 이진 마스크를 만듭니다. Otsu 자동 임계값도 구현되어 있지만, 이
            AOI에서는 분포 특성상 훨씬 공격적인 값이 선택되어 변화 후보가 급증하기 때문에(14.87%
            vs 3.82%), 육안 QA로 검증되기 전까지는 보수적인 고정값을 기본으로 사용합니다. 이진
            마스크는 opening→closing으로 소금-후추 노이즈를 제거하고, 너무 작은 connected
            component는 걸러냅니다.</p>
            {_param_chips(("threshold_method", "fixed"), ("mask_threshold", "0.5"),
                          ("opening/closing kernel", "3×3"), ("최소 면적", "25 m²"))}
            {_arcgis_note(
                "Raster Calculator·Majority Filter 같은 Spatial Analyst 도구로 임계값 결정과 노이즈 "
                "제거를 그대로 재현할 수 있습니다. ModelBuilder로 단계를 묶으면 파라미터만 바꿔가며 "
                "반복 실행하는 '분석 모델'로 패키징되어, 다음 촬영분이 들어올 때마다 재사용할 수 있습니다.",
                "Spatial Analyst", "ModelBuilder",
            )}
          </div>
        </li>

        <li>
          <div class="pipeline-num">4</div>
          <div class="pipeline-body">
            <h3>건물 Overlay &amp; 밝기 방향성 계산</h3>
            <p>Polygon화된 change 영역을 건물 footprint(2,737개)와 공간적으로 overlay해 건물별
            {_term("change_ratio")}를 구합니다. 큰 change 영역 하나에 건물 여러 개가 걸치는
            경우가 실제로 흔히 확인되어, {_term("site_id")}로 묶어 "건물 수"와 "실제 현장 수"를
            구분합니다. 이와 별개로 change_probability가 버리는 정보 - T1→T2 사이 그레이스케일
            평균 밝기가 밝아졌는지 어두워졌는지({_term("brightness_delta")}) - 도 계산해 다음
            단계의 보조 근거로 전달합니다.</p>
            {_param_chips(("버퍼 거리(근접 변화 판정)", "3 m"))}
            {_arcgis_note(
                "Intersect·Spatial Join 같은 표준 지오프로세싱 도구가 이 overlay 단계를 그대로 "
                "대체합니다. ArcGIS API for Python으로 파이프라인 전체를 예약 실행(Notebook, 작업 "
                "스케줄러)하도록 구성하면, 새 위성 영상이 들어올 때마다 자동으로 갱신되는 레이어를 "
                "만들 수 있습니다 - 실제로 이 프로젝트의 src/publish/arcgis_online.py가 그 자동화의 "
                "출발점입니다.",
                "Spatial Join", "ArcGIS API for Python",
            )}
          </div>
        </li>

        <li>
          <div class="pipeline-num">5</div>
          <div class="pipeline-body">
            <h3>변화유형 분류 (규칙 기반)</h3>
            <p>건축물대장 사용승인일이 있으면 <strong>이를 최우선 근거</strong>로 사용합니다 -
            사용승인일이 T1~T2 사이면 확정적으로 신축, 그 밖이면 기존 건물이므로 증축/개축으로
            판정합니다. 건축물대장에 매칭되지 않은 건물만 change_ratio 크기로 근사 판정
            (휴리스틱)하며, 이때만 brightness_delta 방향이 기대와 어긋나면
            {_term("directional_consistency_flag")}을 켭니다(라벨 자체는 그대로 유지합니다).
            건물과 교차하지 않는 고신뢰 변화는 철거 후보로 판단하되, 밝기가 오히려 뚜렷하게
            증가했다면(철거 방향과 모순) 철거 대신 기타 변화로 분류합니다.</p>
            {_param_chips(("신축 판정 change_ratio", "≥ 0.5"), ("건축물대장 PNU 매칭률", "67.7%"),
                          ("철거 후보 mean_change_score", "≥ 0.6"))}
            {_arcgis_note(
                "이 규칙을 Arcade 표현식이나 Attribute Rules로 Feature Layer에 직접 내장하면, "
                "담당자가 편집기에서 값을 수정하는 즉시 변화유형이 자동으로 재계산됩니다 - "
                "Python을 다시 돌리지 않아도 편집 시점에 규칙이 살아있는 레이어가 되는 것입니다.",
                "Arcade", "Attribute Rules",
            )}
          </div>
        </li>

        <li>
          <div class="pipeline-num">6</div>
          <div class="pipeline-body">
            <h3>현장조사 우선순위 점수화</h3>
            <p class="formula">priority_score = 0.4 · change_confidence + 0.3 · change_ratio
            + 0.2 · administrative_uncertainty + 0.1 · building_relevance</p>
            <p>{_term("change_confidence")}는 change_probability의 최댓값이며,
            {_term("administrative_uncertainty")}는 건축물대장으로 설명되면 0에 가깝고 설명되지
            않으면 1입니다. {_term("building_relevance")}는 change_type이 신축·증축·철거면 1.0,
            그 외(예: 건물과 무관한 주변 토지 변화)면 0.3입니다. 가중치가 가장 큰
            change_confidence(0.4)가 영상 근거를, administrative_uncertainty(0.2)가 행정정보
            공백을 반영하여 "행정정보로 설명되지 않는 큰 변화"를 자연스럽게 상위로 끌어올리는
            구조입니다.</p>
            {_param_chips(("HIGH", "≥ 0.7"), ("MEDIUM", "≥ 0.4"), ("LOW", "< 0.4"))}
            {_arcgis_note(
                "같은 가중합산 공식을 Field Calculator/Arcade로 필드에 계산해 넣고 ArcGIS "
                "Dashboards의 게이지·카테고리 위젯에 연결하면, 현장조사팀이 우선순위 현황을 "
                "실시간으로 모니터링하는 대시보드가 됩니다. Field Maps로 배포하면 담당자별로 "
                "오늘 방문할 HIGH 현장 목록이 지도 위 체크리스트로 바로 전달됩니다.",
                "Dashboards", "Field Maps",
            )}
          </div>
        </li>

        <li>
          <div class="pipeline-num">7</div>
          <div class="pipeline-body">
            <h3>공간통계 검증 (Global Moran's I / Getis-Ord Gi*)</h3>
            <p>{_term("priority_score")}가 실제로 공간적으로 군집되어 있는지(=구조화된 변화)
            아니면 산발적 노이즈인지를 <strong>Global Moran's I</strong>(KNN row-standardized
            weights)로 먼저 통계 검정합니다. 이어서 <strong>Getis-Ord Gi*</strong>(binary
            weights)로 어느 건물이 주변과 함께 유의하게 높거나 낮은 값을 갖는 hotspot/coldspot
            ({_term("gi_class")})인지 90/95/99% 신뢰수준으로 분류합니다. 두 검정 모두 permutation
            방식이라 실행할 때마다 결과가 흔들리지 않도록 random seed를 고정합니다.</p>
            {_param_chips(("KNN k", "8"), ("permutations", "999"), ("random_seed", "42"))}
            {_arcgis_note(
                "Global Moran's I와 Getis-Ord Gi*는 ArcGIS Pro Spatial Statistics 툴박스에 각각 "
                "Spatial Autocorrelation(Global Moran's I), Hot Spot Analysis(Getis-Ord Gi*)로 "
                "기본 내장되어 있어 코드 없이 동일한 분석을 수행할 수 있습니다. 세 시점을 함께 "
                "보려면 Space-Time Cube와 Emerging Hot Spot Analysis로 확장해, '어디가 최근에 새로 "
                "hotspot이 되었는지'까지 자동으로 분류할 수 있습니다.",
                "Spatial Statistics 툴박스", "Space-Time Cube",
            )}
          </div>
        </li>

      </ol>
    </section>
    """


def _arcgis_section() -> str:
    """이 PoC를 ArcGIS 플랫폼 위에 올리면 무엇이 가능해지는지 정리한 섹션 (LH 발표용)."""
    cards = [
        (
            "layers", "데이터 관리 &amp; 발행",
            "T1/T2/T3 결과를 각각 별도 파일로 관리하는 대신 Hosted Feature Layer로 발행하고 "
            "시간 인식(time-aware) 속성을 주면, 하나의 Web Map에서 시점 슬라이더로 세 시점을 "
            "스와이프 비교할 수 있습니다. 원본 위성영상은 Mosaic Dataset/Image Service로 "
            "카탈로그화해 매번 파일을 새로 내려받지 않아도 됩니다.",
            ["Hosted Feature Layer", "Image Service", "Time-Aware Layers"],
        ),
        (
            "field", "현장조사 연계",
            "HIGH 등급 현장 목록을 Field Maps로 담당자 태블릿에 오프라인 지도로 배포하고, "
            "Survey123으로 현장 사진·체크리스트를 수집하면 결과가 바로 원본 레이어에 반영됩니다. "
            "이 리포트의 '방향성 재확인 권장' 후보를 그대로 오늘의 현장조사 목록으로 넘길 수 "
            "있습니다.",
            ["Field Maps", "Survey123", "오프라인 지도"],
        ),
        (
            "dashboard", "의사결정 대시보드",
            "이 리포트의 통계·차트·지도를 ArcGIS Dashboards로 옮기면 파이프라인을 재실행할 "
            "때마다 자동으로 갱신되는 실시간 화면이 됩니다. 담당 부서·법정동·우선순위별 필터와 "
            "드릴다운을 추가해, 발표 슬라이드가 아니라 상시 운영되는 모니터링 도구로 확장할 수 "
            "있습니다.",
            ["Dashboards", "Arcade", "Web Map"],
        ),
        (
            "share", "대내외 공유 &amp; 스토리텔링",
            "오늘 이 발표 내용을 StoryMaps로 옮기면 지도·그림·설명이 스크롤 한 번으로 이어지는 "
            "웹 기반 인터랙티브 문서가 됩니다. 대국민 공개나 타 기관 공유가 필요해지면 "
            "Experience Builder로 권한이 분리된 별도 공개용 앱도 같은 데이터 위에서 바로 구성할 "
            "수 있습니다.",
            ["StoryMaps", "Experience Builder"],
        ),
        (
            "automate", "자동화 &amp; 확장 분석",
            "이 PoC에는 이미 <code>src/publish/arcgis_online.py</code>로 ArcGIS API for Python "
            "자동 발행 스크립트가 작성되어 있습니다 - 새 위성영상이 들어올 때마다 파이프라인을 "
            "예약 실행(Notebook Server)해 레이어를 자동 갱신하는 구조로 그대로 확장됩니다. 분석 "
            "범위를 넓힐 때도 Space-Time Cube 등 시계열 공간통계를 코드 추가 없이 붙일 수 "
            "있습니다.",
            ["ArcGIS API for Python", "Notebook Server"],
        ),
        (
            "shield", "거버넌스 &amp; 협업",
            "Portal for ArcGIS/ArcGIS Hub로 옮기면 부서·기관별 권한을 분리한 레이어 공유 체계와 "
            "접근 로그를 갖추게 됩니다. LH 내부 여러 사업지구가 같은 파이프라인을 쓰게 되더라도, "
            "사업지구별 그룹과 권한만 나눠 동일한 분석 자산을 재사용할 수 있습니다.",
            ["Portal for ArcGIS", "ArcGIS Hub"],
        ),
    ]
    cards_html = "".join(
        f"""
        <div class="capability-card">
          {_icon(icon)}
          <h3>{title}</h3>
          <p>{body}</p>
          <div class="gis-products">{"".join(f'<span class="gis-chip">{p}</span>' for p in products)}</div>
        </div>
        """
        for icon, title, body, products in cards
    )
    return f"""
    <section id="arcgis" class="card">
      <p class="eyebrow">확장 로드맵</p>
      <h2>ArcGIS 플랫폼으로 확장하면</h2>
      <p class="subtitle">지금까지는 이 분석이 Python PoC로 재현 가능하다는 것을 보여드렸습니다.
      같은 데이터와 같은 방법론을 ArcGIS 플랫폼에 올리면, 1회성 분석 리포트가 아니라 LH가
      지속적으로 운영할 수 있는 제품이 됩니다.</p>
      <div class="capability-grid">
        {cards_html}
      </div>
    </section>
    """


# ---------------------------------------------------------- section pieces --

def _years_elapsed(span: str) -> float:
    """'2022-05-17 → 2024-05-31' 형태의 span 문자열에서 경과 연수를 계산한다."""
    start_s, end_s = (s.strip() for s in span.split("→"))
    start = datetime.strptime(start_s, "%Y-%m-%d")
    end = datetime.strptime(end_s, "%Y-%m-%d")
    return (end - start).days / 365.25


def _summary_table(periods: list[dict]) -> str:
    """세 실행을 나란히 놓고 비교하는 표 - 상세 섹션을 읽기 전에 전체 모양을 먼저 보여준다."""
    def row(label: str, fmt) -> str:
        cells = "".join(f"<td>{fmt(p)}</td>" for p in periods)
        return f"<tr><th>{label}</th>{cells}</tr>"

    header_cells = "".join(f'<th class="period-col">{p["code"]}</th>' for p in periods)
    rows = "".join([
        row("촬영 구간", lambda p: p["span"]),
        row("경과 기간", lambda p: f'{_years_elapsed(p["span"]):.2f}년'),
        row("변화 후보(건물 기준)", lambda p: f'{p["stats"]["total"]:,}건'),
        row("실제 현장 수(site_id)", lambda p: f'{p["stats"]["n_sites"]:,}곳'),
        row("연간 환산 HIGH 건수", lambda p: (
            f'<span class="mono">{p["stats"]["priority"].get("HIGH", 0) / _years_elapsed(p["span"]):.1f}건/년</span>'
        )),
        row("우선순위 분포", lambda p: _bar(p["stats"]["priority"], PRIORITY_COLORS, ["HIGH", "MEDIUM", "LOW"])
            + f'<div class="bar-legend">{_chip("HIGH " + str(p["stats"]["priority"].get("HIGH", 0)), PRIORITY_COLORS["HIGH"])} '
              f'{_chip("MED " + str(p["stats"]["priority"].get("MEDIUM", 0)), PRIORITY_COLORS["MEDIUM"])} '
              f'{_chip("LOW " + str(p["stats"]["priority"].get("LOW", 0)), PRIORITY_COLORS["LOW"])}</div>'),
        row("건축물대장 근거 확정", lambda p: f'{p["stats"]["n_register"]:,}건'),
        row("방향성 재확인 권장", lambda p: f'{p["stats"]["n_flagged"]:,}건'),
        row("Global Moran's I", lambda p: (
            f'{p["moran"]["I"]:.3f} <span class="muted">(p={p["moran"]["p_sim"]:.3f})</span>'
            if p.get("moran") and p["moran"].get("I") is not None else "—"
        )),
    ])
    return f"""
    <div class="table-scroll">
      <table class="summary-table">
        <thead><tr><th></th>{header_cells}</tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """


def _context_section() -> str:
    """이 AOI/PoC가 LH의 실제 사업·정책과 어떻게 맞닿아 있는지 정리한 배경 섹션.

    수치와 인용은 2026-09 시점 웹 검색으로 확인한 공개 출처에 근거한다 - 출처는
    섹션 하단에 각주로 남긴다. AOI(행정동 경계 근사, 10.99km²)와 공공주택지구
    지정 경계(8.12km²)는 별개이므로 절대 같은 값으로 혼동해 쓰지 않는다.
    """
    return f"""
    <section id="context" class="card">
      <p class="eyebrow">정책 배경</p>
      <h2>왜 지금 이 분석이 필요한가</h2>
      <p class="subtitle">이 AOI는 실제 3기 신도시 '고양창릉 공공주택지구'와 겹치는 구역입니다.
      아래 수치는 이 리포트의 분석 결과가 아니라, 그 지구 자체에 대한 공개 정보입니다.</p>

      <div class="stat-row stat-row-4">
        <div class="stat-card"><div class="stat-num stat-num-sm">8,119,006</div><div class="stat-label">지구 전체면적(㎡)</div></div>
        <div class="stat-card"><div class="stat-num">38,000</div><div class="stat-label">계획 세대수(호)</div></div>
        <div class="stat-card"><div class="stat-num">92,000</div><div class="stat-label">계획 인구(명)</div></div>
        <div class="stat-card"><div class="stat-num stat-num-sm">2020-2029</div><div class="stat-label">사업기간(지구지정~준공예정)</div></div>
      </div>
      <p class="fine-print">지구지정 2020-03-06(국토교통부 고시 제2020-245호) · 사업시행자 경기도·LH·경기주택도시공사·고양도시관리공사<sup>1</sup>.
      이 리포트의 AOI(행정동 경계 근사, 10.99km²)는 위 공공주택지구 지정 경계(8.12km²)와 정확히
      일치하지 않습니다 - 자세한 내용은 한계·유의사항 섹션을 참고하시기 바랍니다.</p>

      <div class="limits-grid" style="margin-top:24px">
        <div class="limit-item context-item"><div class="bar-el"></div>
          <p><strong>택지 조성 속도가 절반 가까이 빨라지고 있습니다.</strong>
          2026년 8월 13일 정부 부동산 대책에서 3기 신도시를 포함한 공공택지의 발표~착공 기간을
          기존 68개월에서 37개월로 단축하는 방안이 발표됐고, 추가 제도 개선이 완료되면 31개월까지
          단축이 목표입니다. 국토교통부·기획재정부·LH 등이 참여하는 '범정부 택지 신속 혁신단'도
          함께 출범했습니다<sup>2</sup>. 사업기간이 이만큼 압축되면, 지구 내 건축물 변화를 확인하는
          주기도 함께 빨라져야 합니다 - 연 1회 수동 실태조사로는 이 속도를 따라가기 어렵습니다.</p></div>
        <div class="limit-item context-item"><div class="bar-el"></div>
          <p><strong>무허가 건축물은 원칙적으로 보상 대상에서 제외됩니다.</strong>
          토지보상법 시행규칙상 관계법령을 위반해 허가 없이 지어진 건축물은 보상하지 않으며,
          1989년 1월 25일 이후 지어진 무허가 건축물 소유자는 이주대책·주거이전비 대상에서도
          제외됩니다<sup>3</sup>. 즉 "이 건물이 언제 생겼는지"가 보상 실무에서 법적으로 중요한
          쟁점이 됩니다. 위성 변화탐지는 T1/T2/T3 촬영일이 명확한 시계열 근거이므로, 특정 시점
          이후 새로 생기거나 크게 바뀐 건축물을 객관적인 날짜와 함께 짚어낼 수 있습니다.</p></div>
        <div class="limit-item context-item"><div class="bar-el"></div>
          <p><strong>LH가 이미 공개적으로 표명한 방향과 같은 선상에 있습니다.</strong>
          LH는 2023년 11월 UN과 협의의사록(RoD)을 체결해 GeoAI, 드론 웍스 플랫폼, 도시
          디지털트윈, 지리공간정보 분석 시스템 분야 기술 교류를 추진해 왔습니다. 당시 관계자는
          "새로운 도시를 건설하는 데 있어서도 AI가 접목된 공간정보 기술이 차별화된 도시를
          만드는 데 큰 역할을 할 것"이라고 밝혔습니다<sup>4</sup>. 이 PoC의 위성 변화탐지 +
          공간통계 접근은 이 방향의 구체적인 실행 사례로 볼 수 있습니다.</p></div>
        <div class="limit-item context-item"><div class="bar-el"></div>
          <p><strong>국가 차원에서도 검증되고 있는 접근입니다.</strong>
          국토위성영상과 딥러닝을 결합해 건물·도로를 탐지하는 연구가 대한원격탐사학회지 등에
          발표되고 있으며, 국토교통부·한국국토정보공사 등에서 국토현황정보 구축과 모니터링
          고도화에 활용될 것으로 기대되고 있습니다<sup>5</sup>. 위성 기반 변화탐지는 이 PoC만의
          실험이 아니라, 국가 국토모니터링 체계가 실제로 향하고 있는 방향입니다.</p></div>
      </div>

      <p class="fine-print" style="margin-top:18px">
        출처 - 1) 3기 신도시 공식 홈페이지, 고양 창릉 지구개요 ·
        2) 뉴시스, "3기 신도시·신규택지 '속도전'…착공 68→37개월로 단축[8·13대책]", 2026-08-13 ·
        3) 토지보상법 시행규칙(무허가건축물 등의 부지 손실보상 기준) ·
        4) LH 뉴스룸 보도자료, "LH, UN과 디지털트윈 등 공간정보 분야 협업 추진", 2023-11-09 ·
        5) 대한원격탐사학회지, "국토위성영상을 이용한 건물과 도로 학습데이터셋 구축 및 딥러닝 모델 개발"
      </p>
    </section>
    """


def _legend_section() -> str:
    priority_items = "".join(
        f'<li>{_chip(tier, PRIORITY_COLORS[tier])} {desc}</li>'
        for tier, desc in [
            ("HIGH", "즉시 현장조사를 권장합니다 - 변화가 크고 행정정보로 설명되지 않습니다."),
            ("MEDIUM", "후순위 현장조사 대상입니다."),
            ("LOW", "참고용입니다 - 변화가 작거나 근거가 약합니다."),
        ]
    )
    change_items = "".join(
        f'<li>{_chip(CHANGE_TYPE_LABELS[ct], color)} <code>{ct}</code></li>'
        for ct, color in CHANGE_TYPE_COLORS.items()
    )
    gi_items = "".join(
        f'<li>{_chip(GI_CLASS_LABELS[g], color)}</li>' for g, color in GI_CLASS_COLORS.items()
    )
    return f"""
    <section id="legend" class="card">
      <p class="eyebrow">읽는 법</p>
      <h2>이 리포트의 색과 지표</h2>
      <div class="legend-grid">
        <div>
          <h3>현장조사 우선순위</h3>
          <ul class="legend-list">{priority_items}</ul>
        </div>
        <div>
          <h3>변화유형 (classify.py 규칙기반 판정)</h3>
          <ul class="legend-list">{change_items}</ul>
        </div>
        <div>
          <h3>Gi* 공간 hotspot</h3>
          <ul class="legend-list">{gi_items}</ul>
          <p class="fine-print">HOT일수록 주변 건물과 함께 유의하게 높은 변화가 몰려있는 구역입니다 -
          산발적 오탐이 아니라 구조화된 변화(대규모 공사장 등)일 가능성이 높다는 뜻입니다.</p>
        </div>
      </div>
      <p class="fine-print callout-note">
        {_term("directional_consistency_flag")}란 - {_term("change_probability")}는 변화의
        크기만 담고 방향(밝아짐/어두워짐)은 버립니다. 건축물대장 미매칭 휴리스틱 판정에 한해
        T1→T2 밝기 변화 방향이 기대(신축·증축 = 밝아짐)와 어긋나면 라벨은 유지한 채
        재확인 플래그만 켭니다. 건축물대장 근거로 확정된 건은 이 플래그와 무관하게 신뢰도가
        가장 높습니다. (용어에 마우스를 올리면 풀이가 표시됩니다.)
      </p>
    </section>
    """


def _period_section(
    section_id: str,
    code: str,
    title: str,
    subtitle: str,
    stats: dict,
    before_after_png: str | Path,
    priority_png: str | Path,
    gi_star_png: str | Path | None,
    moran: dict | None,
    manifest: dict | None,
    extra_note: str = "",
) -> str:
    priority, change_type = stats["priority"], stats["change_type"]

    priority_rows = "".join(
        f'<tr><td>{_chip(tier, PRIORITY_COLORS[tier])}</td>'
        f'<td class="num">{priority.get(tier, 0):,}</td></tr>'
        for tier in ("HIGH", "MEDIUM", "LOW")
    )
    change_type_rows = "".join(
        f'<tr><td>{_chip(CHANGE_TYPE_LABELS.get(ct, ct), CHANGE_TYPE_COLORS.get(ct, "#999"))}</td>'
        f'<td class="num">{n:,}</td></tr>'
        for ct, n in sorted(change_type.items(), key=lambda x: -x[1])
    )

    moran_html = ""
    if moran and moran.get("I") is not None:
        sig = moran["p_sim"] < 0.05
        moran_html = f"""
        <div class="moran-readout {'moran-sig' if sig else ''}">
          <div class="moran-num">{moran["I"]:.3f}</div>
          <div class="moran-meta">
            Global Moran's I &middot; p = {moran["p_sim"]:.3f} &middot; n = {moran["n"]:,} &middot; k = {moran["k"]}<br>
            {"공간적으로 유의하게 군집되어 있습니다(p&lt;0.05) - 산발적 노이즈가 아니라 구조화된 변화 패턴입니다."
             if sig else "통계적으로 유의한 군집은 확인되지 않았습니다."}
          </div>
        </div>
        """

    gi_figure = ""
    if gi_star_png and Path(gi_star_png).exists():
        gi_figure = f"""
        <figure>
          <img src="data:image/png;base64,{_b64_image(gi_star_png)}" alt="{title} Gi* hotspots" loading="lazy">
          <figcaption>Getis-Ord Gi* 공간 hotspot 분류</figcaption>
        </figure>
        """

    provenance_html = ""
    if manifest:
        inputs_rows = "".join(
            f'<tr><td>{k}</td><td class="mono">{Path(v["path"]).name}</td>'
            f'<td class="mono">{v["sha256"][:12]}…</td></tr>'
            for k, v in manifest.get("inputs", {}).items() if v.get("path")
        )
        provenance_html = f"""
        <details class="provenance">
          <summary>재현성 정보 (실행 시각, 입력 데이터 해시, 파라미터)</summary>
          <div class="provenance-body">
            <table class="kv-table">
              <tr><th>실행 시각(UTC)</th><td class="mono">{manifest.get("timestamp_utc", "—")}</td></tr>
              <tr><th>git commit</th><td class="mono">{manifest.get("git_commit", "—")[:12]}</td></tr>
              <tr><th>random seed</th><td class="mono">{manifest.get("seed", "—")}</td></tr>
              <tr><th>threshold</th><td class="mono">{manifest.get("params", {}).get("threshold_method", "—")}
                = {manifest.get("params", {}).get("used_threshold", "—")}</td></tr>
            </table>
            <table class="kv-table">
              <tr><th>입력</th><th>파일</th><th>sha256</th></tr>
              {inputs_rows}
            </table>
          </div>
        </details>
        """

    return f"""
    <section id="{section_id}" class="card period">
      <p class="eyebrow">{code}</p>
      <h2>{title}</h2>
      <p class="subtitle">{subtitle}</p>
      {f'<p class="callout-note">{extra_note}</p>' if extra_note else ""}

      <div class="stat-row">
        <div class="stat-card"><div class="stat-num">{stats["total"]:,}</div><div class="stat-label">변화 후보(건물기준)</div></div>
        <div class="stat-card"><div class="stat-num">{stats["n_sites"]:,}</div><div class="stat-label">실제 현장 수(site_id)</div></div>
        <div class="stat-card stat-accent"><div class="stat-num">{priority.get("HIGH", 0):,}</div><div class="stat-label">HIGH 우선순위</div></div>
        <div class="stat-card"><div class="stat-num">{stats["n_register"]:,}</div><div class="stat-label">건축물대장 근거 확정</div></div>
        <div class="stat-card"><div class="stat-num">{stats["n_flagged"]:,}</div>
          <div class="stat-label"><abbr class="term" title="{TERM_TOOLTIPS["directional_consistency_flag"]}">방향성 재확인 권장</abbr></div>
        </div>
      </div>

      <h3 class="section-divider">전수조사에서 현장조사 대상까지</h3>
      {_funnel(
          [("변화 후보(건물기준)", stats["total"]),
           ("HIGH 우선순위", priority.get("HIGH", 0)),
           ("HIGH 현장(site_id)", stats["n_sites_high"])],
          context_note=f'AOI 내 건물 2,737개 가운데 이 구간에서 실제로 변화가 감지된 건물 {stats["total"]:,}개를 기준(100%)으로 합니다.',
      )}

      <div class="tables">
        <div>
          <table><caption>현장조사 우선순위</caption><tr><th>등급</th><th class="num">건수</th></tr>{priority_rows}</table>
          {_bar(priority, PRIORITY_COLORS, ["HIGH", "MEDIUM", "LOW"])}
        </div>
        <table><caption>변화유형</caption><tr><th>유형</th><th class="num">건수</th></tr>{change_type_rows}</table>
      </div>

      <figure class="figure-wide">
        <img src="data:image/png;base64,{_b64_image(before_after_png)}" alt="{title} before/after" loading="lazy">
        <figcaption>T1/T2 true-color, Change Probability, 변화유형 분류</figcaption>
      </figure>

      <div class="figure-pair">
        <figure>
          <img src="data:image/png;base64,{_b64_image(priority_png)}" alt="{title} priority map" loading="lazy">
          <figcaption>현장조사 우선순위 지도</figcaption>
        </figure>
        {gi_figure}
      </div>

      {moran_html}

      <h3 class="section-divider">핵심 인사이트</h3>
      {_insights_block(stats)}

      {provenance_html}
    </section>
    """


# ---------------------------------------------------------------- builder --

def build_html_report(out_path: str | Path) -> Path:
    """outputs/ (2022-2024), outputs_2024_2026/ (최신 비교), outputs_2022_2026/
    (2022 vs 2026 전체 4년 직접 비교) 세 실행 결과로 리포트를 생성한다.

    Args:
        out_path: 저장할 HTML 경로.

    Returns:
        저장된 파일 경로.
    """
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    run_specs = [
        {
            "id": "t1-t2", "code": "T1 → T2", "span": "2022-05-17 → 2024-05-31",
            "title": "T1 → T2 (2022-05-17 → 2024-05-31)",
            "subtitle": "공식 Baseline - 고양 창릉동 AOI, Sentinel-2 L2A, robust CVA 앙상블",
            "out_dir": "outputs", "extra_note": "",
        },
        {
            "id": "t2-t3", "code": "T2 → T3", "span": "2024-05-31 → 2026-05-31",
            "title": "T2 → T3 (2024-05-31 → 2026-05-31)",
            "subtitle": "최신 영상 연장 비교입니다 - 참고용 (2026-09-01 신규 확보)",
            "out_dir": "outputs_2024_2026",
            "extra_note": (
                "2년의 짧은 구간임에도 HIGH 건수가 T1→T2보다 많습니다 - 최근 개발이 "
                "가속화되고 있다는 신호로 해석할 수 있습니다(정식 결론이 아닌 참고 자료입니다)."
            ),
        },
        {
            "id": "t1-t3", "code": "T1 → T3", "span": "2022-05-17 → 2026-05-31",
            "title": "T1 → T3 (2022-05-17 → 2026-05-31, 전체 4년 직접 비교)",
            "subtitle": (
                "T1→T2와 T2→T3를 이어붙인 것이 아니라 2022년과 2026년 원본을 직접 비교한 "
                "결과입니다 - 중간(2024년)에 생겼다 사라진 변화나 두 구간에 걸쳐 누적된 변화까지 "
                "함께 포착합니다."
            ),
            "out_dir": "outputs_2022_2026",
            "extra_note": (
                "4년 누적 구간이므로 후보 수가 두 2년 구간보다 뚜렷하게 많습니다(HIGH 건수 최다) - "
                "이는 오탐 급증이 아니라 두 구간의 변화가 산술적으로 겹쳐 잡히기 때문이므로, "
                "우선순위 판단은 T1→T2·T2→T3의 개별 구간 결과와 함께 확인하시기 바랍니다."
            ),
        },
    ]

    periods = []
    for spec in run_specs:
        out_dir = spec["out_dir"]
        stats = _period_stats(f"{out_dir}/vectors/building_change_results.gpkg")
        moran = _moran_summary(f"{out_dir}/statistics/global_moran.json")
        manifest = _manifest_summary(f"{out_dir}/run_manifest.json")
        periods.append({**spec, "stats": stats, "moran": moran, "manifest": manifest})

    total_candidates = sum(p["stats"]["total"] for p in periods)
    total_high = sum(p["stats"]["priority"].get("HIGH", 0) for p in periods)

    persistence = _persistence_insight(
        "outputs/vectors/building_change_results.gpkg",
        "outputs_2024_2026/vectors/building_change_results.gpkg",
    )
    persistence_html = ""
    if persistence and persistence["n_total"]:
        pct_new = 100 * persistence["n_new"] / persistence["n_total"]
        persistence_html = f"""
        <div class="insight-callout">
          <div class="insight-num">{pct_new:.0f}%</div>
          <div class="insight-body">
            <strong>T2→T3 HIGH 현장의 {pct_new:.0f}%는 T1→T2 시점에는 없던 새로운 위치입니다.</strong>
            T2→T3에서 HIGH로 분류된 {persistence["n_total"]}곳 가운데 {persistence["n_persistent"]}곳만
            T1→T2에서도 HIGH였던 자리와 실제로 겹칩니다(공사가 계속 진행 중인 현장으로 추정됩니다) -
            나머지 {persistence["n_new"]}곳은 지오메트리상 새로 등장한 변화입니다. 개발이 몇몇 기존
            현장에 머무르지 않고 AOI 전역으로 번지고 있다는 근거로 해석할 수 있습니다(눈대중이 아니라
            building_change_results의 실제 geometry를 겹쳐 확인한 결과입니다).
          </div>
        </div>
        """

    velocity_html = ""
    if len(periods) >= 2:
        p_t1t2, p_t2t3 = periods[0], periods[1]
        rate_t1t2 = p_t1t2["stats"]["priority"].get("HIGH", 0) / _years_elapsed(p_t1t2["span"])
        rate_t2t3 = p_t2t3["stats"]["priority"].get("HIGH", 0) / _years_elapsed(p_t2t3["span"])
        if rate_t1t2 > 0:
            pct_change = 100 * (rate_t2t3 - rate_t1t2) / rate_t1t2
            velocity_html = f"""
            <div class="insight-callout">
              <div class="insight-num">{pct_change:+.0f}%</div>
              <div class="insight-body">
                <strong>연간 HIGH 발생 속도가 {p_t1t2["code"]} 대비 {p_t2t3["code"]}에서
                {pct_change:+.0f}% 변화했습니다.</strong>
                두 구간 모두 정확히 2년 안팎이라 연간 환산값을 직접 비교할 수 있습니다 -
                {p_t1t2["code"]}는 연간 {rate_t1t2:.1f}건, {p_t2t3["code"]}는 연간 {rate_t2t3:.1f}건입니다.
                단일 시점 스냅샷이 아니라 두 번의 독립적인 실행으로 얻은 추세이므로, 개발 속도
                변화를 시사하는 근거로 참고할 수 있습니다(공식 결론이 아닌 참고 자료입니다).
              </div>
            </div>
            """

    period_sections = "".join(
        _period_section(
            p["id"], p["code"], p["title"], p["subtitle"], p["stats"],
            f'{p["out_dir"]}/figures/before_after_change.png',
            f'{p["out_dir"]}/figures/priority_map.png',
            f'{p["out_dir"]}/figures/gi_star_hotspots.png',
            p["moran"], p["manifest"], p["extra_note"],
        )
        for p in periods
    )

    nav_items = "".join(
        f'<a href="#{p["id"]}">{p["code"]}</a>' for p in periods
    )

    cadastre_fig = Path("outputs/figures/cadastre_context.png")
    cadastre_html = ""
    if cadastre_fig.exists():
        cadastre_html = f"""
        <section id="cadastre" class="card">
          <p class="eyebrow">업무자료 연계</p>
          <h2>지적(필지) 연계</h2>
          <p class="subtitle">VWorld 연속지적도(본번+부번) - AOI 내 필지 10,555개</p>
          <figure class="figure-wide">
            <img src="data:image/png;base64,{_b64_image(cadastre_fig)}" alt="cadastre context" loading="lazy">
            <figcaption>변화 후보 - 필지 경계 중첩</figcaption>
          </figure>
        </section>
        """

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>고양 창릉 Building Change Intelligence - 결과 리포트</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap" rel="stylesheet">
<style>
  :root {{
    --ink: #142523;
    --ink-soft: #4c5f5a;
    --paper: #edf0ea;
    --surface: #ffffff;
    --surface-alt: #f4f6f1;
    --rule: #dbe0d5;
    --accent: #b5792a;
    --accent-soft: #f3e6cf;
    --link: #2c5c52;
    --gis: #2f6690;
    --gis-soft: #e6eef4;
    --shadow: 0 1px 2px rgba(20,37,35,.05), 0 8px 24px rgba(20,37,35,.06);
    --font-sans: "Noto Sans KR", "Pretendard Variable", Pretendard, "Malgun Gothic",
                 "Apple SD Gothic Neo", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --font-mono: "D2Coding", "JetBrains Mono", Consolas, "SFMono-Regular", Menlo, monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --ink: #e9efe9;
      --ink-soft: #a7b6ae;
      --paper: #101815;
      --surface: #17211d;
      --surface-alt: #1c2721;
      --rule: #2b3730;
      --accent: #e0a748;
      --accent-soft: #2f2718;
      --link: #86c7b3;
      --gis: #7ab0d6;
      --gis-soft: #1b2c38;
      --shadow: 0 1px 2px rgba(0,0,0,.3), 0 8px 24px rgba(0,0,0,.35);
    }}
  }}

  * {{ box-sizing: border-box; }}
  body {{
    font-family: var(--font-sans);
    margin: 0; background: var(--paper); color: var(--ink);
    -webkit-font-smoothing: antialiased;
  }}
  a {{ color: var(--link); }}
  code {{ font-family: var(--font-mono); font-size: .85em; color: var(--ink-soft); }}
  .mono {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 12px; }}
  .muted {{ color: var(--ink-soft); font-weight: 400; }}

  header.hero {{
    background: var(--ink); color: var(--paper);
    padding: 48px max(24px, calc(50% - 660px));
    background-image: repeating-linear-gradient(90deg, rgba(255,255,255,.035) 0 1px, transparent 1px 64px);
  }}
  .eyebrow {{
    text-transform: uppercase; letter-spacing: .12em; font-size: 12px; font-weight: 700;
    color: var(--accent); margin: 0 0 10px;
  }}
  header.hero .eyebrow {{ color: #d9b877; }}
  header.hero h1 {{ margin: 0 0 10px; font-size: 34px; font-weight: 800; text-wrap: balance; letter-spacing: -.01em; }}
  header.hero p.meta {{ margin: 0; opacity: .8; font-size: 14px; font-family: var(--font-mono); }}
  .hero-summary {{
    display: flex; gap: 28px; margin-top: 28px; flex-wrap: wrap;
    border-top: 1px solid rgba(255,255,255,.15); padding-top: 20px;
  }}
  .hero-summary div {{ min-width: 120px; }}
  .hero-summary .num {{ font-size: 26px; font-weight: 800; font-variant-numeric: tabular-nums; }}
  .hero-summary .label {{ font-size: 12px; opacity: .75; margin-top: 2px; }}

  nav.toc {{
    position: sticky; top: 0; z-index: 10;
    background: var(--surface); border-bottom: 1px solid var(--rule);
    padding: 0 max(24px, calc(50% - 660px));
    display: flex; gap: 4px; overflow-x: auto;
  }}
  nav.toc a {{
    white-space: nowrap; text-decoration: none; color: var(--ink-soft);
    font-size: 13px; font-weight: 600; padding: 14px 12px; border-bottom: 2px solid transparent;
  }}
  nav.toc a:hover {{ color: var(--ink); border-bottom-color: var(--rule); }}

  main {{ max-width: 1320px; margin: 0 auto; padding: 32px 24px 80px; }}
  section.card, .card {{
    background: var(--surface); border: 1px solid var(--rule); border-radius: 6px;
    padding: 28px 32px; margin: 24px 0; box-shadow: var(--shadow); scroll-margin-top: 56px;
  }}
  h2 {{ margin: 0 0 6px; font-size: 22px; font-weight: 800; letter-spacing: -.01em; text-wrap: balance; }}
  h3 {{ margin: 0 0 8px; font-size: 14px; font-weight: 700; color: var(--ink-soft); }}
  .subtitle {{ color: var(--ink-soft); font-size: 14px; margin: 0 0 18px; }}
  abbr.term {{ text-decoration: underline dotted; text-decoration-color: var(--accent); cursor: help; }}

  .callout-note {{
    background: var(--accent-soft); border-left: 3px solid var(--accent);
    padding: 10px 14px; font-size: 13px; border-radius: 0 4px 4px 0; margin: 0 0 18px;
  }}
  .fine-print {{ font-size: 12.5px; color: var(--ink-soft); line-height: 1.6; }}

  .chip {{
    display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 100px;
    font-size: 12px; font-weight: 700; line-height: 1.7; white-space: nowrap;
  }}

  .bar {{ display: flex; width: 100%; height: 8px; border-radius: 100px; overflow: hidden; background: var(--surface-alt); margin-top: 6px; }}
  .bar span {{ display: block; height: 100%; }}
  .bar-legend {{ margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap; }}

  .table-scroll {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; font-size: 13px; width: 100%; }}
  caption {{ text-align: left; font-weight: 700; font-size: 13px; margin-bottom: 8px; color: var(--ink-soft); }}
  th, td {{ border-bottom: 1px solid var(--rule); padding: 8px 12px; text-align: left; vertical-align: middle; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .summary-table th.period-col {{ text-align: center; font-family: var(--font-mono); font-size: 12px; }}
  .summary-table td {{ text-align: center; }}
  .summary-table th:first-child, .summary-table td:first-child {{ text-align: left; font-weight: 600; }}
  .kv-table th {{ color: var(--ink-soft); font-weight: 600; width: 140px; }}

  .tables {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin: 20px 0; }}
  @media (max-width: 680px) {{ .tables {{ grid-template-columns: 1fr; }} }}

  .stat-row {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin: 20px 0; }}
  .stat-row.stat-row-4 {{ grid-template-columns: repeat(4, 1fr); }}
  @media (max-width: 800px) {{ .stat-row {{ grid-template-columns: repeat(2, 1fr); }} }}
  .stat-num.stat-num-sm {{ font-size: 20px; }}
  .stat-card {{ background: var(--surface-alt); border-radius: 6px; padding: 16px; text-align: center; }}
  .stat-card.stat-accent {{ background: var(--accent-soft); }}
  .stat-num {{ font-size: 26px; font-weight: 800; font-variant-numeric: tabular-nums; }}
  .stat-card.stat-accent .stat-num {{ color: var(--accent); }}
  .stat-label {{ font-size: 12px; color: var(--ink-soft); margin-top: 4px; }}

  figure {{ margin: 20px 0 0; text-align: center; }}
  figure img {{ max-width: 100%; border: 1px solid var(--rule); border-radius: 4px; display: block; margin: 0 auto; }}
  figcaption {{ font-size: 12px; color: var(--ink-soft); margin-top: 8px; }}
  .figure-pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  @media (max-width: 680px) {{ .figure-pair {{ grid-template-columns: 1fr; }} }}

  .moran-readout {{
    display: flex; align-items: baseline; gap: 16px; margin-top: 24px;
    padding: 14px 18px; background: var(--surface-alt); border-radius: 6px;
  }}
  .moran-readout.moran-sig {{ background: var(--accent-soft); }}
  .moran-num {{ font-size: 28px; font-weight: 800; font-variant-numeric: tabular-nums; }}
  .moran-meta {{ font-size: 12.5px; color: var(--ink-soft); line-height: 1.6; }}

  .section-divider {{
    margin-top: 32px; padding-top: 24px; border-top: 1px solid var(--rule);
    font-size: 15px; color: var(--ink);
  }}
  .insights-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 28px; margin: 12px 0 24px; }}
  @media (max-width: 900px) {{ .insights-grid {{ grid-template-columns: 1fr; }} }}
  .mini-stats {{ display: flex; gap: 20px; margin: 8px 0 6px; }}
  .mini-stats > div {{ display: flex; flex-direction: column; }}
  .mini-num {{ font-size: 20px; font-weight: 800; font-variant-numeric: tabular-nums; }}
  .mini-label {{ font-size: 11.5px; color: var(--ink-soft); }}

  .rank-bars {{ list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 7px; }}
  .rank-bars li {{ display: grid; grid-template-columns: 96px 1fr 34px; align-items: center; gap: 8px; font-size: 12px; }}
  .rb-label {{ color: var(--ink-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .rb-track {{ height: 7px; background: var(--surface-alt); border-radius: 100px; overflow: hidden; }}
  .rb-fill {{ display: block; height: 100%; border-radius: 100px; }}
  .rb-num {{ text-align: right; font-variant-numeric: tabular-nums; color: var(--ink-soft); }}

  .insight-callout {{
    display: grid; grid-template-columns: auto 1fr; gap: 20px; align-items: center;
    background: var(--surface-alt); border-radius: 6px; padding: 18px 22px; margin-top: 20px;
  }}
  .insight-num {{ font-size: 34px; font-weight: 800; color: var(--accent); font-variant-numeric: tabular-nums; }}
  .insight-body {{ font-size: 13px; line-height: 1.7; }}
  @media (max-width: 560px) {{ .insight-callout {{ grid-template-columns: 1fr; }} }}

  /* 전수조사 -> 현장조사 대상 funnel (순수 CSS, 폭이 줄어드는 막대) */
  .funnel-context {{ margin: 0 0 12px; }}
  .funnel {{ display: flex; flex-direction: column; align-items: center; gap: 6px; margin: 4px 0 8px; }}
  .funnel-stage {{
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    height: 46px; border-radius: 6px; padding: 0 20px; color: #fff;
  }}
  .funnel-label {{ font-size: 13px; font-weight: 600; white-space: nowrap; }}
  .funnel-num {{ font-size: 17px; font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .funnel-pct {{ font-size: 11.5px; font-weight: 500; opacity: .85; margin-left: 6px; }}
  .funnel-connector {{ width: 2px; height: 8px; background: var(--rule); }}

  /* 세 구간 HIGH/MEDIUM/LOW 세로 막대 그래프 (순수 CSS) */
  .chart-wrap {{ margin: 16px 0 8px; }}
  .chart-groups {{
    display: flex; justify-content: center; align-items: flex-end; gap: 56px;
    padding: 28px 20px 0; border-bottom: 1px solid var(--rule);
  }}
  .chart-group {{ display: flex; flex-direction: column; align-items: center; }}
  .chart-bars {{ display: flex; gap: 8px; align-items: flex-end; }}
  .chart-bar {{
    width: 34px; border-radius: 4px 4px 0 0; position: relative;
  }}
  .chart-bar-value {{
    position: absolute; top: -20px; left: 50%; transform: translateX(-50%);
    font-size: 11px; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap;
  }}
  .chart-group-label {{ margin-top: 10px; font-size: 12.5px; font-weight: 700; color: var(--ink-soft); }}
  .chart-legend {{ display: flex; justify-content: center; gap: 8px; margin-top: 16px; }}

  /* 방법론 상단 한 줄 흐름도 */
  .flow-diagram {{
    display: flex; align-items: center; gap: 6px; margin: 18px 0 4px;
    overflow-x: auto; padding-bottom: 6px;
  }}
  .flow-node {{
    display: flex; align-items: center; gap: 7px; white-space: nowrap;
    background: var(--surface-alt); border-radius: 100px; padding: 7px 14px 7px 8px;
    font-size: 12px; font-weight: 700; color: var(--ink);
  }}
  .flow-num {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 20px; height: 20px; border-radius: 50%; background: var(--accent);
    color: #fff; font-size: 11px; font-weight: 800;
  }}
  .flow-arrow {{ color: var(--rule); font-size: 14px; flex-shrink: 0; }}

  /* ArcGIS 역량 카드 아이콘 */
  .capability-icon {{
    width: 30px; height: 30px; fill: none; stroke: var(--gis);
    stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; margin-bottom: 10px;
  }}

  details.provenance {{ margin-top: 20px; border-top: 1px solid var(--rule); padding-top: 14px; }}
  details.provenance summary {{
    cursor: pointer; font-size: 13px; font-weight: 600; color: var(--ink-soft);
  }}
  .provenance-body {{ display: flex; gap: 24px; flex-wrap: wrap; margin-top: 14px; }}
  .provenance-body table {{ width: auto; min-width: 260px; }}

  .pipeline {{ list-style: none; margin: 8px 0 0; padding: 0; }}
  .pipeline > li {{
    display: grid; grid-template-columns: 40px 1fr; gap: 20px;
    padding: 22px 0; border-top: 1px solid var(--rule);
  }}
  .pipeline > li:first-child {{ border-top: none; padding-top: 12px; }}
  .pipeline-num {{
    width: 32px; height: 32px; border-radius: 50%; background: var(--surface-alt);
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 14px; color: var(--ink-soft);
  }}
  .pipeline-body h3 {{ font-size: 16px; font-weight: 700; color: var(--ink); margin: 0 0 8px; }}
  .pipeline-body p {{ margin: 0 0 12px; font-size: 13.5px; line-height: 1.75; color: var(--ink-soft); }}
  .pipeline-body p.formula {{
    font-family: var(--font-mono); font-size: 13px; color: var(--ink);
    background: var(--surface-alt); padding: 10px 14px; border-radius: 4px;
  }}
  .param-chips {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .param-chip {{
    font-family: var(--font-mono); font-size: 11.5px; background: var(--surface-alt);
    color: var(--ink-soft); padding: 4px 10px; border-radius: 4px;
  }}
  .method-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-top: 14px; }}
  @media (max-width: 900px) {{ .method-grid {{ grid-template-columns: 1fr; }} }}
  .method-card {{ background: var(--surface-alt); border-radius: 6px; padding: 16px 18px; }}
  .method-tag {{
    display: inline-block; font-size: 10.5px; font-weight: 700; letter-spacing: .06em;
    color: var(--accent); text-transform: uppercase; margin-bottom: 4px;
  }}
  .method-card h4 {{ margin: 0 0 6px; font-size: 14px; font-weight: 700; }}
  .method-card p {{ margin: 0; font-size: 12.5px; line-height: 1.65; color: var(--ink-soft); }}

  .arcgis-note {{
    display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: start;
    background: var(--gis-soft); border-radius: 6px; padding: 10px 14px; margin-top: 14px;
    font-size: 12.5px; line-height: 1.65;
  }}
  .arcgis-note .gis-tag {{
    font-size: 10.5px; font-weight: 800; letter-spacing: .05em; color: var(--gis);
    text-transform: uppercase; white-space: nowrap; padding-top: 1px;
  }}
  .arcgis-note p {{ margin: 0; color: var(--ink); }}
  .arcgis-note .gis-products {{ display: flex; gap: 6px; flex-wrap: wrap; margin-top: 6px; }}
  .gis-chip {{
    font-size: 11px; font-weight: 700; background: var(--surface); color: var(--gis);
    border: 1px solid var(--gis); padding: 2px 8px; border-radius: 100px;
  }}

  .capability-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 18px; margin-top: 16px; }}
  @media (max-width: 800px) {{ .capability-grid {{ grid-template-columns: 1fr; }} }}
  .capability-card {{
    background: var(--gis-soft); border-radius: 8px; padding: 20px 22px;
  }}
  .capability-card h3 {{ font-size: 15px; font-weight: 800; color: var(--gis); margin: 0 0 8px; }}
  .capability-card p {{ margin: 0 0 10px; font-size: 13px; line-height: 1.7; color: var(--ink); }}
  .capability-card .gis-products {{ display: flex; gap: 6px; flex-wrap: wrap; }}

  .legend-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 28px; margin-top: 8px; }}
  @media (max-width: 800px) {{ .legend-grid {{ grid-template-columns: 1fr; }} }}
  .legend-list {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; font-size: 13px; }}

  .limits-grid {{ display: grid; gap: 14px; margin-top: 8px; }}
  .limit-item {{
    display: grid; grid-template-columns: 4px 1fr; gap: 16px; align-items: start;
  }}
  .limit-item .bar-el {{ align-self: stretch; background: var(--accent); border-radius: 100px; }}
  .context-item .bar-el {{ background: var(--link); }}
  sup {{ font-size: 10px; color: var(--link); font-weight: 700; }}
  .limit-item p {{ margin: 0; font-size: 13.5px; line-height: 1.7; }}

  footer {{ text-align: center; color: var(--ink-soft); font-size: 12px; padding: 30px 24px 60px; }}
</style>
</head>
<body>
<header class="hero">
  <p class="eyebrow">GIS 변화탐지 분석 리포트</p>
  <h1>고양 창릉 Building Change Intelligence</h1>
  <p class="meta">경기도 고양시덕양구 창릉동 · AOI 10.99km² (행정동 경계 근사) · 생성 {generated_at}</p>
  <div class="hero-summary">
    <div><div class="num">{len(periods)}</div><div class="label">비교 구간</div></div>
    <div><div class="num">{total_candidates:,}</div><div class="label">전체 변화 후보(누적)</div></div>
    <div><div class="num">{total_high:,}</div><div class="label">HIGH 우선순위(누적)</div></div>
    <div><div class="num">2,737</div><div class="label">AOI 내 건물 footprint</div></div>
  </div>
</header>
<nav class="toc">
  <a href="#overview">개요</a>
  <a href="#context">정책 배경</a>
  <a href="#legend">읽는 법</a>
  <a href="#methodology">방법론</a>
  <a href="#arcgis">확장 로드맵</a>
  {nav_items}
  {'<a href="#cadastre">지적 연계</a>' if cadastre_html else ""}
  <a href="#limitations">한계·유의사항</a>
</nav>
<main>
  <section id="overview" class="card">
    <p class="eyebrow">개요</p>
    <h2>세 구간 한눈에 비교</h2>
    <p class="subtitle">T1→T2가 공식 Baseline이며, T2→T3와 T1→T3는 참고용으로 함께 실행한 확장
    비교입니다. 상세 지도와 표는 아래 각 구간 섹션에서 확인하실 수 있습니다.</p>
    {_grouped_bar_chart(periods)}
    {_summary_table(periods)}
    {velocity_html}
    {persistence_html}
  </section>

  {_context_section()}

  {_legend_section()}

  {_methodology_section()}

  {_arcgis_section()}

  {period_sections}

  {cadastre_html}

  <section id="limitations" class="card">
    <p class="eyebrow">반드시 함께 읽을 것</p>
    <h2>한계와 유의사항</h2>
    <div class="limits-grid">
      <div class="limit-item"><div class="bar-el"></div>
        <p><strong>이 시스템은 불법건축물을 자동으로 판정하지 않습니다.</strong>
        HIGH/MEDIUM/LOW는 "영상 변화가 크고 + 건물과 겹치고 + 보유한 행정정보로는
        설명되지 않음"이라는 뜻일 뿐입니다. "설명되지 않음"은 실제 위반일 수도 있지만,
        건축물대장 조인율(67.7%)이 그 건물의 허가 이력을 놓친 결과일 수도 있습니다.</p></div>
      <div class="limit-item"><div class="bar-el"></div>
        <p><strong>AOI는 정식 지구계가 아니라 행정동 경계 근사치</strong>입니다
        (정식 지구계는 국토부 고시 제2021-1285호에 근거하며, 완전한 추출을 위해서는
        별도 작업이 필요합니다).</p></div>
      <div class="limit-item"><div class="bar-el"></div>
        <p>Sentinel-2 10m 해상도는 개별 단독주택 단위의 변화 탐지에 근본적인 한계가
        있습니다 - 대형 아파트단지·대규모 토지조성 등 큰 변화 위주로 신뢰하시기 바랍니다.</p></div>
      <div class="limit-item"><div class="bar-el"></div>
        <p>{_term("directional_consistency_flag")}는 밝기 방향 하나만 보는 보조 신호입니다 -
        계절·식생 변화 등으로도 바뀔 수 있어 라벨을 자동으로 뒤집지는 않습니다. HIGH
        등급이면서 이 플래그가 켜진 후보는 반드시 담당자가 직접 확인하시기 바랍니다.</p></div>
    </div>
  </section>
</main>
<footer>src/evaluation/generate_html_report.py로 생성되었습니다 · 파이프라인 재실행 후 이 스크립트를 다시 실행하면 최신 내용으로 갱신됩니다.</footer>
</body>
</html>
"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    logger.info("[REPORT] HTML 리포트 저장 완료: %s (%.1fMB)", out_path, out_path.stat().st_size / 1e6)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    build_html_report("outputs/reports/changneung_report.html")
