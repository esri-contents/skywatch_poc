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
    return {
        "priority": priority, "change_type": change_type,
        "n_sites": n_sites, "total": total,
        "n_register": n_register, "n_flagged": n_flagged,
    }


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
    padding: 48px max(24px, calc(50% - 500px));
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
    padding: 0 max(24px, calc(50% - 500px));
    display: flex; gap: 4px; overflow-x: auto;
  }}
  nav.toc a {{
    white-space: nowrap; text-decoration: none; color: var(--ink-soft);
    font-size: 13px; font-weight: 600; padding: 14px 12px; border-bottom: 2px solid transparent;
  }}
  nav.toc a:hover {{ color: var(--ink); border-bottom-color: var(--rule); }}

  main {{ max-width: 1000px; margin: 0 auto; padding: 32px 24px 80px; }}
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

  details.provenance {{ margin-top: 20px; border-top: 1px solid var(--rule); padding-top: 14px; }}
  details.provenance summary {{
    cursor: pointer; font-size: 13px; font-weight: 600; color: var(--ink-soft);
  }}
  .provenance-body {{ display: flex; gap: 24px; flex-wrap: wrap; margin-top: 14px; }}
  .provenance-body table {{ width: auto; min-width: 260px; }}

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
  </section>

  {_legend_section()}

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
