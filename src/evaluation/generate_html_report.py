"""파이프라인 결과를 자체완결형(self-contained) HTML 리포트로 묶는다.

기존 outputs/reports/changneung_report.html은 재사용 스크립트 없이
일회성으로 만들어져 있었다 - 파이프라인을 다시 돌릴 때마다(오늘의
robust_cva 도입, T3 추가 등) 숫자와 그림이 그대로 박제된 채 낡아갔다.
이 스크립트로 대체해 언제든 최신 결과로 재생성할 수 있게 한다.

이미지는 base64로 인라인 임베드한다 - 파일 하나만 공유해도(이메일 첨부,
USB 등) 깨지지 않고 열리게 하기 위함 (기존 리포트와 동일한 설계 의도).
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd

logger = logging.getLogger("generate_html_report")


def _b64_image(path: str | Path) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _stats_table(results_path: str | Path) -> tuple[dict, dict, int]:
    gdf = gpd.read_file(results_path)
    priority = gdf["inspection_priority"].value_counts().to_dict()
    change_type = gdf["change_type"].value_counts(dropna=True).to_dict()
    n_sites = gdf["site_id"].nunique()
    return priority, change_type, n_sites


def _period_section(
    title: str,
    subtitle: str,
    results_path: str | Path,
    before_after_png: str | Path,
    priority_png: str | Path,
    extra_note: str = "",
) -> str:
    priority, change_type, n_sites = _stats_table(results_path)
    total = sum(priority.values())

    priority_rows = "".join(
        f"<tr><td>{tier}</td><td>{priority.get(tier, 0)}</td></tr>"
        for tier in ("HIGH", "MEDIUM", "LOW")
    )
    change_type_rows = "".join(
        f"<tr><td>{ct}</td><td>{n}</td></tr>" for ct, n in sorted(change_type.items(), key=lambda x: -x[1])
    )

    return f"""
    <section class="period">
      <h2>{title}</h2>
      <p class="subtitle">{subtitle}</p>
      {f'<p class="note">{extra_note}</p>' if extra_note else ""}
      <div class="stat-row">
        <div class="stat-card"><div class="stat-num">{total}</div><div class="stat-label">변화 후보(건물기준)</div></div>
        <div class="stat-card"><div class="stat-num">{n_sites}</div><div class="stat-label">실제 현장 수(site_id)</div></div>
        <div class="stat-card"><div class="stat-num">{priority.get("HIGH", 0)}</div><div class="stat-label">HIGH 우선순위</div></div>
      </div>
      <div class="tables">
        <table><caption>현장조사 우선순위</caption><tr><th>등급</th><th>건수</th></tr>{priority_rows}</table>
        <table><caption>변화유형</caption><tr><th>유형</th><th>건수</th></tr>{change_type_rows}</table>
      </div>
      <figure>
        <img src="data:image/png;base64,{_b64_image(before_after_png)}" alt="{title} before/after">
        <figcaption>T1/T2 true-color, Change Probability, 변화유형 분류</figcaption>
      </figure>
      <figure>
        <img src="data:image/png;base64,{_b64_image(priority_png)}" alt="{title} priority map">
        <figcaption>현장조사 우선순위 지도</figcaption>
      </figure>
    </section>
    """


def build_html_report(out_path: str | Path) -> Path:
    """현재 outputs/ (2022-2024)와 outputs_2024_2026/ (최신 비교) 결과로 리포트를 생성한다.

    Args:
        out_path: 저장할 HTML 경로.

    Returns:
        저장된 파일 경로.
    """
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    baseline_section = _period_section(
        "T1→T2 (2022-05-17 → 2024-05-31)",
        "공식 Baseline - 고양 창릉동 AOI, Sentinel-2 L2A, robust CVA 앙상블",
        "outputs/vectors/building_change_results.gpkg",
        "outputs/figures/before_after_change.png",
        "outputs/figures/priority_map.png",
    )
    recent_section = _period_section(
        "T2→T3 (2024-05-31 → 2026-05-31)",
        "최신 영상 연장 비교 - 참고용 (2026-09-01 신규 확보)",
        "outputs_2024_2026/vectors/building_change_results.gpkg",
        "outputs_2024_2026/figures/before_after_change.png",
        "outputs_2024_2026/figures/priority_map.png",
        extra_note=(
            "2년의 짧은 구간임에도 HIGH 건수가 T1→T2보다 많음 - 최근 개발이 "
            "가속화되고 있다는 신호로 해석 가능(정식 결론 아님, 참고 자료)."
        ),
    )

    cadastre_fig = Path("outputs/figures/cadastre_context.png")
    cadastre_html = ""
    if cadastre_fig.exists():
        cadastre_html = f"""
        <section>
          <h2>업무자료 연계 - 지적(필지)</h2>
          <p class="subtitle">VWorld 연속지적도(본번+부번) - AOI 내 필지 10,555개</p>
          <figure>
            <img src="data:image/png;base64,{_b64_image(cadastre_fig)}" alt="cadastre context">
            <figcaption>변화 후보 - 필지 경계 중첩</figcaption>
          </figure>
        </section>
        """

    html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>고양 창릉 Building Change Intelligence - 결과 리포트</title>
<style>
  body {{ font-family: "Malgun Gothic", "Segoe UI", sans-serif; margin: 0; padding: 0 0 60px; background: #f7f7f5; color: #222; }}
  header {{ background: #0b3d2e; color: #fff; padding: 32px 40px; }}
  header h1 {{ margin: 0 0 6px; font-size: 26px; }}
  header p {{ margin: 0; opacity: .85; font-size: 14px; }}
  main {{ max-width: 980px; margin: 0 auto; padding: 0 24px; }}
  section {{ background: #fff; border-radius: 10px; padding: 28px 32px; margin: 28px 0; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  h2 {{ margin-top: 0; color: #0b3d2e; }}
  .subtitle {{ color: #555; font-size: 14px; margin-top: -8px; }}
  .note {{ background: #fff8e6; border-left: 3px solid #f5a623; padding: 8px 14px; font-size: 13px; }}
  .stat-row {{ display: flex; gap: 16px; margin: 20px 0; }}
  .stat-card {{ flex: 1; background: #f0f4f2; border-radius: 8px; padding: 16px; text-align: center; }}
  .stat-num {{ font-size: 30px; font-weight: 700; color: #0b3d2e; }}
  .stat-label {{ font-size: 13px; color: #555; margin-top: 4px; }}
  .tables {{ display: flex; gap: 24px; margin: 20px 0; flex-wrap: wrap; }}
  table {{ border-collapse: collapse; font-size: 13px; }}
  caption {{ text-align: left; font-weight: 700; margin-bottom: 6px; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 12px; text-align: right; }}
  th:first-child, td:first-child {{ text-align: left; }}
  figure {{ margin: 20px 0; text-align: center; }}
  figure img {{ max-width: 100%; border: 1px solid #ddd; border-radius: 4px; }}
  figcaption {{ font-size: 12px; color: #666; margin-top: 6px; }}
  .disclaimer {{ font-size: 13px; line-height: 1.7; }}
  footer {{ text-align: center; color: #888; font-size: 12px; padding: 30px 0; }}
</style>
</head>
<body>
<header>
  <h1>고양 창릉 Building Change Intelligence PoC</h1>
  <p>경기도 고양시덕양구 창릉동 (행정동 경계 근사, 10.99km²) · 생성: {generated_at}</p>
</header>
<main>
  {baseline_section}
  {cadastre_html}
  {recent_section}
  <section>
    <h2>반드시 함께 읽을 것</h2>
    <div class="disclaimer">
      <p><strong>이 시스템은 불법건축물을 자동 판정하지 않는다.</strong>
      HIGH/MEDIUM/LOW는 "영상 변화가 크고 + 건물과 겹치고 + 보유한 행정정보로는
      설명 안 됨"이라는 뜻일 뿐이다. "설명 안 됨"은 실제 위반일 수도, 건축물대장
      조인(67.7%)이 그 건물의 허가 이력을 놓친 것일 수도 있다.</p>
      <p><strong>AOI는 정식 지구계가 아니라 행정동 경계 근사치</strong>다
      (정식 지구계는 국토부 고시 제2021-1285호, 완전 추출은 별도 작업 필요).</p>
      <p>Sentinel-2 10m 해상도는 개별 단독주택 단위 변화 탐지에 근본적 한계가
      있다 - 대형 아파트단지/대규모 토지조성 등 큰 변화 위주로 신뢰할 수 있다.</p>
    </div>
  </section>
</main>
<footer>src/evaluation/generate_html_report.py 로 생성 - 파이프라인 재실행 후 이 스크립트를 다시 돌리면 최신화된다.</footer>
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
