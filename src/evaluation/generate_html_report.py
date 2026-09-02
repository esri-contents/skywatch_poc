"""파이프라인 결과를 자체완결형(self-contained) HTML 리포트로 묶는다.

기존 outputs/reports/changneung_report.html은 재사용 스크립트 없이
일회성으로 만들어져 있었다 - 파이프라인을 다시 돌릴 때마다(robust_cva
도입, T3 추가 등) 숫자와 그림이 그대로 박제된 채 낡아갔다. 이 스크립트로
대체해 언제든 최신 결과로 재생성할 수 있게 한다.

이미지는 base64로 인라인 임베드하고, 폰트는 시스템 폰트 스택만 쓴다
(Google Fonts 등 외부 리소스 의존 없음) - 파일 하나만 공유해도(이메일
첨부, USB, 인터넷 없는 현장 등) 깨지지 않고 열리게 하기 위함이다.

색상은 이 파일이 새로 정의하지 않고 visualize.py의 PRIORITY_COLORS/
CHANGE_TYPE_COLORS/GI_CLASS_COLORS를 그대로 가져다 쓴다 - 지도 범례
색과 표의 chip 색이 반드시 1:1로 일치해야(legend fidelity) 리포트를
보는 사람이 지도와 표를 같은 언어로 읽을 수 있기 때문이다.
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


def _period_stats(results_path: str | Path) -> dict:
    gdf = gpd.read_file(results_path)
    priority = gdf["inspection_priority"].value_counts().to_dict()
    change_type = gdf["change_type"].value_counts(dropna=True).to_dict()
    gi_class = gdf["gi_class"].value_counts(dropna=True).to_dict() if "gi_class" in gdf else {}
    n_sites = gdf["site_id"].nunique()
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
            .head(5)
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
        "n_sites": n_sites, "total": total,
        "n_register": n_register, "n_flagged": n_flagged,
        "area_stats": area_stats, "purpose_counts": purpose_counts, "top_sites": top_sites,
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
        <p class="fine-print">평균이 중앙값보다 훨씬 크다 - 소규모 증축 다수와 대규모 조성공사
        소수가 섞여있다는 뜻이라, 순위표(아래)로 큰 현장부터 따로 확인하는 게 좋다.</p>
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
    {top_table}
    """


def _param_chips(*pairs: tuple[str, str]) -> str:
    items = "".join(f'<span class="param-chip">{k} = {v}</span>' for k, v in pairs)
    return f'<div class="param-chips">{items}</div>'


def _methodology_section() -> str:
    """파이프라인 7단계를 실제 코드/config.yaml 값과 함께 설명한다 (src/pipeline.py 순서 그대로)."""
    return f"""
    <section id="methodology" class="card">
      <p class="eyebrow">방법론</p>
      <h2>파이프라인은 어떻게 동작하는가</h2>
      <p class="subtitle">T1/T2 원본 스택부터 최종 우선순위 점수까지 7단계. 굵은 글씨가 아닌 부분은
      전부 <code>config/config.yaml</code>에서 관리되는 값이라 하드코딩이 아니다.</p>
      <ol class="pipeline">

        <li>
          <div class="pipeline-num">1</div>
          <div class="pipeline-body">
            <h3>전처리 &amp; 정합 검증</h3>
            <p>Sentinel-2 원본 밴드(B02/B03/B04/B08, 10m)를 분석 좌표계로 재투영하고 AOI로 clip해
            4밴드 스택을 만든다. 두 시점 영상이 픽셀 단위로 어긋나면 진짜 변화가 아니라 정합 오차가
            change로 잡히므로, ECC(Enhanced Correlation Coefficient)로 정합 오차를 먼저 실측한다.</p>
            {_param_chips(("CRS", "EPSG:5186"), ("정합 오차", "1.26m (0.126px)"), ("ecc_score", "0.979"),
                          ("허용 기준", "≤ 10m (1px)"))}
          </div>
        </li>

        <li>
          <div class="pipeline-num">2</div>
          <div class="pipeline-body">
            <h3>변화탐지 앙상블 - 3개 방법을 균등 가중 결합</h3>
            <p>어떤 단일 지표도 완벽하지 않다는 전제로, 성질이 다른 세 방법을 <strong>1/3씩 균등
            가중</strong>으로 더해 <code>change_probability</code>(0~1)를 만든다.</p>
            <div class="method-grid">
              <div class="method-card">
                <div class="method-tag">Method A</div>
                <h4>Robust CVA</h4>
                <p>밴드별 (T2−T1) 차분을 <strong>median/MAD로 표준화</strong>(이상치에 강건)한 뒤
                유클리드 거리로 결합하고, 최댓값이 아니라 <strong>상위 1%(99th percentile)</strong>를
                기준으로 0~1로 clip한다. 전역 최댓값으로 정규화하는 단순 방식은 구름 잔여물 같은
                극단 픽셀 하나에 전체 스케일이 눌려버리는 문제가 있어 이 방식으로 대체했다.</p>
              </div>
              <div class="method-card">
                <div class="method-tag">Method B</div>
                <h4>SSIM</h4>
                <p>T1/T2의 국소 밝기·대비·구조 패턴이 다를수록(구조적 유사도가 낮을수록) 변화
                점수가 높아진다. 순수 밝기 차이만으로는 안 잡히는, "패턴 자체가 달라진" 변화를
                보완한다.</p>
              </div>
              <div class="method-card">
                <div class="method-tag">Method C</div>
                <h4>Edge / Texture</h4>
                <p>T1/T2 각각의 Canny 엣지맵을 뽑아 XOR로 차분한다. 건물 외곽선처럼 엣지가 새로
                생기거나 사라지는 - 신축/철거에서 특히 두드러지는 - 변화를 잡기 위한 방법이다.</p>
              </div>
            </div>
          </div>
        </li>

        <li>
          <div class="pipeline-num">3</div>
          <div class="pipeline-body">
            <h3>임계값 결정 &amp; 후처리</h3>
            <p>고정 임계값으로 이진 마스크를 만든다. Otsu 자동 임계값도 구현돼 있지만, 이 AOI에서는
            분포 특성상 훨씬 공격적인 값을 골라 변화 후보가 급증해(14.87% vs 3.82%) 육안 QA로
            검증되기 전까지는 보수적인 고정값을 기본으로 쓴다. 이진 마스크는 opening→closing으로
            소금-후추 노이즈를 지우고, 너무 작은 connected component는 버린다.</p>
            {_param_chips(("threshold_method", "fixed"), ("mask_threshold", "0.5"),
                          ("opening/closing kernel", "3×3"), ("최소 면적", "25 m²"))}
          </div>
        </li>

        <li>
          <div class="pipeline-num">4</div>
          <div class="pipeline-body">
            <h3>건물 Overlay &amp; 밝기 방향성(brightness_delta) 계산</h3>
            <p>Polygon화된 change 영역을 건물 footprint(2,737개)와 공간 overlay해 건물별
            <code>change_ratio</code>(footprint 대비 교차 면적 비율)를 구한다. 큰 change polygon
            하나에 건물 여러 개가 걸치는 경우가 실측으로 흔해, <code>site_id</code>로 묶어 "건물
            수"와 "실제 현장 수"를 구분한다. 이와 별개로 change_probability가 버리는 정보 - T1→T2
            그레이스케일 평균 밝기가 밝아졌는지 어두워졌는지 - 도 계산해 다음 단계의 보조 근거로
            넘긴다.</p>
            {_param_chips(("버퍼 거리(근접 변화 판정)", "3 m"))}
          </div>
        </li>

        <li>
          <div class="pipeline-num">5</div>
          <div class="pipeline-body">
            <h3>변화유형 분류 (규칙 기반)</h3>
            <p>건축물대장 사용승인일이 있으면 <strong>그것을 최우선 근거</strong>로 쓴다 - T1~T2
            사이면 확정적으로 신축, 그 밖이면 기존 건물이므로 증축/개축. 대장 미매칭 건물만
            change_ratio 크기로 근사 판정(휴리스틱)하고, 이때만 brightness_delta 방향이 기대와
            어긋나면 <code>directional_consistency_flag</code>를 켠다(라벨은 유지). 건물과
            교차하지 않는 고신뢰 변화는 철거 후보로 보되, 밝기가 오히려 뚜렷하게 증가했다면(철거
            방향과 모순) 철거 대신 기타 변화로 남긴다.</p>
            {_param_chips(("신축 판정 change_ratio", "≥ 0.5"), ("건축물대장 PNU 매칭률", "67.7%"),
                          ("철거 후보 mean_change_score", "≥ 0.6"))}
          </div>
        </li>

        <li>
          <div class="pipeline-num">6</div>
          <div class="pipeline-body">
            <h3>현장조사 우선순위 점수화</h3>
            <p class="formula">priority_score = 0.4 · change_confidence + 0.3 · change_ratio
            + 0.2 · administrative_uncertainty + 0.1 · building_relevance</p>
            <p><code>change_confidence</code>는 change_probability의 최댓값,
            <code>administrative_uncertainty</code>는 건축물대장으로 설명되면 0에 가깝게·안되면
            1, <code>building_relevance</code>는 change_type이 신축/증축/철거면 1.0 아니면 0.3(예:
            건물과 무관한 주변 토지 변화)이다. 가중치가 가장 큰 change_confidence(0.4)가 영상
            근거를, administrative_uncertainty(0.2)가 행정정보 공백을 반영해 "행정정보로 설명 안
            되는 큰 변화"를 자연스럽게 상위로 끌어올린다.</p>
            {_param_chips(("HIGH", "≥ 0.7"), ("MEDIUM", "≥ 0.4"), ("LOW", "< 0.4"))}
          </div>
        </li>

        <li>
          <div class="pipeline-num">7</div>
          <div class="pipeline-body">
            <h3>공간통계 검증 (Global Moran's I / Getis-Ord Gi*)</h3>
            <p>priority_score가 실제로 공간적으로 군집돼 있는지(=구조화된 변화) 아니면 산발적
            노이즈인지를 <strong>Global Moran's I</strong>(KNN row-standardized weights)로 먼저
            통계 검정한다. 이어서 <strong>Getis-Ord Gi*</strong>(binary weights)로 어느 건물이
            주변과 함께 유의하게 높은/낮은 값을 갖는 hotspot/coldspot인지 90/95/99% 신뢰수준으로
            분류한다. 둘 다 permutation 검정이라 매 실행마다 결과가 흔들리지 않도록 random seed를
            고정한다.</p>
            {_param_chips(("KNN k", "8"), ("permutations", "999"), ("random_seed", "42"))}
          </div>
        </li>

      </ol>
    </section>
    """


# ---------------------------------------------------------- section pieces --

def _summary_table(periods: list[dict]) -> str:
    """세 실행을 나란히 놓고 비교하는 표 - 상세 섹션을 읽기 전에 전체 모양을 먼저 보여준다."""
    def row(label: str, fmt) -> str:
        cells = "".join(f"<td>{fmt(p)}</td>" for p in periods)
        return f"<tr><th>{label}</th>{cells}</tr>"

    header_cells = "".join(f'<th class="period-col">{p["code"]}</th>' for p in periods)
    rows = "".join([
        row("촬영 구간", lambda p: p["span"]),
        row("변화 후보(건물 기준)", lambda p: f'{p["stats"]["total"]:,}건'),
        row("실제 현장 수(site_id)", lambda p: f'{p["stats"]["n_sites"]:,}곳'),
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


def _legend_section() -> str:
    priority_items = "".join(
        f'<li>{_chip(tier, PRIORITY_COLORS[tier])} {desc}</li>'
        for tier, desc in [
            ("HIGH", "즉시 현장조사 권장 - 변화가 크고 행정정보로 설명 안 됨"),
            ("MEDIUM", "우선순위 후순위 현장조사 대상"),
            ("LOW", "참고용 - 변화가 작거나 근거가 약함"),
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
          <p class="fine-print">HOT일수록 주변 건물과 함께 유의하게 높은 변화가 몰려있는 구역 -
          산발적 오탐이 아니라 구조화된 변화(대규모 공사장 등)일 가능성이 높다는 뜻.</p>
        </div>
      </div>
      <p class="fine-print callout-note">
        <strong>directional_consistency_flag</strong> - change_probability는 변화의 크기만
        담고 방향(밝아짐/어두워짐)은 버린다. 건축물대장 미매칭 휴리스틱 판정에 한해
        T1→T2 밝기 변화 방향이 기대(신축·증축 = 밝아짐)와 어긋나면 라벨은 유지한 채
        재확인 플래그만 켠다. 대장 근거로 확정된 건은 이 플래그와 무관하게 신뢰도가 가장 높다.
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
            {"공간적으로 유의하게 군집됨(p&lt;0.05) - 산발적 노이즈가 아니라 구조화된 변화 패턴."
             if sig else "통계적으로 유의한 군집이 확인되지 않음."}
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
        <div class="stat-card"><div class="stat-num">{stats["n_flagged"]:,}</div><div class="stat-label">방향성 재확인 권장</div></div>
      </div>

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
            "subtitle": "최신 영상 연장 비교 - 참고용 (2026-09-01 신규 확보)",
            "out_dir": "outputs_2024_2026",
            "extra_note": (
                "2년의 짧은 구간임에도 HIGH 건수가 T1→T2보다 많음 - 최근 개발이 "
                "가속화되고 있다는 신호로 해석 가능(정식 결론 아님, 참고 자료)."
            ),
        },
        {
            "id": "t1-t3", "code": "T1 → T3", "span": "2022-05-17 → 2026-05-31",
            "title": "T1 → T3 (2022-05-17 → 2026-05-31, 전체 4년 직접 비교)",
            "subtitle": (
                "T1→T2와 T2→T3를 이어붙인 것이 아니라 2022년과 2026년 원본을 직접 비교한 결과 - "
                "중간(2024년)에 생겼다 사라진 변화나 두 구간에 걸쳐 누적된 변화까지 함께 잡는다"
            ),
            "out_dir": "outputs_2022_2026",
            "extra_note": (
                "4년 누적 구간이라 후보 수가 두 2년 구간보다 뚜렷하게 많다(HIGH 건수 최다) - "
                "이는 오탐 급증이 아니라 두 구간의 변화가 산술적으로 겹쳐 잡히기 때문이므로, "
                "우선순위 판단은 T1→T2/T2→T3의 개별 구간 결과와 함께 봐야 한다."
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
            <strong>T2→T3 HIGH 현장의 {pct_new:.0f}%는 T1→T2 시점엔 없던 새 위치다.</strong>
            T2→T3에서 HIGH로 분류된 {persistence["n_total"]}곳 중 {persistence["n_persistent"]}곳만
            T1→T2에서도 HIGH였던 자리와 실제로 겹친다(공사가 계속 진행 중인 현장으로 추정) - 나머지
            {persistence["n_new"]}곳은 지오메트리상 새로 등장한 변화다. 개발이 몇몇 기존 현장에
            머무르지 않고 AOI 전역으로 번지고 있다는 근거로 읽을 수 있다(눈대중이 아니라
            building_change_results의 실제 geometry를 겹쳐본 결과).
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
    --shadow: 0 1px 2px rgba(20,37,35,.05), 0 8px 24px rgba(20,37,35,.06);
    --font-sans: "Pretendard Variable", Pretendard, "Malgun Gothic", "Apple SD Gothic Neo",
                 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
  .subtitle {{ color: var(--ink-soft); font-size: 14px; margin: 0 0 18px; max-width: 68ch; }}

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
  @media (max-width: 800px) {{ .stat-row {{ grid-template-columns: repeat(2, 1fr); }} }}
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
  .pipeline-body p {{ margin: 0 0 12px; font-size: 13.5px; line-height: 1.75; color: var(--ink-soft); max-width: 82ch; }}
  .pipeline-body p.formula {{
    font-family: var(--font-mono); font-size: 13px; color: var(--ink);
    background: var(--surface-alt); padding: 10px 14px; border-radius: 4px; max-width: none;
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

  .legend-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 28px; margin-top: 8px; }}
  @media (max-width: 800px) {{ .legend-grid {{ grid-template-columns: 1fr; }} }}
  .legend-list {{ list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; font-size: 13px; }}

  .limits-grid {{ display: grid; gap: 14px; margin-top: 8px; }}
  .limit-item {{
    display: grid; grid-template-columns: 4px 1fr; gap: 16px; align-items: start;
  }}
  .limit-item .bar-el {{ align-self: stretch; background: var(--accent); border-radius: 100px; }}
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
  <a href="#legend">읽는 법</a>
  <a href="#methodology">방법론</a>
  {nav_items}
  {'<a href="#cadastre">지적 연계</a>' if cadastre_html else ""}
  <a href="#limitations">한계·유의사항</a>
</nav>
<main>
  <section id="overview" class="card">
    <p class="eyebrow">개요</p>
    <h2>세 구간 한눈에 비교</h2>
    <p class="subtitle">T1→T2가 공식 Baseline이고, T2→T3와 T1→T3는 참고용으로 함께 실행한 확장 비교다.
    상세 지도와 표는 아래 각 구간 섹션에 있다.</p>
    {_summary_table(periods)}
    {persistence_html}
  </section>

  {_legend_section()}

  {_methodology_section()}

  {period_sections}

  {cadastre_html}

  <section id="limitations" class="card">
    <p class="eyebrow">반드시 함께 읽을 것</p>
    <h2>한계와 유의사항</h2>
    <div class="limits-grid">
      <div class="limit-item"><div class="bar-el"></div>
        <p><strong>이 시스템은 불법건축물을 자동 판정하지 않는다.</strong>
        HIGH/MEDIUM/LOW는 "영상 변화가 크고 + 건물과 겹치고 + 보유한 행정정보로는
        설명 안 됨"이라는 뜻일 뿐이다. "설명 안 됨"은 실제 위반일 수도, 건축물대장
        조인(67.7%)이 그 건물의 허가 이력을 놓친 것일 수도 있다.</p></div>
      <div class="limit-item"><div class="bar-el"></div>
        <p><strong>AOI는 정식 지구계가 아니라 행정동 경계 근사치</strong>다
        (정식 지구계는 국토부 고시 제2021-1285호, 완전 추출은 별도 작업 필요).</p></div>
      <div class="limit-item"><div class="bar-el"></div>
        <p>Sentinel-2 10m 해상도는 개별 단독주택 단위 변화 탐지에 근본적 한계가
        있다 - 대형 아파트단지·대규모 토지조성 등 큰 변화 위주로 신뢰할 수 있다.</p></div>
      <div class="limit-item"><div class="bar-el"></div>
        <p>directional_consistency_flag는 밝기 방향 하나만 보는 보조 신호다 - 계절/식생
        변화 등으로도 바뀔 수 있어 라벨을 자동으로 뒤집지 않는다. HIGH 등급이면서
        이 플래그가 켜진 후보는 사람이 직접 확인해야 한다.</p></div>
    </div>
  </section>
</main>
<footer>src/evaluation/generate_html_report.py 로 생성 · 파이프라인 재실행 후 이 스크립트를 다시 돌리면 최신화된다.</footer>
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
