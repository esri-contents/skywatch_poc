"""필지 단위 구조물 판독 - 공공기관 보고자료(A4 인쇄용 단독 HTML).

  python -m src.evaluation.generate_interpretation_official --project config/projects/gwangmyeong_siheung_aerial.yaml

generate_interpretation_report(웹 열람용)와 같은 산출물을 읽되, 보고서 양식(표지·요약·목차·Ⅰ~Ⅴ장·붙임)과
개조식 문체, 표·그림 번호를 쓰고 필지 목록 전체를 붙임으로 싣는다. 그림은 data URI로 넣는다.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

from ..data.project_acquisition import load_project
from ..interpretation.qa import LABELS, stratified_precision, summarize
from ..interpretation.deliver import NAME as SHP_NAME, NAME_NOREG, appendix1, appendix1b, write_appendix1_shp
from ..interpretation.survey import summarize as survey_summary
from ..interpretation.tiling import grid
from . import interpretation_figures as F
from .generate_interpretation_report import _ROUNDS, _STATUS, _STRATA, _VERDICT, _dong, _e, _qa_verdicts, _timing
from .generate_project_deliverables import _embedded_image

_FLOW = """<svg viewBox="0 0 860 300" width="100%" role="img" aria-label="판독 처리 흐름도" style="font-family:'Malgun Gothic',sans-serif">
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker></defs>
<g font-size="12.5" text-anchor="middle">
<g fill="#eef2f6" stroke="#1f4e79" stroke-width="1.3">
<rect x="10" y="20" width="150" height="64" rx="4"/><rect x="10" y="118" width="150" height="64" rx="4"/><rect x="10" y="216" width="150" height="64" rx="4"/></g>
<text x="85" y="47" font-weight="700">항공 정사영상</text><text x="85" y="66" font-size="11">@D1@ · @D2@</text>
<text x="85" y="145" font-weight="700">기준 건물 레이어</text><text x="85" y="164" font-size="11">건물통합정보·도로명주소·대장</text>
<text x="85" y="243" font-weight="700">연속지적도</text><text x="85" y="262" font-size="11">@N@필지</text>
<g fill="#fff" stroke="#333" stroke-width="1.3">
<rect x="215" y="20" width="160" height="64" rx="4"/><rect x="215" y="118" width="160" height="64" rx="4"/>
<rect x="430" y="20" width="190" height="64" rx="4"/><rect x="430" y="118" width="190" height="64" rx="4"/><rect x="430" y="216" width="190" height="64" rx="4"/></g>
<text x="295" y="47" font-weight="700">① 조각화</text><text x="295" y="66" font-size="11">@RES@m · 지구 전체 @T@타일</text>
<text x="295" y="145" font-weight="700">② 판독 요소 계산</text><text x="295" y="164" font-size="11">색조·형태·크기·그림자 등 21종</text>
<text x="525" y="47" font-weight="700">③ 학습형 판독기(2단계)</text><text x="525" y="66" font-size="11">조각별 구조물 확률 · 공간 교차검증</text>
<text x="525" y="145" font-weight="700">④ 필지 집계·대조</text><text x="525" y="164" font-size="11">등재/판독 상태 · 미등재 추출</text>
<text x="525" y="243" font-weight="700">⑤ 두 시기 비교</text><text x="525" y="262" font-size="11">신규·소멸 + 변화탐지 교차 확인</text>
<g fill="#fff7e6" stroke="#8a5a00" stroke-width="1.3"><rect x="675" y="69" width="175" height="64" rx="4"/><rect x="675" y="167" width="175" height="64" rx="4"/></g>
<text x="762" y="96" font-weight="700">⑥ 표본 육안 검수</text><text x="762" y="115" font-size="11">층화 무작위 · 정밀도 산정</text>
<text x="762" y="194" font-weight="700">결과물</text><text x="762" y="213" font-size="11">필지 목록 · 근거 영상 · 보고서</text></g>
<g stroke="#333" stroke-width="1.4" fill="none" marker-end="url(#ar)">
<path d="M160 52 H213"/><path d="M295 84 V116"/><path d="M375 150 H400 V52 H428"/><path d="M160 150 H200 V200 H400 V150 H428"/>
<path d="M525 84 V116"/><path d="M525 182 V214"/><path d="M160 248 H428"/><path d="M620 150 H645 V101 H673"/><path d="M620 248 H645 V199 H673"/><path d="M762 133 V165"/></g>
</svg>"""

_CSS = """
/* A4 보고서: 흰 바탕 단일 테마(인쇄물), 본문 명조 계열 대신 가독성 위주 고딕, 표·그림 번호 체계 */
:root{--ink:#111;--muted:#555;--line:#9aa0a6;--line2:#d0d4d8;--head:#e9edf1;--accent:#1f4e79;--red:#a4262c;--amber:#8a5a00;--ok:#1d6b45;
  --st-both:#1f6f78;--st-reg:#8fb8bd;--st-seen:#c8414b;--st-none:#d9dee2;--st-na:#9aa3aa;
  --f:"Malgun Gothic","맑은 고딕","Apple SD Gothic Neo","Noto Sans KR",sans-serif;color-scheme:light}
*{box-sizing:border-box}
html{background:#e6e8ea}
body{margin:0;color:var(--ink);font:10.5pt/1.65 var(--f);background:#e6e8ea}
.page{width:210mm;min-height:297mm;margin:12px auto;background:#fff;padding:22mm 20mm 20mm;box-shadow:0 1px 6px rgba(0,0,0,.18)}
@media screen and (max-width:820px){.page{width:auto;min-height:0;margin:0 0 8px;padding:24px max(16px,env(safe-area-inset-right,0px)) 24px max(16px,env(safe-area-inset-left,0px))}}
@media print{html,body{background:#fff}.page{margin:0;box-shadow:none;width:auto;min-height:0;padding:0;page-break-after:always}
  @page{size:A4;margin:18mm 16mm}.noprint{display:none}}
/* 표지 */
.cover{display:flex;flex-direction:column;justify-content:space-between}
.cover .cls{font-size:10pt;color:var(--muted);border:1px solid var(--line);display:inline-block;padding:2px 10px}
.cover h1{font-size:24pt;line-height:1.35;margin:52mm 0 6mm;letter-spacing:-.02em;text-wrap:balance}
.cover .sub{font-size:13pt;color:#333}
.cover table{width:auto;margin-top:18mm;font-size:10.5pt}
.cover td{border:0;padding:3px 14px 3px 0}
.cover .org{font-size:13pt;font-weight:700;text-align:center;margin-top:30mm}
/* 제목 체계 */
h2{font-size:15pt;margin:0 0 4mm;padding-bottom:2mm;border-bottom:2px solid var(--ink)}
h3{font-size:12pt;margin:7mm 0 2mm}
h4{font-size:10.5pt;margin:5mm 0 1mm}
/* 개조식 */
ul.b{list-style:none;padding:0;margin:1mm 0}
ul.b > li{padding-left:1.3em;text-indent:-1.3em;margin:1.2mm 0}
ul.b > li::before{content:"□ ";font-weight:700}
ul.o{list-style:none;padding:0;margin:.5mm 0 0 1.3em}
ul.o > li{padding-left:1.2em;text-indent:-1.2em;margin:.6mm 0}
ul.o > li::before{content:"○ "}
ul.d{list-style:none;padding:0;margin:.3mm 0 0 1.2em}
ul.d > li{padding-left:1em;text-indent:-1em;margin:.3mm 0;color:#222}
ul.d > li::before{content:"- "}
.note{font-size:9pt;color:var(--muted)}
.box{border:1px solid var(--ink);padding:3.5mm 5mm;margin:3mm 0}
.box.warn{border-color:var(--amber);background:#fffaf0}
.box h4{margin:0 0 1.5mm}
/* 표 */
.cap{font-size:9.5pt;font-weight:700;margin:4mm 0 1mm}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:9pt;border-top:1.5px solid var(--ink);border-bottom:1.5px solid var(--ink)}
th,td{border:1px solid var(--line2);padding:1.4mm 2mm;vertical-align:top;text-align:left}
th{background:var(--head);font-weight:700;text-align:center;white-space:nowrap}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.c{text-align:center;white-space:nowrap}
td.pnu{font-family:Consolas,monospace;font-size:8.5pt;white-space:nowrap}
tr.sum td{font-weight:700;background:#f4f6f8}
.src{font-size:8.5pt;color:var(--muted);margin-top:1mm}
/* 그림 */
figure{margin:3mm 0;text-align:center;break-inside:avoid}
figure img{max-width:100%;border:1px solid var(--line2)}
figcaption{font-size:9.5pt;font-weight:700;margin-top:1.5mm}
.fig1{display:grid;gap:5mm}
.fig1 figure{margin:0}.fig1 figcaption{font-weight:400;font-size:9pt;text-align:left;margin-top:1mm}
.fig1 figcaption b{font-weight:700}
.figpage figure img{max-height:235mm;width:auto}
.fig2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4mm}
.fig2 figure{margin:0}.fig2 figcaption{font-weight:400;font-size:8.5pt;text-align:left}
.legend{font-size:8.5pt;color:var(--muted);text-align:center}
.sw{display:inline-block;width:9px;height:9px;margin:0 3px 0 10px;vertical-align:-1px}
.pill{display:inline-block;padding:0 6px;font-size:8.5pt;font-weight:700;border:1px solid currentColor;white-space:nowrap}
.p-ok{color:var(--ok)}.p-mid{color:var(--amber)}.p-low{color:var(--red)}.p-na{color:var(--muted)}
.toc{list-style:none;padding:0;margin:4mm 0;font-size:11pt}
.toc li{display:flex;gap:8px;padding:1.6mm 0;border-bottom:1px dotted var(--line)}
.toc li span:first-child{min-width:2.4em;font-weight:700}
.kv{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1.5px solid var(--ink);border-bottom:1.5px solid var(--ink);margin:3mm 0}
.kv div{padding:2.5mm 3mm;border-right:1px solid var(--line2)}.kv div:last-child{border-right:0}
.kv .k{font-size:8.5pt;color:var(--muted)}.kv .v{font-size:15pt;font-weight:700}.kv .v small{font-size:9pt;font-weight:400}
@media screen and (max-width:820px){.kv{grid-template-columns:repeat(2,minmax(0,1fr))}.fig2{grid-template-columns:1fr}}
.toolbar{position:sticky;top:0;z-index:5;background:#1f2a33;color:#fff;font-size:12px;padding:8px max(16px,env(safe-area-inset-left,0px));text-align:center}
"""


def _tbl(cap: str, head: list[str], rows: list[list], cls: list[str] | None = None, src: str = "", sum_row: list | None = None) -> str:
    cls = cls or [""] * len(head)
    cap = cap.replace("<", "〈").replace(">", "〉")  # 〈표 1〉 - 꺾쇠를 쓰면 태그로 읽힘
    th ="".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f'<td class="{c}">{v}</td>' for v, c in zip(r, cls)) + "</tr>" for r in rows)
    if sum_row:
        body += '<tr class="sum">' + "".join(f'<td class="{c}">{v}</td>' for v, c in zip(sum_row, cls)) + "</tr>"
    return (f'<div class="cap">{cap}</div><div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'
            + (f'<div class="src">{src}</div>' if src else ""))


def _p(d: dict) -> str:
    return f'{d["precision"] * 100:.1f}%' if d else "-"


def _ci(d: dict) -> str:
    return f'{d["ci_lo"] * 100:.0f}~{d["ci_hi"] * 100:.0f}%' if d else "-"


def _fr(r) -> str:
    return f'{int(r["구조물 맞음"])}/{int(r["구조물 맞음"] + r["구조물 아님"])}' if r is not None else "-"


def _rn(tag: str | None) -> str:
    """회차 짧은 이름: round4 → 4차, 그 밖(review1 등)은 _ROUNDS 이름."""
    if tag is None:
        return "-"
    return f"{tag[5:]}차" if tag.startswith("round") and tag[5:].isdigit() else _ROUNDS.get(tag, tag)


def _latest(rounds: dict[str, pd.DataFrame], cond) -> str | None:
    """라벨이 달린 행 중 cond를 만족하는 행이 있는 마지막 회차(회차 이름순)."""
    hit = [k for k, q in rounds.items() if cond(q[q["label"].isin(list(LABELS))]).any()]
    return hit[-1] if hit else None


def _is_ind(q: pd.DataFrame) -> pd.Series:
    return q["stratum"].str.startswith("unreg_") & ~q["stratum"].isin(["unreg_attached", "unreg_linear"])


def _html(cfg: dict, config_path: str | None = None) -> str:
    ic = cfg["interpretation"]
    out = Path(ic["out_dir"])
    t1, t2 = ic["epochs"]
    ep = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    d1, d2 = str(ep[t1]["date"]), str(ep[t2]["date"])
    s = json.loads((out / "reports" / "interpretation_summary.json").read_text(encoding="utf-8"))
    parcels = gpd.read_file(out / "vectors" / "parcel_interpretation.gpkg")
    objs = gpd.read_file(out / "vectors" / "structure_change_objects.gpkg")
    clf, rc, base, proj = s["classifier"], s["rules"], cfg["compensation"], cfg["project"]
    n = len(parcels)
    st = parcels[f"status_{t1}"].value_counts()
    has = int(sum(st.get(k, 0) for k, _, _ in _STATUS[:3]))
    unreg = parcels[parcels["unregistered_flag"]]
    u_ind, u_att, u_low = (unreg[unreg["unregistered_conf"] == c] for c in ("독립", "부속 의심", "저신뢰"))
    u_nreg = unreg[unreg["unregistered_conf"] == "대장 없음"]
    n_violt = int((parcels["violt_n"] > 0).sum()) if "violt_n" in parcels else 0
    bref = s.get("buildings_ref") or {}
    new_a = objs[(objs["kind"] == "new") & (objs["tier"] == "A")].sort_values("area_m2", ascending=False).reset_index(drop=True)
    gone_ab = objs[(objs["kind"] == "gone") & objs["tier"].isin(["A", "B"])].sort_values("area_m2", ascending=False).reset_index(drop=True)
    qa = {}
    qa_rows = []
    rounds = {p.parent.name: pd.read_csv(p, encoding="utf-8-sig") for p in sorted((out / "qa").glob("*/qa_labels.csv"))}
    for name in rounds:
        for _, r in summarize(out / "qa" / name / "qa_labels.csv").iterrows():
            qa[(name, r["stratum"])] = r
            qa_rows.append([_ROUNDS.get(name, name), _STRATA.get(r["stratum"], r["stratum"]), r["n"],
                            r["구조물 맞음"], r["구조물 아님"], r["판단 불가"],
                            f'{r["precision"] * 100:.0f}% ({r["ci_lo"] * 100:.0f}~{r["ci_hi"] * 100:.0f})' if r["precision"] == r["precision"] else "-"])
    # 결과별 정밀도는 그 층이 라벨된 마지막 회차로 낸다(저장소: 미등재 독립·부속 4차, 신규·소멸·선형 3차)
    r_ind, r_att = _latest(rounds, _is_ind), _latest(rounds, lambda q: q["stratum"].eq("unreg_attached"))
    p_ind = stratified_precision(rounds[r_ind], "unreg_", exclude=("unreg_attached", "unreg_linear", "unreg_noreg")) if r_ind else {}
    r_noreg = _latest(rounds, lambda q: q["stratum"].eq("unreg_noreg"))
    p_noreg = stratified_precision(rounds[r_noreg], "unreg_noreg") if r_noreg else {}
    p_att = stratified_precision(rounds[r_att], "unreg_attached") if r_att else {}
    p_loose = stratified_precision(rounds["round3"], "unreg_", exclude=("unreg_linear",)) if "round3" in rounds and r_ind != "round3" else {}
    last = {stratum: (name, r) for (name, stratum), r in qa.items()}
    (ra3, a3), (rg3, g3), (rl3, l3) = (last.get(k, (None, None)) for k in ("new_A", "gone_AB", "unreg_linear"))
    reviewed = bool(qa_rows)
    n_samples = sum(len(q) for q in rounds.values())
    cur = ic["qa"]["round"]
    cur_csv = f"qa/{cur}/qa_labels.csv"
    notes = ic.get("report_notes", {})
    reviewer = ic["qa"].get("reviewer", "담당자")
    has_change = (Path(cfg["paths"]["output_root"]) / "comparisons" / f"{t1}_{t2}" / "rasters" / "change_probability.tif").exists()
    d2_full = len(d2) >= 10  # 촬영 월일까지 있는가
    survey_csv = out / "survey" / "survey_results.csv"
    sres = pd.read_csv(survey_csv, encoding="utf-8-sig", dtype=str).fillna("") if survey_csv.exists() else pd.DataFrame()
    rec1, rec2 = clf["footprint_recall"][t1], clf["footprint_recall"][t2]
    ref = s.get("new_recall_reference", {})
    window = " ~ ".join(ic.get("reference_window", ["", ""]))
    late, gap, _ = _timing(base.get("baseline_date"), d1)
    today = date.today()
    cad_all = len(gpd.read_file(cfg["paths"]["cadastre"], columns=["pnu"], ignore_geometry=True))
    ref_path = cfg["paths"].get("buildings_ref") or cfg["paths"]["buildings"]
    n_bld = len(gpd.read_file(ref_path, ignore_geometry=True))
    n_reg = len(json.loads(Path(cfg["paths"]["building_register"]).read_text(encoding="utf-8")))
    n_pms = len(json.loads(Path(cfg["paths"]["building_permits"]).read_text(encoding="utf-8")))
    aoi_km2 = float(gpd.read_file(cfg["paths"]["aoi"]).to_crs(proj["analysis_crs"]).area.sum()) / 1e6
    P = []
    from ..interpretation import screen as _screen
    scr = _screen.summary(cfg)
    n_cand = len(appendix1(parcels)) + len(appendix1b(parcels)) + len(new_a) + len(gone_ab)

    # ── 표지
    P.append(f'''<section class="page cover"><div><span class="cls">보상 실무 참고자료 · 내부 검토용</span>
<h1>{_e(proj["official_name"])}<br>필지 단위 구조물 판독 결과 보고</h1>
<div class="sub">항공 정사영상({d1} · {d2}) 기반 지장물 현황 점검</div>
<table><tbody>
<tr><td>대상 사업</td><td>{_e(proj["official_name"])} ({_e(proj["operator"])})</td></tr>
<tr><td>대상 지역</td><td>{_e(proj["location"])}</td></tr>
<tr><td>지구 면적</td><td>{aoi_km2:.2f}㎢ (VWorld 공공주택지구 경계 계산값)</td></tr>
<tr><td>분석 영상</td><td>{d1} · {d2} 항공 정사영상</td></tr>
<tr><td>작성일</td><td>{today:%Y. %m. %d.}</td></tr>
</tbody></table></div>
<div class="org">공간정보 분석 검토</div></section>''')

    # ── 요약
    P.append(f'''<section class="page"><h2>요 약</h2>
<div class="kv"><div><div class="k">판독 필지</div><div class="v">{n:,}<small> 필지</small></div></div>
<div><div class="k">{d1} 구조물 있음</div><div class="v">{has:,}<small> 필지</small></div></div>
<div><div class="k">미등재(도면·대장 없음)</div><div class="v">{len(u_ind):,}<small> 필지</small></div></div>
<div><div class="k">신규·소멸 의심</div><div class="v">{len(new_a)}·{len(gone_ab)}<small> 건</small></div></div></div>
<ul class="b">
<li>목적: 보상 대상 지구 내 지장물 누락 방지와 현장조사 우선순위 설정을 위해 항공 정사영상으로 필지별 구조물 현황을 판독함</li>
<li>방법: {d1}·{d2} 항공 정사영상을 0.5m 격자로 조각화하고, 기준 건물 레이어(GIS건물통합정보·도로명주소 건물)로 학습한 판독기로 조각별 구조물 여부를 판정한 뒤 지적 필지 단위로 집계함
<ul class="o"><li>건물마다 건축물대장 연계 여부를 붙여, 대장 연계 건물 밖의 구조물을 미등재로 추출하고(도면·대장 모두 없음 / 도면은 있으나 대장 없음) 두 시기 비교로 신규·소멸 의심을 선별함</li>
<li>{f"판독 결과의 정확도는 층화 무작위 표본({len({k for k, _ in qa})}회, 총 {sum(int(r[2]) for r in qa_rows)}건)을 원본 영상으로 육안 검수하여 산정함" if reviewed
       else f"정확도 산정용 층화 무작위 표본 {n_samples}건을 추출함(검수 전). 담당자가 원본 영상으로 검수하면 정밀도를 산정함"}</li></ul></li>
<li>주요 결과
<ul class="o"><li>{d1} 기준 구조물이 있는 필지는 {has:,}필지(전체의 {has / n * 100:.1f}%)임</li>
<li>건물 윤곽도 대장도 없는 <b>미등재 구조물 {len(u_ind):,}필지</b>(붙임 1)와, 건물 윤곽은 있으나 <b>대장이 없는 건물 위 구조물 {len(u_nreg):,}필지</b>(붙임 1-2)를 추출함{f". 위반건축물이 있는 필지는 {n_violt:,}필지임" if n_violt else ""}. {f"표본 정밀도 {_p(p_ind)}(95% 신뢰구간 {_ci(p_ind)})로, 물건조서 대조용 점검 목록으로 활용 가능함" if p_ind
       else "표본 검수 전으로, 정밀도 확인 후 물건조서 대조용 점검 목록으로 활용함"}</li>
<li>등재 건물과 맞닿은 <b>부속 의심 {len(u_att):,}필지</b>는 {notes.get("attached", "등재 건물 지붕과 이어진 덩어리로")}, {f"표본 정밀도 {_p(p_att)}에 그쳐 " if p_att else ""}선별 확인용으로만 활용함</li>
<li>{d1[:4]}→{d2} <b>신규 의심 {len(new_a)}건, 소멸 의심 {len(gone_ab)}건</b>은 {f"표본 정밀도가 각각 {_fr(a3)}, {_fr(g3)}로 낮아 " if a3 is not None or g3 is not None else ""}전수 영상 확인 후 활용함</li></ul></li>
<li>유의사항
<ul class="o"><li><b>모델 판독 결과는 확정이 아닌 '후보'임.</b> 변화 후보 무작위 표본 검수에서 실제 건물·구조 변화는 약 50~55%였고, 그림자 위치 변화·경사지붕 밝기 차이·나지→포장·차량·적치물·비닐하우스·컨테이너 색 변화 등 실제 변화와 영상 특성이 비슷한 오탐은 임계값 조정만으로 구분되지 않음.
현장조사 전에 후보 {n_cand:,}건(붙임 1·1-2·2)을 전후 영상으로 전수 육안 판독(건당 수 초, 1인 {"반나절" if n_cand <= 700 else "하루"} 내외)하고 확인된 대상만 현장조사로 전달함{f" - 현재 {scr['done']:,}건 판독, 현장조사 대상 {scr['structure']:,}·판단 보류 {scr['uncertain']:,}·오탐 제외 {scr['not_structure']:,}건" if scr else ""}</li>
<li>{f"표본 검수는 {reviewer}한 판독이며, 담당 부서의 재판독 전까지 정확도 수치는 잠정값임" if reviewed
       else f"표본 검수 전이므로 정확도 수치가 없음. 검수표({cur_csv})를 채운 뒤 보고서를 다시 만들면 반영됨"}</li>
{f'<li>잠정 기준일({base["baseline_date"]}, 주민공람일)보다 영상이 {gap}개월 늦어 <b>기준일 당시 현황을 나타내지 않음</b></li>' if late else ""}
{"" if has_change else "<li>화소 변화탐지 교차 확인을 하지 않아 신규·소멸 의심(A·B 등급)이 선별되지 않음</li>"}
<li>판독기의 건물 재현율이 {rec1["recall"] * 100:.0f}%로 낮아, "구조물 없음" 판정을 지장물 부존재의 근거로 사용하지 않음</li></ul></li>
<li>건의: {"보상계획공고 원문의 이주대책기준일 확인" + ("" if d2_full else f", {d2} 영상 촬영일 확인") + (", 기준일 무렵 영상 확보 시 동일 방법으로 재판독" if late else "")}</li>
</ul></section>''')

    # ── 목차
    P.append('''<section class="page"><h2>목 차</h2><ol class="toc">
<li><span>Ⅰ.</span><span>개요</span></li><li><span>Ⅱ.</span><span>분석 자료 및 방법</span></li>
<li><span>Ⅲ.</span><span>판독 결과</span></li><li><span>Ⅳ.</span><span>정확도 검증</span></li>
<li><span>Ⅴ.</span><span>활용 방안 및 향후 과제</span></li>
<li><span>붙임</span><span>1. 미등재(도면·대장 없음) 필지 목록 · 1-2. 미등재(도면 있음·대장 없음) 필지 목록 · 2. 신규·소멸 의심 목록 · 3. 회차별 표본 검수 결과 · 4. 관련 고시·공고</span></li></ol></section>''')

    # ── Ⅰ. 개요
    timeline = [[_e(x.get("notice_date")), _e(x["title"]), _e(x["notice_number"])] for x in cfg.get("official_sources", [])]
    timeline.insert(0, [_e(base.get("baseline_date")), "광명시흥 공공주택지구 주민공람 및 의견청취", "광명시·시흥시 사업 연혁"])
    P.append(f'''<section class="page"><h2>Ⅰ. 개 요</h2>
<h3>1. 추진 배경 및 목적</h3><ul class="b">
<li>공공주택지구 보상 과정에서 물건조사 누락·분쟁을 줄이려면 지구 전체 필지의 구조물 현황을 객관적 자료로 사전 점검할 필요가 있음</li>
<li>본 분석은 항공 정사영상으로 ① 필지별 구조물 유무, ② 건축물대장 미등재 구조물(도면·대장 없음 / 도면 있음·대장 없음), ③ 두 시기 사이 신규·소멸 구조물을 판독하여 현장조사·물건조사의 우선순위를 제시함
<ul class="o"><li>판독 결과는 조사 대상 선정용 보조자료이며, 불법 여부·보상 대상 여부·구조물 용도를 판정하지 않음</li></ul></li></ul>
<h3>2. 대상 지구</h3>
{_tbl("<표 1> 대상 지구 개요", ["구분", "내용"], [["사업명", _e(proj["official_name"])], ["시행자", _e(proj["operator"])],
      ["위치", _e(proj["location"])], ["면적", f"{aoi_km2:,.2f}㎢ (경계 폴리곤 계산값, 보도 면적 1,271만㎡와 0.04% 차이)"],
      ["판독 필지", f"{n:,}필지 (연속지적도 {cad_all:,}개 중 본번·부번 중복 {cad_all - n:,}개 제외)"]],
      ["c", ""], "자료: VWorld 공공주택지구 경계(lt_c_lhzone), 연속지적도")}
<h3>3. 사업 추진 경위 및 잠정 기준일</h3>
{_tbl("<표 2> 사업 추진 경위", ["일자", "내용", "근거"], timeline, ["c", "", ""], "주: 2023년 이후 고시 번호는 언론 보도 기준이며 원문 미확인")}
<ul class="b"><li>잠정 기준일: <b>{_e(base.get("baseline_date"))}</b>(주민공람 및 의견청취일)
<ul class="o"><li>공공주택지구 이주대책 기준일은 원칙적으로 주민공람공고일이므로 이를 잠정 적용함(이전 잠정값: {_e(base.get("baseline_previous"))} 지구지정고시일)</li>
<li>보상계획공고 원문의 이주대책기준일·권리산정기준일 확인 후 확정 필요</li></ul></li></ul>
{f'<div class="box warn"><h4>영상 시점 유의</h4>분석 영상({d1}, {d2})은 모두 잠정 기준일 이후 촬영분으로, {d1} 판독 결과에는 기준일부터 {gap}개월간 신축된 구조물도 포함됨. 기준일 당시 현황 판독에는 2020년 하반기~2021년 2월 촬영 영상이 필요함.</div>' if late else ""}
</section>''')

    figdir = out / "reports" / "figures"
    bld = gpd.read_file(ref_path).to_crs(parcels.crs)
    bld = bld[bld.area > 0]
    fig_method = F.method_panels(cfg, parcels, objs, bld, t1, figdir / "method_panels.png")
    fig_over = F.overview_map(cfg, parcels, objs, t1, figdir / "overview_official.png")
    fig_dist = F.distribution_maps(cfg, parcels, objs, t1, figdir / "distribution.png")
    from ..interpretation.qa import wilson
    def _wr(r):
        k, m = int(r["구조물 맞음"]), int(r["구조물 맞음"] + r["구조물 아님"])
        lo, hi = wilson(k, m)
        return k / max(m, 1), lo, hi
    prow = [(name, d["precision"], d["ci_lo"], d["ci_hi"], col)
            for name, d, col in [("미등재 · 독립", p_ind, "#c8414b"), ("미등재 · 대장 없음", p_noreg, "#7b4fa0"),
                                 ("미등재 · 부속 의심", p_att, "#e9a23b")] if d]
    for name, r, col in [("미등재 · 도로·하천류", l3, "#8a8f94"), ("신규 의심(A)", a3, "#ffb703"), ("소멸 의심(A·B)", g3, "#3a7bd5")]:
        if r is not None:
            prow.append((name, *_wr(r), col))
    fig_prec = F.precision_chart(prow, figdir / "precision.png") if prow else None
    fig_rec = F.recall_chart({d1: rec1, d2: rec2}, figdir / "recall.png")
    n_tiles = len(grid(gpd.read_file(cfg["paths"]["aoi"]).to_crs(proj["analysis_crs"]), ic["resolution_m"], ic["tile_core_m"])[3])
    flow = (_FLOW.replace("@D1@", d1).replace("@D2@", d2).replace("@N@", f"{n:,}")
            .replace("@RES@", f'{ic["resolution_m"]:g}').replace("@T@", f"{n_tiles:,}"))
    leg = clf.get("legacy_rule", {})

    # ── Ⅱ. 자료 및 방법
    P.append(f'''<section class="page"><h2>Ⅱ. 분석 자료 및 방법</h2>
<h3>1. 분석 자료</h3>
{_tbl("<표 3> 분석 자료 목록", ["구분", "자료", "규모·사양", "용도"], [
        ["영상", f"{d1} 항공 정사영상", _e(ep[t1].get("spec", "")), "기준 시기 판독"],
        ["영상", f"{d2} 항공 정사영상", _e(ep[t2].get("spec", "")), "비교 시기 판독"],
        ["지적", "연속지적도(본번·부번)", f"{cad_all:,}개 → {n:,}필지", "필지 집계 단위"],
        ["건물", "GIS건물통합정보(VWorld NED) + 도로명주소 건물", f"{n_bld:,}동" + (f" (대장 연계 {bref['linked']:,}, 위반건축물 {bref['violt']:,})" if bref else ""), "학습 정답·도면 대조"],
        ["대장", "건축물대장 표제부·기본개요·부속지번", f"{n_reg:,}건(표제부)", "건물 단위 대장 연계·필지 대조·신축 재현율 검증"],
        ["허가", "건축인허가 기본개요", f"{n_pms:,}건", "착공일 기준 재현율 검증"],
        ["변화탐지", "화소 단위 앙상블 변화확률(별도 변화탐지)", f'{cfg["imagery"].get("resolution_m", 1):g}m 격자' if has_change else "미실시", "신규·소멸 교차 확인"]],
        ["c", "", "", ""], "주: " + ". ".join(filter(None, [cfg["imagery"].get("report_note"), "대장·허가는 공공데이터포털 건축HUB API, 건물통합정보는 VWorld 국가중점데이터 API로 수집", "화소 변화탐지는 도로명주소 건물을 그대로 사용"])))}
<h3>2. 분석 방법</h3>
<ul class="b">
<li>(조각화) 0.25m 원본을 {ic["resolution_m"]:g}m로 평균하여 지구 전체를 {ic["tile_core_m"]:g}m 타일(여백 {ic["tile_pad_m"]:g}m) {n_tiles:,}개로 나누고 Felzenszwalb 그래프 분할로 색·밝기가 비슷한 화소를 조각으로 묶음
<ul class="o"><li>조각 수: {d1} {s["segments"][t1]:,}개, {d2} {s["segments"][t2]:,}개</li>
<li>조각마다 색조(밝기·색), 형태(사각형 채움률·볼록도·장단축비), 크기, 질감, 경계 강도, 방향별 그림자 비율 등 21개 판독 요소를 계산함</li></ul></li>
<li>(판정) 기준 건물 레이어(대장 여부와 무관하게 건물 윤곽 전체)와 80% 이상 겹치는 조각을 구조물, 2m 이상 떨어진 조각을 비구조물로 두고 경사부스팅 분류기를 학습함
<ul class="o"><li>600m 블록 단위 5분할 공간 교차검증으로 학습 지역과 판정 지역을 분리함</li>
<li>2단계에서 이웃 조각의 1단계 확률을 추가 입력으로 사용하여 큰 지붕이 여러 조각으로 갈리는 문제를 보완함</li>
<li>조각 확률 {rc["prob_hi"]} 이상을 구조물로 판정함</li></ul></li>
<li>(필지 집계) 지적 필지별로 구조물 면적과 대장 연계 건물 면적을 집계하여 다섯 상태(등재+판독, 등재만, 판독만, 없음, 영상 부족)로 구분함</li>
<li>(미등재) 대장 연계 건물 1m 버퍼 밖 구조물 화소 덩어리 중 {rc["min_object_m2"]}㎡ 이상·사각형 채움률 {rc.get("min_rect_fill", 0)} 이상인 것을 추출하고, 판독된 지붕을 따라 등재 건물과 연결되면 '부속 의심'으로 분리함
<ul class="o"><li>덩어리의 절반 이상이 대장 미연계 건물 윤곽 위면 '도면 있음·대장 없음', 어느 건물 윤곽에도 없으면 '도면·대장 없음(독립)'으로 구분함</li>
<li>도로·구거·하천·제방·철도 지목 필지는 포장면 오탐이 많아 '저신뢰'로 표시함</li></ul></li>
<li>(신규·소멸) {d1} 확률 {rc["prob_lo"]} 미만 → {d2} {rc["prob_hi"]} 이상 화소 덩어리를 신규, 반대를 소멸로 보고 다음 조건을 적용함
<ul class="o"><li>정합 오차 여유 {rc["tolerance_m"]}m, 어느 시기든 그림자인 화소 제외, 사각형 채움률 {rc.get("min_rect_fill", 0)} 이상</li>
<li>기존 화소 변화탐지 확률 {rc["corroborate_min"]} 이상(두 방법 일치)이고 기준 건물 윤곽과 겹치지 않는 것을 '신규 의심(A)'으로 함</li></ul></li>
</ul>
{_tbl("<표 4> 기존 판독 방식(중앙선 매포 구간 판독 코드) 대비 개선 사항", ["항목", "기존", "본 분석"], [
        ["판독 단위", "조각 + 판독 요소(색조·형태·크기·그림자)", "동일, 질감·경계 강도·방향별 그림자 추가"],
        ["분할 범위", "필지마다 주변 영상을 따로 분할(여백에 따라 판정 변동)", "지구 전체 고정 타일로 1회 분할 후 필지 집계"],
        ["판정 기준", "수작업 임계값(165필지로 설정·평가)", "기준 건물 레이어 학습 분류기, 공간 교차검증"],
        ["시기 비교", "시기별 독립 판정", "이력 판정·정합 여유·그림자 차단·변화탐지 교차 확인"],
        ["검증", "기준 판독 일치율(전부 '없음' 기준선보다 낮음)", "층화 무작위 표본, 보정용·평가용 회차 분리, 95% 신뢰구간"]],
        ["c", "", ""])}
</section>
<section class="page"><h3>3. 처리 흐름 및 판독 예시</h3>
<figure>{flow}<figcaption>[그림 1] 판독 처리 흐름</figcaption></figure>
<figure><img src="{_embedded_image(fig_method, 1800)}" alt="판독 과정 예시"><figcaption>[그림 2] 판독 과정 예시({d1}, 140m×140m 구역)</figcaption></figure>
<ul class="b"><li>① 원본 영상을 ② 색·밝기가 비슷한 조각으로 나누고, ③ 판독기가 조각마다 구조물 확률을 매긴 뒤, ④ 확률이 높은 덩어리를 대장 연계 건물과 대조함
<ul class="o"><li>대장 연계 건물 밖 덩어리 중 어느 건물 윤곽에도 없으면 '독립'(적색), 대장 없는 건물 윤곽 위면 '대장 없음'(보라), 등재 건물 지붕과 이어지면 '부속 의심'(주황)으로 표시함</li></ul></li></ul>
</section>''')

    # ── Ⅲ. 판독 결과
    srows = [[k, f"{st.get(k, 0):,}", f"{st.get(k, 0) / n * 100:.1f}%", m] for (k, _, _), m in zip(_STATUS, [
        "대장 연계 건물과 영상 판독이 모두 구조물", "대장 연계 건물은 있으나 판독 미검출(대부분 판독 누락)", "대장 연계 건물 없이 영상에 구조물(대장 없는 건물 포함)",
        "두 자료 모두 구조물 없음(판독 누락 가능)", "지구 경계 자투리 필지"])]
    dong = parcels.assign(dong=_dong(parcels["addr"].fillna("")))
    drows = []
    for name, g in dong.groupby("dong"):
        if name == "미상" or len(g) < 20:
            continue
        c = g[f"status_{t1}"].value_counts()
        h = int(sum(c.get(k, 0) for k, _, _ in _STATUS[:3]))
        drows.append([name, f"{len(g):,}", f"{h:,}", f"{h / len(g) * 100:.0f}%", f"{c.get('있음(판독만)', 0):,}",
                      f"{int((g['unregistered_conf'] == '독립').sum())}", f"{int((g['unregistered_conf'] == '대장 없음').sum())}",
                      f"{int((g['unregistered_conf'] == '부속 의심').sum())}",
                      f"{int((g['new_A_n'] > 0).sum())}"])
    drows.sort(key=lambda r: -int(r[1].replace(",", "")))
    jm = u_ind.assign(j=u_ind["jimok"].fillna("미상")).groupby("j").agg(n=("pnu", "size"), m2=("unregistered_m2", "sum")).sort_values("n", ascending=False)
    jrows = [[j, f"{int(r.n)}", f"{r.n / len(u_ind) * 100:.0f}%", f"{r.m2:,.0f}"] for j, r in jm.head(8).iterrows()]

    def chips(rows, width=1600):
        return "".join(f'<figure><img src="{_embedded_image(path, width)}" alt=""><figcaption>{cap}</figcaption></figure>' for path, cap in rows)

    def qa_pick(rnd, cond, k):
        if rnd is None or rnd not in rounds:
            return []
        q = rounds[rnd]
        q = q[cond(q)].head(k)
        return [(out / "qa" / rnd / f"{sid}.jpg", note) for sid, note in zip(q["sample_id"], q["note"])]

    def pending(cond, k):
        """검수 전 표본(현재 회차, 라벨 없음) - 사례 그림 대용."""
        q = rounds.get(cur)
        if q is None:
            return []
        q = q[cond(q) & q["label"].isna()].head(k)
        return [(out / "qa" / cur / f"{sid}.jpg", f"검수 전 · {_STRATA.get(s, s)}") for sid, s in zip(q["sample_id"], q["stratum"])]

    edge = lambda q: q["note"].fillna("").str.startswith("[")
    is_new = lambda q: q["stratum"].eq("new_A")
    is_gone = lambda q: q["stratum"].eq("gone_AB")
    ex_ind = qa_pick(r_ind, lambda q: _is_ind(q) & (q["label"] == "structure"), 6)
    ex_att = qa_pick(r_att, lambda q: (q["stratum"] == "unreg_attached") & edge(q), 2)
    ex_fp = qa_pick(r_ind, lambda q: _is_ind(q) & (q["label"] == "not_structure") & ~edge(q), 2)
    if ra3 == "round3":  # 저장소 결과: 2차 표본 사례 4건 + 3차 2건
        ex_new = qa_pick("round2", lambda q: is_new(q) & q["label"].eq("structure"), 4) + qa_pick("round3", lambda q: is_new(q) & q["label"].eq("structure"), 2)
    else:
        ex_new = qa_pick(ra3, lambda q: is_new(q) & q["label"].eq("structure"), 5)
    ex_new_fp = qa_pick(ra3, lambda q: is_new(q) & q["label"].eq("not_structure"), 2)
    ex_gone = qa_pick(rg3, lambda q: is_gone(q) & q["label"].eq("structure"), 4)
    ex_gone_fp = qa_pick(rg3, lambda q: is_gone(q) & q["label"].eq("not_structure"), 1)
    pend_ind = [] if ex_ind else pending(_is_ind, 6)
    pend_att = [] if ex_att else pending(lambda q: q["stratum"].eq("unreg_attached"), 2)
    pend_new = [] if ex_new else [(p_, f"검수 전 · 신규 의심 A{int(p_.stem[-3:]) + 1:02d}") for p_ in sorted((out / "evidence" / "new_A").glob("new_A_*.jpg"))[:3]]
    pend_gone = [] if ex_gone else pending(is_gone, 3)
    hcount = sorted(((r[0], int(r[2].replace(",", ""))) for r in drows), key=lambda x: -x[1])[:2]
    dong_li = (f"<li>구조물 필지는 {hcount[0][0]}·{hcount[1][0]}에 많으며, 두 동이 전체 구조물 필지의 {sum(h for _, h in hcount) / max(has, 1) * 100:.0f}%를 차지함</li>"
               if len(hcount) == 2 else "")

    P.append(f'''<section class="page"><h2>Ⅲ. 판독 결과</h2>
<h3>1. {d1} 필지별 구조물 현황</h3>
<ul class="b"><li>전체 {n:,}필지 중 구조물이 있는 필지는 {has:,}필지({has / n * 100:.1f}%)이며, 이 중 대장 연계 건물 없이 영상에서만 확인된 필지는 {st.get("있음(판독만)", 0):,}필지임</li>
{dong_li}</ul>
{_tbl("<표 5> 필지 상태별 현황", ["상태", "필지", "비율", "의미"], srows, ["", "n", "n", ""], sum_row=["계", f"{n:,}", "100.0%", ""])}
{_tbl("<표 6> 법정동별 판독 현황", ["법정동", "필지", "구조물 있음", "구조물 비율", "판독만", "미등재(독립)", "대장 없음", "부속 의심", "신규 의심"], drows, ["c", "n", "n", "n", "n", "n", "n", "n", "n"])}
</section>
<section class="page figpage"><figure><img src="{_embedded_image(fig_over, 2000)}" alt="필지 상태 지도"><figcaption>[그림 3] {d1} 필지 상태 및 신규 의심 위치</figcaption></figure></section>
<section class="page">
<h3>2. 미등재 구조물</h3>
<ul class="b"><li>대장 연계 건물 밖에서 구조물이 판독된 필지는 {len(unreg):,}필지이며, 건물 윤곽·대장 유무, 등재 건물과의 연결 여부, 지목에 따라 네 가지로 구분함</li>
<li>도면·대장 모두 없는 독립 미등재 {len(u_ind):,}필지(붙임 1)가 물건조서 대조의 1순위, 도면은 있으나 대장 없는 {len(u_nreg):,}필지(붙임 1-2)가 2순위 대상임. 지목은 {"·".join(jm.index[:3])} 순으로 {"많으며 " + notes["unreg_types"] if notes.get("unreg_types") else "많음"}</li></ul>
{_tbl("<표 7> 미등재 구조물 구분별 현황 및 표본 정밀도", ["구분", "필지", "덩어리(개)", "면적(㎡)", "표본 정밀도", "95% 신뢰구간", "주된 오탐"], [
        ["독립(도면·대장 없음)", f"{len(u_ind):,}", f"{int(u_ind['unregistered_n'].sum()):,}", f"{u_ind['unregistered_m2'].sum():,.0f}", _p(p_ind), _ci(p_ind), notes.get("unreg_fp", "-")],
        ["도면 있음·대장 없음", f"{len(u_nreg):,}", f"{int(u_nreg['unregistered_n'].sum()):,}", f"{u_nreg['unregistered_m2'].sum():,.0f}", _p(p_noreg), _ci(p_noreg), "-"],
        ["부속 의심", f"{len(u_att):,}", f"{int(u_att['unregistered_n'].sum()):,}", f"{u_att['unregistered_m2'].sum():,.0f}", _p(p_att), _ci(p_att), notes.get("attached_fp", "-")],
        ["저신뢰(도로·하천류)", f"{len(u_low)}", f"{int(u_low['unregistered_n'].sum()):,}", f"{u_low['unregistered_m2'].sum():,.0f}", _fr(l3), "-", notes.get("linear_fp", "-")]],
        ["", "n", "n", "n", "n", "c", ""],
        (f"주: {_rn(r_ind)} 표본(지목별 층화, 등재 건물 일부가 아닌 구조물만 정답)." + (f" 저신뢰는 {_rn(rl3)} 표본 건수." if rl3 else "")
         + (f" 등재 지붕 가장자리를 정답에 포함하면 {_p(p_loose)}(3차)" if p_loose else "")) if p_ind
        else f"주: 표본 검수 전 - 검수표({cur_csv}) 입력 후 보고서를 다시 만들면 정밀도가 채워짐")}
{_tbl("<표 8> 독립 미등재 지목별 현황(상위 8)", ["지목", "필지", "비율", "면적(㎡)"], jrows, ["c", "n", "n", "n"])}
</section>
<section class="page figpage"><figure><img src="{_embedded_image(fig_dist, 2000)}" alt="분포 지도"><figcaption>[그림 4] 미등재 구조물 필지 및 신규·소멸 의심 분포</figcaption></figure></section>
<section class="page">''' + (f'''<h4>[그림 5] 독립 미등재 구조물 판독 사례({_rn(r_ind)} 표본, 실제 구조물로 확인)</h4>
<div class="fig2">{chips(ex_ind, 900)}</div><div class="src">노란 선: 판독 덩어리 · 흰 선: 필지 경계 · {d1} 영상</div>
<h4>[그림 6] 구분이 필요한 사례 - 부속 의심(등재 지붕 가장자리)과 독립 오탐</h4>
<div class="fig2">{chips([(p_, "부속 의심 · " + _e(c_)) for p_, c_ in ex_att] + [(p_, "독립 오탐 · " + _e(c_)) for p_, c_ in ex_fp], 900)}</div>''' if ex_ind else f'''<h4>[그림 5] 미등재 구조물 검수 표본 예시(검수 전 - 노란 선 안이 구조물인지 확인 필요)</h4>
<div class="fig2">{chips(pend_ind + pend_att, 900)}</div><div class="src">노란 선: 판독 덩어리 · 흰 선: 필지 경계 · {d1} 영상 · 검수표 {cur_csv}</div>''') + '''
<p class="note">독립 미등재 필지 전체 목록은 붙임 1 참조</p></section>''')

    # 건물통합정보·건축물대장 대조
    if "register_check" in parcels:
        rcx = parcels[parcels["register_check"] != ""]
        cnt = lambda d, c: f'{int((d["register_check"] == c).sum()):,}'
        from ..interpretation.reference import register_area_ratio
        ratio = register_area_ratio(parcels, t1)
        ratio_txt = (f"대장 건축면적 대비 판독 면적은 중앙값 {ratio.median():.2f}배(비교 가능 {len(ratio):,}필지)"
                     if len(ratio) else "면적을 비교할 수 있는 필지가 없음")
        jm2 = u_nreg["jimok"].value_counts()
        bl = ([["기준 건물(윤곽)", f"{bref['buildings']:,}동", f"건물통합정보 {bref['gis']:,} + 도로명주소에만 있는 건물 {bref['road_only']:,}"],
               ["대장 연계", f"{bref['linked']:,}동", f"표제부와 건물 단위로 맞춘 것 {bref['register_matched']:,}동(필지·건축면적·연면적·사용승인일)"],
               ["대장 미연계", f"{bref['unlinked']:,}동", "도면에는 있으나 대장이 없는 건물(창고·가설물 등)"],
               ["위반건축물", f"{bref['violt']:,}동", f"필지 {n_violt:,}개"]] if bref else [])
        P.append(f'''<section class="page">
<h3>2-1. 건물통합정보·건축물대장 대조</h3>
<ul class="b"><li>GIS건물통합정보는 건물 윤곽마다 건축물대장 연계 여부와 위반건축물 여부를 담고 있어, 대장 대조를 건물 단위로 함. 도로명주소 건물만 쓰던 이전 방식은 대장 없는 건물도 '등재'로 보았음</li>
<li>도면은 있으나 <b>대장이 없는 건물 위 구조물 {len(u_nreg):,}필지</b>를 붙임 1-2(SHP·CSV)로 제공함{f". 지목은 {'·'.join(jm2.index[:3])} 순으로 많음" if len(jm2) else ""}</li>
<li>필지 단위로도 대장(표제부·기본개요·부속지번)을 대조하여 '대장 없음·대장 면적 초과(판독 면적이 대장 건축면적의 {rc.get("register_excess_ratio", 2.0):g}배·{rc.get("register_excess_min_m2", 50):g}㎡ 초과)'를 표시함. {ratio_txt}</li></ul>
{_tbl("<표 8-1> 기준 건물 레이어 구성", ["구분", "규모", "내용"], bl, ["c", "n", ""]) if bl else ""}
{_tbl("<표 8-2> 필지 단위 건축물대장 대조(구조물 판독 필지)", ["구분", "필지", "대장 없음", "대장 면적 초과", "대장 있음"], [
        ["구조물 판독 필지 전체", f"{len(rcx):,}", cnt(rcx, "대장 없음"), cnt(rcx, "대장 면적 초과"), cnt(rcx, "대장 있음")],
        ["  (독립 미등재)", f"{len(u_ind):,}", cnt(u_ind, "대장 없음"), cnt(u_ind, "대장 면적 초과"), cnt(u_ind, "대장 있음")],
        ["  (도면 있음·대장 없음)", f"{len(u_nreg):,}", cnt(u_nreg, "대장 없음"), cnt(u_nreg, "대장 면적 초과"), cnt(u_nreg, "대장 있음")]],
        ["", "n", "n", "n", "n"], "주: 건물통합정보의 대장 연계 번호는 건축HUB 대장 관리번호와 체계가 달라 필지·면적·사용승인일로 맞춤. 필지 단위 대조는 지적 분할·합병 후 지번 불일치, 대장 대상이 아닌 농업용 가설물을 포함할 수 있음")}
</section>''')

    # 신규·소멸
    verdicts = _qa_verdicts(out / "qa", new_a)
    rr = [[k, f'{v["parcels"]}', f'{v["A"]}', f'{v["A_or_B"]}', f'{v["A_B_or_C"]}'] for k, v in ref.items()]
    ref_n = max((v["parcels"] for v in ref.values()), default=0)
    v0 = next(iter(ref.values()), None)
    ref_li = ("" if not v0 else f"<li>행정자료(건축물대장 사용승인일, 건축허가 착공일) 기준 {window} 신축 필지와 비교한 결과, "
              + ("신규 판정이 신축을 대부분 검출하지 못함" if v0["A_or_B"] * 2 < v0["parcels"] else f"신규 판정(A·B)이 {v0['A_or_B']}/{v0['parcels']}필지를 검출함") + "</li>")
    obs = (f'<ul class="o"><li>실제 신규: {notes["new_true"]} / 실제 소멸: {notes["gone_true"]}</li><li>주요 오탐: {notes["change_fp"]}</li></ul>'
           if notes.get("new_true") else "")
    head = (f"<li>표본 정밀도가 신규 {_fr(a3)}, 소멸 {_fr(g3)}로 낮아 목록 전체를 영상으로 확인한 후 활용해야 함{obs}</li>" if a3 is not None or g3 is not None
            else "<li>표본 검수 전이므로 목록 전체를 영상으로 확인한 후 활용해야 함</li>")
    if not has_change:
        head += "<li>화소 변화탐지 교차 확인을 하지 않아 A·B 등급이 없음. 판독기만으로 잡힌 C 등급은 오탐이 많아 목록에 싣지 않음</li>"
    tbl9 = (_tbl("<표 9> 행정자료 대비 신규 판정 재현율", ["기준", "지구 내 필지", "신규 A", "신규 A·B", "A·B·C(판독만 포함)"], rr, ["c", "n", "n", "n", "n"],
                 "주: " + (f"표본이 작아({ref_n}필지) 참고 수준임. " if ref_n < 30 else "") + "착공 후 미준공·영상 촬영 시점 차이로 검출이 안 될 수 있음") if rr else "")
    if ex_new or ex_gone:
        figs = f'''<h4>[그림 7] 실제 신규 구조물 사례(표본 검수 확인, 왼쪽 {d1} · 오른쪽 {d2})</h4>
<div class="fig1">{chips([(p_, "<b>실제 신규</b> · " + _e(c_)) for p_, c_ in ex_new[:3]])}</div></section>
<section class="page"><div class="fig1">{chips([(p_, "<b>실제 신규</b> · " + _e(c_)) for p_, c_ in ex_new[3:5]])}</div>
<h4>[그림 8] 신규 의심 오탐 사례</h4><div class="fig1">{chips([(p_, "<b>오탐</b> · " + _e(c_)) for p_, c_ in ex_new_fp[:2]])}</div></section>
<section class="page"><h4>[그림 9] 실제 소멸(철거) 사례(왼쪽 {d1} · 오른쪽 {d2})</h4>
<div class="fig1">{chips([(p_, "<b>실제 철거</b> · " + _e(c_)) for p_, c_ in ex_gone[:3]])}</div></section>
<section class="page"><div class="fig1">{chips([(p_, "<b>실제 철거</b> · " + _e(c_)) for p_, c_ in ex_gone[3:4]] + [(p_, "<b>소멸 오탐</b> · " + _e(c_)) for p_, c_ in ex_gone_fp])}</div>'''
    elif pend_new or pend_gone:
        figs = f'''<h4>[그림 7] 신규 의심 근거 영상(검수 전, 왼쪽 {d1} · 오른쪽 {d2})</h4>
<div class="fig1">{chips(pend_new[:2])}</div></section>
<section class="page"><div class="fig1">{chips(pend_new[2:3])}</div>
<h4>[그림 8] 소멸 의심 근거 영상(검수 전)</h4><div class="fig1">{chips(pend_gone[:2])}</div>'''
    else:
        figs = ""
    P.append(f'''<section class="page">
<h3>3. {d1[:4]}→{d2} 신규·소멸 의심</h3>
<ul class="b"><li>신규 의심(A) {len(new_a)}건({new_a["area_m2"].sum():,.0f}㎡), 소멸 의심(A·B) {len(gone_ab)}건({gone_ab["area_m2"].sum():,.0f}㎡)을 선별함(분포는 [그림 4] 오른쪽)</li>
{head}
{ref_li}</ul>
{tbl9}
{figs}
<p class="note">신규·소멸 의심 전체 목록과 표본 검수 결과는 붙임 2, 전체 근거 영상은 evidence/new_A/ 참조</p></section>''')

    # ── Ⅳ. 정확도 검증
    P.append(f'''<section class="page"><h2>Ⅳ. 정확도 검증</h2>
<h3>1. 판독기 성능(기준 건물 레이어 기준)</h3>
{_tbl("<표 10> 판독기 공간 교차검증 결과", ["지표", "값", "비고"], [
        ["조각 판별력(AUC)", f'{clf["auc"]:.3f}', f'1단계 {clf["stage1"]["auc"]:.3f} → 2단계(이웃 조각 정보) 적용' if clf.get("stage1") else ""],
        ["평균 정밀도(AP)", f'{clf["average_precision"]:.3f}', f'1단계 {clf["stage1"]["average_precision"]:.3f}' if clf.get("stage1") else ""],
        ["학습 조각 / 구조물 조각", f'{clf["labeled"]:,} / {clf["positive"]:,}', "건물 경계에 걸친 조각은 학습 제외"],
        ["기존 판독 코드 규칙", f'정밀도 {leg["precision"] * 100:.0f}% · 재현율 {leg["recall"] * 100:.1f}%' if leg.get("precision") is not None else "구조물 판정 없음",
         "1m 영상용 임계값으로 본 영상에서 작동하지 않음"]],
        ["", "n", ""])}
<figure><img src="{_embedded_image(fig_rec, 1600)}" alt="건물 크기별 재현율"><figcaption>[그림 10] 건물 크기별 재현율(건물 면적의 50% 이상 판독된 비율)</figcaption></figure>
<ul class="b"><li>판독기는 구조물로 판정한 곳의 신뢰도는 높으나 놓치는 건물이 많음(특히 50㎡ 미만, 기준 건물 20㎡ 이상 {rec1['buildings']:,}동 기준)
<ul class="o">{f"<li>{notes['recall_test']}</li>" if notes.get("recall_test") else ""}
<li>따라서 필지 현황은 영상 판독 단독이 아닌 기준 건물 레이어와 함께 판단하고, '없음'을 부존재 근거로 쓰지 않음</li></ul></li></ul>
</section>
<section class="page">
<h3>2. 표본 육안 검수</h3>''' + (f'''
<ul class="b"><li>층화 무작위 표본을 원본 0.25m 영상으로 판독하여 결과별 정밀도를 산정함
<ul class="o">{f"<li>{notes['qa_exclusion']}</li>" if notes.get("qa_exclusion") else ""}
<li>판독은 {reviewer}함. 담당 부서 재판독용 표(qa/human_review.csv)를 함께 제공함</li></ul></li></ul>
{f'<figure><img src="{_embedded_image(fig_prec, 1600)}" alt="결과별 표본 정밀도"><figcaption>[그림 11] 결과별 표본 정밀도와 95% 신뢰구간</figcaption></figure>' if fig_prec else ""}
{_tbl("<표 11> 주요 결과별 표본 정밀도", ["결과", "평가 회차", "표본(판단 가능)", "정밀도", "95% 신뢰구간"], [
        ["미등재 · 독립", _rn(r_ind), f'{p_ind.get("labeled", "-")}', _p(p_ind), _ci(p_ind)],
        ["미등재 · 부속 의심", _rn(r_att), f'{p_att.get("labeled", "-")}', _p(p_att), _ci(p_att)],
        ["미등재 · 도로·하천류", _rn(rl3), _fr(l3).split("/")[-1], _fr(l3), "-"],
        ["신규 의심(A)", _rn(ra3), _fr(a3).split("/")[-1], _fr(a3), "-"],
        ["소멸 의심(A·B, 전수)", _rn(rg3), _fr(g3).split("/")[-1], _fr(g3), "-"]],
        ["", "c", "n", "n", "c"], "주: 정밀도는 판단 불가를 제외하고, 미등재는 지목별 모집단 크기로 가중함. 회차별 상세는 붙임 3")}''' if reviewed else f'''
<div class="box warn"><h4>표본 검수 전</h4>정확도 산정용 층화 무작위 표본 {n_samples}건을 뽑아 두었음(표본 영상 qa/{cur}/*.jpg). 담당자가 영상을 보고
검수표 <b>{cur_csv}</b>의 label 열에 structure(구조물 맞음) · not_structure(아님) · uncertain(판단 불가)을 적은 뒤
Add-in의 <b>보고서 다시 만들기</b>를 실행하면 이 장과 요약의 정밀도가 채워짐.
<ul class="d"><li>미등재 표본은 노란 선 안 덩어리 중 하나 이상이 등재 건물의 일부가 아닌 구조물이면 structure</li>
<li>신규 표본은 {d1}에 없던 구조물이 {d2}에 있으면, 소멸 표본은 {d1}의 구조물이 {d2}에 없어졌으면 structure</li></ul></div>
{_tbl("<표 11> 검수 표본 구성", ["층", "모집단", "표본"], [[_STRATA.get(s, s), f"{int(g['population'].iloc[0]):,}", f"{len(g)}"] for s, g in rounds[cur].groupby("stratum", sort=False)] if cur in rounds else [],
      ["", "n", "n"], "주: 미등재는 지목군별 최대 " + str(ic["qa"].get("per_group", 8)) + "필지, 신규·소멸은 A·B 등급 전수")}''') + '''
</section>''')

    # ── Ⅳ-3. 현장조사 결과(survey_results.csv가 있을 때)
    if survey_csv.exists():
        sv = survey_summary(survey_csv)
        srows_ = [[r.kind, f"{r.targets:,}", f"{r.surveyed:,}", f"{r.exists:,}", f"{r.absent:,}", f"{r.unknown:,}",
                   f"{r.precision * 100:.0f}% ({r.ci_lo * 100:.0f}~{r.ci_hi * 100:.0f})" if r.precision == r.precision else "-",
                   f"{r.missing_in_register:,}"] for r in sv.itertuples()]
        uses = sres[(sres["EXISTS"] == "있음") & (sres["USE_TYPE"] != "")]["USE_TYPE"].value_counts()
        P.append(f'''<section class="page"><h3>3. 현장조사 결과</h3>
<ul class="b"><li>판독 목록(붙임 1·2)을 현장조사표로 조사한 결과를 집계함. 현장 정밀도 = 실재 / (실재 + 없음){" - 후보 판독에서 오탐으로 제외한 후보는 조사하지 않았으므로 판독을 거친 대상의 정밀도임" if scr and scr["not_structure"] else ""}
<ul class="o"><li>미등재·신규는 현장에 구조물이 있으면, 소멸은 구조물이 실제로 없어졌으면 '실재'로 기록함</li>
<li>'실재'이면서 물건조서에 없는 것을 <b>물건조서 누락 의심</b>으로 집계함</li></ul></li></ul>
{_tbl("<표 14> 구분별 현장조사 결과", ["구분", "대상", "조사", "실재", "없음", "확인불가", "현장 정밀도(95%)", "물건조서 누락 의심"], srows_,
      ["c", "n", "n", "n", "n", "n", "n", "n"], "자료: " + _e(survey_csv.name))}
{_tbl("<표 15> 실재 구조물 용도", ["용도", "건수"], [[_e(k), f"{v:,}"] for k, v in uses.items()], ["c", "n"]) if len(uses) else ""}
</section>''')

    # ── Ⅴ. 활용 및 과제
    if scr:
        kinds = [[_e(k), f'{v["total"]:,}', f'{v["structure"]:,}', f'{v["uncertain"]:,}', f'{v["not_structure"]:,}',
                  f'{v["total"] - v["structure"] - v["uncertain"] - v["not_structure"]:,}'] for k, v in scr["by_kind"].items()]
        rs = ", ".join(f"{_e(k)} {v}" for k, v in scr["reasons"].items())
        P.append(f'''<section class="page"><h3>4. 후보 육안 판독 결과</h3>
<ul class="b"><li>모델 후보를 전후 영상으로 전수 판독하여 현장조사 대상을 선별함. 오탐으로 판독한 후보는 현장조사표에서 제외함</li>
<li>오탐 원인: {rs or "-"}</li></ul>
{_tbl("<표 16> 구분별 후보 판독 결과", ["구분", "후보", "현장조사 대상", "판단 보류", "오탐(제외)", "미판독"], kinds, ["c", "n", "n", "n", "n", "n"],
      "자료: interpretation/screen/screen_labels.csv · 판단 보류와 미판독은 현장조사표에 포함됨")}
</section>''')

    P.append(f'''<section class="page"><h2>Ⅴ. 활용 방안 및 향후 과제</h2>
<h3>1. 활용 방안(우선순위)</h3>
{_tbl("<표 12> 결과별 활용 방안", ["순위", "대상", "규모", "활용 방법", "신뢰 수준"], [
        ["0", "후보 전수 육안 판독", f"{n_cand:,}건", "Add-in [후보 판독]으로 전후 영상 확인 - 오탐은 현장조사에서 제외, 확인된 대상과 판단 보류만 현장조사표로 전달",
         f'<span class="pill p-ok">판독 {scr["done"]:,}/{scr["total"]:,}</span>' if scr else '<span class="pill p-mid">현장조사 전 필수</span>'],
        ["1", "독립 미등재 구조물(붙임 1)", f"{len(u_ind):,}필지", "물건조서 등재 여부 대조, 미등재 시 현장조사 대상 지정",
         '<span class="pill p-ok">우선 활용</span>' if p_ind else '<span class="pill p-mid">검수 후 활용</span>'],
        ["2", "도면 있음·대장 없음(붙임 1-2)", f"{len(u_nreg):,}필지", "영상에 건물이 실제로 있는지 확인 후 물건조서·건축물대장 대조(위반건축물 필지 우선)",
         '<span class="pill p-ok">우선 활용</span>' if p_noreg else '<span class="pill p-mid">검수 후 활용</span>'],
        ["3", "신규·소멸 의심", f"{len(new_a)}·{len(gone_ab)}건", "근거 영상 전수 확인 후 인허가·가설건축물 신고 대조", '<span class="pill p-mid">확인 후 활용</span>'],
        ["4", "부속 의심", f"{len(u_att):,}필지", "등재 건물 증축·부속 건물 여부만 선별 확인", '<span class="pill p-mid">선별 활용</span>'],
        ["5", "도로·하천류 저신뢰", f"{len(u_low)}필지", "타 목록 확인 후 잔여 검토", '<span class="pill p-low">참고</span>']],
        ["c", "", "n", "", "c"])}
<h3>2. 한계</h3><ul class="b">
{"" if not (late or not d2_full) else "<li>영상 시점: " + "이며, ".join(filter(None, ["두 영상 모두 잠정 기준일 이후 촬영분" + ("임" if d2_full else "") if late else "", "" if d2_full else f"{d2} 영상은 촬영 월일이 확인되지 않음"])) + "</li>"}
<li>판독 누락: 건물 재현율 {rec1["recall"] * 100:.0f}%로 소형·어두운 지붕 구조물을 다수 놓침</li>
<li>정답 자료: 학습 정답인 기준 건물 레이어(건물통합정보)에도 무허가·가설 구조물은 일부만 있어 판독기 평가에 한계가 있고, 이미 철거된 건물 윤곽이 남아 있을 수 있으며, {"표본 검수는 담당 부서 재판독 전 잠정치임" if reviewed else "표본 검수 전이라 정확도가 산정되지 않음"}</li>
<li>신규·소멸 판정: 지붕 도색·피복 교체·천막을 구조물 변화와 구분하지 못하며, 높이 정보(수치표면모델)가 필요함</li></ul>
<h3>3. 향후 과제 및 협조 요청</h3>
{_tbl("<표 13> 향후 과제", ["구분", "내용", "담당"], [r for r in [
        ["기준일 확정", "보상계획공고(2025.08.27, 2026.07.31) 원문의 이주대책기준일·권리산정기준일 확인", "사업부서"],
        None if d2_full else ["영상 정보", f"{d2} 항공사진 촬영 월일 및 제공 기관 확인", "사업부서"],
        ["추가 영상", "기준일 무렵(2020 하반기~2021.02) 정사영상 확보 후 동일 방법 재판독", "사업부서·분석"] if late else None,
        ["검수 확정", "표본 재판독(qa/human_review.csv) 후 정확도 확정", "사업부서"] if reviewed else ["표본 검수", f"검수표({cur_csv}) 입력 후 보고서 재작성으로 정확도 산정", "사업부서"],
        ["성능 개선", "독립 미등재 오탐(포장면·수목) 저감, 소형 건물 판독 개선", "분석"]] if r], ["c", "", "c"])}
</section>''')

    # ── 붙임 1
    field = sres.set_index("SID")["EXISTS"].to_dict() if len(sres) else {}
    has_reg = "register_check" in parcels

    def _rows(lst, code):
        return [[r.SEQ, f'<span class="pnu">{_e(r.pnu)}</span>', _e(str(r.addr).replace("경기도 ", "")), _e(r.jimok), int(r.unregistered_n),
                 f"{r.unregistered_m2:,.0f}", f"{getattr(r, 'mapped_m2', r.registered_m2):,.0f}", f"{r.registered_m2:,.0f}"]
                + ([_e(getattr(r, "register_check", "")).replace("대장 ", "")] if has_reg else [])
                + ([_e(field.get(f"{code}{r.SEQ:03d}", ""))] if field else [])
                for r in lst.itertuples()]

    head = ["연번", "PNU", "소재지", "지목", "덩어리", "미등재(㎡)", "도면(㎡)", "대장 건물(㎡)"] + (["대장"] if has_reg else []) + (["현장"] if field else [])
    align = ["c", "pnu", "", "c", "n", "n", "n", "n"] + (["c"] if has_reg else []) + (["c"] if field else [])
    note = ("주: 미등재 = 대장 연계 건물 1m 밖 판독 구조물 덩어리 면적 합, 도면 = 필지 내 건물 윤곽 전체 면적(건물통합정보·도로명주소), "
            "대장 건물 = 그중 대장 연계 건물 면적. " + ("대장 = 필지 단위 건축물대장 대조(없음 / 면적 초과 / 있음). " if has_reg else ""))
    P.append(f'''<section class="page"><h2>붙임 1. 미등재(도면·대장 없음) 구조물 필지 목록</h2>
{_tbl(f"<표 붙1> 건물 윤곽도 대장도 없는 구조물이 있는 필지({len(u_ind):,}필지, 법정동·지번순)", head, _rows(appendix1(parcels), "U"), align,
      note + (f"표본상 약 {round(p_ind['precision'] * 10)}할이 실제 미등재 구조물이며 필지별 영상 확인 필요" if p_ind else "표본 검수 전 - 필지별 영상 확인 필요")
      + f". 같은 목록의 필지 폴리곤 SHP(연번 SEQ 동일): reports/{SHP_NAME}.zip")}
</section>''')
    if len(u_nreg):
        P.append(f'''<section class="page"><h2>붙임 1-2. 미등재(도면 있음·대장 없음) 구조물 필지 목록</h2>
{_tbl(f"<표 붙1-2> 건물 윤곽은 있으나 대장이 없는 건물 위 구조물만 있는 필지({len(u_nreg):,}필지, 법정동·지번순)", head, _rows(appendix1b(parcels), "R"), align,
      note + f"같은 목록의 필지 폴리곤 SHP(연번 SEQ 동일): reports/{NAME_NOREG}.zip")}
</section>''')

    # ── 붙임 2
    nrows = [[f"A{i + 1:02d}", f'<span class="pnu">{_e(r.pnu)}</span>', f"{r.area_m2:,.0f}", f"{r.prob_from:.2f}→{r.prob_to:.2f}", f"{r.change_prob:.2f}", verdicts[i]]
             for i, r in new_a.iterrows()]
    gverd = _qa_verdicts(out / "qa", gone_ab) if len(gone_ab) else []
    gq = []
    for p in sorted((out / "qa").glob("*/qa_labels.csv")):
        q = pd.read_csv(p, encoding="utf-8-sig")
        gq.append(q[(q["stratum"] == "gone_AB") & q["label"].notna()])
    gq = pd.concat(gq) if gq else pd.DataFrame()
    grows = []
    for i, r in gone_ab.iterrows():
        v = _VERDICT[None]
        if len(gq):
            dd = ((gq["x"] - r.geometry.centroid.x) ** 2 + (gq["y"] - r.geometry.centroid.y) ** 2) ** .5
            if dd.min() <= 1:
                lab = gq.loc[dd.idxmin(), "label"]
                v = {"structure": '<span class="pill p-ok">실제 철거</span>', "not_structure": '<span class="pill p-low">아님</span>'}.get(lab, _VERDICT["uncertain"])
        grows.append([f"G{i + 1:02d}", f'<span class="pnu">{_e(r.pnu)}</span>', f"{r.area_m2:,.0f}", f"{r.prob_from:.2f}→{r.prob_to:.2f}", f"{r.change_prob:.2f}", v])
    _ = gverd
    P.append(f'''<section class="page"><h2>붙임 2. 신규·소멸 의심 목록</h2>
{_tbl(f"<표 붙2> 신규 의심(A) {len(new_a)}건(면적순)", ["번호", "PNU", "면적(㎡)", f"구조물 확률 {d1[:4]}→{d2}", "변화확률", "표본 검수"], nrows,
      ["c", "pnu", "n", "c", "n", "c"], "주: 확률 = 판독기 구조물 확률, 변화확률 = 기존 화소 변화탐지 평균. 표본 검수는 위치가 일치(1m 이내)하는 검수 결과")}
{_tbl(f"<표 붙3> 소멸 의심(A·B) {len(gone_ab)}건(면적순)", ["번호", "PNU", "면적(㎡)", f"구조물 확률 {d1[:4]}→{d2}", "변화확률", "표본 검수(전수)"], grows,
      ["c", "pnu", "n", "c", "n", "c"])}
</section>''')

    # ── 붙임 3, 4
    src_rows = [[_e(x.get("notice_date")), _e(x["title"]), _e(x["agency"]), _e(x["notice_number"])] for x in cfg.get("official_sources", [])]
    calib = Path(cfg["paths"]["reports"]) / "interpretation_calibration.md"
    P.append(f'''<section class="page"><h2>붙임 3. 회차별 표본 검수 결과</h2>
{_tbl("<표 붙4> 회차·층별 표본 검수 결과", ["회차", "층", "표본", "맞음", "아님", "판단 불가", "정밀도(95%)"], qa_rows, ["", "", "n", "n", "n", "n", "n"],
      "주: " + notes["rounds"] if notes.get("rounds") else "") if qa_rows else f'<p>표본 검수 전(검수표 {cur_csv})</p>'}
<h2 style="margin-top:12mm">붙임 4. 관련 고시·공고</h2>
{_tbl("<표 붙5> 관련 고시·공고", ["일자", "제목", "기관", "번호"], src_rows, ["c", "", "c", ""],
      "주: 2023년 이후 고시 번호와 보상계획공고는 언론 보도 기준(원문 미확인)")}
<p class="note" style="margin-top:8mm">산출물: {_e(out.as_posix())}/ (필지 판정 reports/parcel_interpretation.csv · vectors/parcel_interpretation.gpkg,
판독 덩어리 vectors/structure_change_objects.gpkg, 근거 영상 evidence/new_A/, 검수 표본 qa/) · 설정 {_e(config_path or f"config/projects/{proj['id']}.yaml")}{" · 보정 기록 reports/interpretation_calibration.md" if calib.exists() else ""}</p>
</section>''')

    doc = "".join(P)
    # 조건에 따라 빠지는 그림·표가 있어 번호를 나온 순서대로 다시 매긴다(본문 참조도 같은 번호로)
    for label, cap_pat in (("그림", r"(?:<figcaption>|<h4>)\[그림 (\d+)\]"), ("표", r'<div class="cap">〈표 (\d+)〉')):
        remap = {old: str(i + 1) for i, old in enumerate(dict.fromkeys(re.findall(cap_pat, doc)))}
        if label == "그림":
            doc = re.sub(r"\[그림 (\d+)\]", lambda m: f"[그림 {remap.get(m.group(1), m.group(1))}]", doc)
        else:
            doc = re.sub(r"〈표 (\d+)〉", lambda m: f"〈표 {remap.get(m.group(1), m.group(1))}〉", doc)
    title = f'{proj["short_name"]} 필지 구조물 판독 결과 보고'
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>{title}</title><style>{_CSS}</style></head><body>'
            f'<div class="toolbar noprint">A4 보고서 · 인쇄(Ctrl+P) 시 쪽 단위로 나뉨</div>{doc}</body></html>')


def generate(cfg: dict, config_path: str | None = None, suffix: str = "") -> Path:
    """보고서 HTML과 붙임 1 SHP를 쓴다. suffix(예: _20261106_102522)를 주면 둘 다 그 이름을 붙여 새로 만든다
    - 다시 만들 때 이전 보고서를 덮어쓰지 않고, Pro가 잠근 이전 SHP도 건드리지 않는다."""
    ic = cfg["interpretation"]
    reports = Path(ic["out_dir"]) / "reports"
    out = reports / f'{cfg["project"]["id"]}_parcel_interpretation_official{suffix}.html'
    out.write_text(_html(cfg, config_path).replace(f"reports/{SHP_NAME}.zip", f"reports/{SHP_NAME}{suffix}.zip"), encoding="utf-8")
    parcels = gpd.read_file(Path(ic["out_dir"]) / "vectors" / "parcel_interpretation.gpkg")
    t1, t2 = ic["epochs"]
    dates = {e["id"]: e["date"] for e in cfg["imagery"]["epochs"]}
    write_appendix1_shp(parcels, f"status_{t1}", reports / f"{SHP_NAME}{suffix}", name=f"{SHP_NAME}{suffix}",
                        note=f"{cfg['project']['official_name']} · 판독 영상 {dates[t1]}·{dates[t2]} · 보고서 {out.name}")
    rm = appendix1b(parcels)
    if len(rm):
        write_appendix1_shp(parcels, f"status_{t1}", reports / f"{NAME_NOREG}{suffix}", name=f"{NAME_NOREG}{suffix}", rows=rm,
                            title="붙임 1-2. 미등재 - 건물 윤곽은 있으나 건축물대장이 없는 건물 위 구조물 필지",
                            note=f"{cfg['project']['official_name']} · 건물통합정보 대장 연계 여부 기준 · 보고서 {out.name}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    print(generate(load_project(ap.parse_args().project)))


if __name__ == "__main__":
    main()
