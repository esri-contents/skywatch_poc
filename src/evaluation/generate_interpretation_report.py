"""필지 단위 구조물 판독 보고서(LH 보상 실무자용).

  python -m src.evaluation.generate_interpretation_report --project config/projects/gwangmyeong_siheung_aerial.yaml

src.interpretation.pipeline 산출물(interpretation_summary.json, parcel_interpretation.gpkg,
structure_change_objects.gpkg, qa/*/qa_labels.csv, evidence/)을 읽어 하나의 HTML로 만든다.
그림은 data URI로 넣어 reports 폴더만 전달해도 열린다. 같은 본문을 문서 껍데기 없이
<id>_parcel_interpretation_report.body.html로도 써서 웹 게시에 쓴다.

읽는 순서: 핵심 수치 → 쓰는 순서 → 2022 현황 → 미등재 → 신규·소멸 → 신뢰도 → 기준일·산출물.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

from ..data.project_acquisition import load_project
from ..interpretation.qa import stratified_precision, summarize
from .generate_project_deliverables import _embedded_image

# 필지 상태: (이름, CSS 토큰). 지도(matplotlib)는 같은 색의 밝은 테마 값을 쓴다.
_STATUS = [("있음(등재+판독)", "--st-both", "#1f6f78"), ("있음(등재만)", "--st-reg", "#8fb8bd"),
           ("있음(판독만)", "--st-seen", "#c8414b"), ("없음", "--st-none", "#d9dee2"), ("영상 부족", "--st-na", "#9aa3aa")]

_CSS = """
/* 측량도면 계열: 잉크 남색 + 지적도 청록 + 미등재 적색 + 신규 호박색. 본문 한 단, 표는 가로 스크롤 */
:root{
  --bg:#f5f6f4; --paper:#ffffff; --ink:#18232d; --muted:#5b6872; --line:#d8dee2; --soft:#eef1f2;
  --accent:#1f6f78; --red:#b8323c; --amber:#a86a00; --ok:#1f7a55;
  --st-both:#1f6f78; --st-reg:#8fb8bd; --st-seen:#c8414b; --st-none:#d9dee2; --st-na:#9aa3aa;
  --f-body:"IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#11171c; --paper:#182128; --ink:#e4e9ec; --muted:#9aa7b0; --line:#2c3943; --soft:#1e2a32;
  --accent:#5fb3bb; --red:#e2737a; --amber:#e0a43c; --ok:#5cc497;
  --st-both:#4ea3ab; --st-reg:#3f6a70; --st-seen:#e2737a; --st-none:#2c3943; --st-na:#5d6971; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#11171c; --paper:#182128; --ink:#e4e9ec; --muted:#9aa7b0; --line:#2c3943; --soft:#1e2a32;
  --accent:#5fb3bb; --red:#e2737a; --amber:#e0a43c; --ok:#5cc497;
  --st-both:#4ea3ab; --st-reg:#3f6a70; --st-seen:#e2737a; --st-none:#2c3943; --st-na:#5d6971; color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15.5px/1.7 var(--f-body);-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding-block:0 64px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
header.top{border-bottom:1px solid var(--line);background:var(--paper)}
header.top .wrap{padding-block:40px 28px}
.eyebrow{font-size:12.5px;letter-spacing:.08em;color:var(--muted);text-transform:uppercase}
h1{font-size:clamp(26px,4vw,36px);line-height:1.25;margin:6px 0 10px;text-wrap:balance;letter-spacing:-.01em}
.lede{max-width:66ch;color:var(--muted);margin:0}
.meta{display:flex;flex-wrap:wrap;gap:6px 22px;margin-top:18px;font-size:13.5px;color:var(--muted)}
.meta b{color:var(--ink);font-weight:600}
h2{font-size:21px;margin:0 0 6px;text-wrap:balance;letter-spacing:-.005em}
h3{font-size:16px;margin:26px 0 8px}
section{margin-top:44px}
section > p,.prose{max-width:70ch}
p{margin:8px 0}
.small{font-size:13px;color:var(--muted)}
.mono,td.pnu{font-family:var(--f-mono);font-size:13px;font-variant-numeric:tabular-nums}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);border-radius:10px;overflow:hidden;margin-top:28px}
.kpi{background:var(--paper);padding:16px 18px;min-width:0}
.kpi .k{font-size:12.5px;color:var(--muted)}
.kpi .v{font-size:28px;font-weight:700;line-height:1.2;margin:4px 0;font-variant-numeric:tabular-nums}
.kpi .v small{font-size:14px;font-weight:500;color:var(--muted);margin-left:2px}
.kpi .d{font-size:12.5px;color:var(--muted)}
.kpi.red .v{color:var(--red)} .kpi.amber .v{color:var(--amber)}
.notice{margin-top:20px;border:1px solid var(--line);border-left:4px solid var(--amber);background:var(--paper);border-radius:8px;padding:14px 18px}
.notice p{margin:4px 0;max-width:none}
.steps{list-style:none;padding:0;margin:14px 0 0;display:grid;gap:10px}
.steps li{display:grid;grid-template-columns:34px minmax(0,1fr) auto;gap:4px 14px;align-items:start;background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.steps .no{font-family:var(--f-mono);font-weight:600;color:var(--accent);font-size:15px;line-height:1.6}
.steps b{display:block}
.steps .why{font-size:13.5px;color:var(--muted)}
.pill{display:inline-block;white-space:nowrap;border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600;border:1px solid currentColor}
.p-ok{color:var(--ok)} .p-mid{color:var(--amber)} .p-low{color:var(--red)} .p-na{color:var(--muted)}
.tbl{overflow-x:auto;margin-top:12px;border:1px solid var(--line);border-radius:8px;background:var(--paper)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:9px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
tr:last-child td{border-bottom:0}
th{font-size:12.5px;font-weight:600;color:var(--muted);background:var(--soft);white-space:nowrap}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.split{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:24px;align-items:start;margin-top:14px}
.map{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:10px}
.map img{display:block;width:100%;border-radius:4px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--muted);margin-top:8px}
.sw{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.ring{display:inline-block;width:11px;height:11px;border-radius:50%;border:2px solid #ffb703;margin-right:5px;vertical-align:-2px}
.bars{display:grid;gap:7px;margin-top:4px}
.bar{display:grid;grid-template-columns:72px minmax(0,1fr) 92px;gap:10px;align-items:center;font-size:13px}
.bar .track{display:flex;height:14px;background:var(--soft);border-radius:3px;overflow:hidden}
.bar .track span{display:block;height:100%}
.bar .lab{color:var(--muted)} .bar .num{text-align:right;font-variant-numeric:tabular-nums;color:var(--muted)}
.chips{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:12px}
.chips figure{margin:0;background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:8px}
.chips img{display:block;width:100%;border-radius:4px}
.chips figcaption{font-size:12.5px;color:var(--muted);margin-top:6px}
.chips figcaption b{color:var(--ink)}
details{margin-top:14px;border:1px solid var(--line);border-radius:8px;background:var(--paper)}
summary{cursor:pointer;padding:12px 16px;font-weight:600}
details > div{padding:0 16px 14px}
details .tbl{border:0;margin-top:0}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
footer{margin-top:52px;padding-top:18px;border-top:1px solid var(--line);font-size:12.5px;color:var(--muted)}
@media (max-width:820px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.split{grid-template-columns:1fr}.chips{grid-template-columns:1fr}
  .steps li{grid-template-columns:28px minmax(0,1fr)}.steps li .pill{grid-column:2;justify-self:start}}
"""

_FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
          '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap">')


def _e(x) -> str:
    return html.escape(str(x)) if x is not None and x == x else "-"


def _dong(addr: pd.Series) -> pd.Series:
    return addr.str.extract(r"(?:광명시|시흥시)\s+(\S+동)")[0].fillna("미상")


def _overview_png(cfg: dict, parcels: gpd.GeoDataFrame, objs: gpd.GeoDataFrame, t1: str, out: Path) -> Path:
    """T1 영상(8m 축소) 위에 필지 상태와 신규 의심 A 위치를 그린 개요도."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(parcels.crs)
    x0, y0, x1, y1 = aoi.total_bounds
    path = {e["id"]: e["path"] for e in cfg["imagery"]["epochs"]}[t1]
    px = 8.0
    w, h = int((x1 - x0) / px), int((y1 - y0) / px)
    with rasterio.open(path) as ds:
        img = ds.read([1, 2, 3], window=from_bounds(x0, y0, x1, y1, ds.transform), out_shape=(3, h, w),
                      boundless=True, resampling=Resampling.average).transpose(1, 2, 0)
    fig, ax = plt.subplots(figsize=(6.0, 6.0 * h / w), dpi=170)
    ax.imshow(img, extent=(x0, x1, y0, y1), alpha=0.6)
    for status, _, color in _STATUS:
        sub = parcels[parcels[f"status_{t1}"] == status]
        if len(sub) and status != "없음":
            sub.plot(ax=ax, color=color, alpha=0.8, linewidth=0)
    aoi.boundary.plot(ax=ax, color="#18232d", linewidth=1.0)
    new = objs[(objs["kind"] == "new") & (objs["tier"] == "A")]
    if len(new):
        pts = new.geometry.centroid
        ax.scatter(pts.x, pts.y, s=44, facecolors="none", edgecolors="#ffb703", linewidths=1.8, zorder=5)
    ax.set_axis_off()
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", pad_inches=0.02, facecolor="white")
    plt.close(fig)
    return out


_VERDICT = {"structure": '<span class="pill p-ok">실제 신규</span>', "not_structure": '<span class="pill p-low">아님</span>',
            "uncertain": '<span class="pill p-mid">판단 불가</span>', None: '<span class="pill p-na">미검수</span>'}


def _qa_verdicts(qa_root: Path, objs: gpd.GeoDataFrame, tol_m: float = 1.0) -> list[str]:
    """검수 표본(신규 층)과 객체를 중심점 거리로 맞춰 판독 결과를 붙인다. 뒤 회차가 우선."""
    labels = []
    for p in sorted(qa_root.glob("*/qa_labels.csv")):
        q = pd.read_csv(p, encoding="utf-8-sig")
        labels.append(q[q["stratum"].str.startswith("new") & q["label"].notna()])
    if not labels:
        return [_VERDICT[None]] * len(objs)
    q = pd.concat(labels, ignore_index=True)
    c = objs.geometry.centroid
    out = []
    for x, y in zip(c.x, c.y):
        d = (q["x"] - x).pow(2).add((q["y"] - y).pow(2)).pow(0.5)
        out.append(_VERDICT.get(q.loc[d.idxmin(), "label"]) if d.min() <= tol_m else _VERDICT[None])
    return out


def _timing(baseline: str | None, d1: str) -> tuple[bool, int, str]:
    """잠정 기준일과 T1 영상의 선후. (T1이 기준일보다 뒤인가, 개월 차, 한 줄 설명)."""
    b, t = pd.to_datetime(baseline, errors="coerce"), pd.to_datetime(d1, errors="coerce")
    if pd.isna(b) or pd.isna(t):
        return False, 0, ""
    months = round((t - b).days / 30.44)
    if months <= 0:
        return False, months, f"{d1} 영상은 잠정 기준일 {baseline} 당시 현황에 해당한다."
    return True, months, (f"<b>단, {d1} 영상은 잠정 기준일 {baseline}(주민공람일)보다 {months}개월 뒤라 기준일 당시 현황이 아니다</b> - "
                          f"그 사이에 지은 구조물도 '있음'으로 잡힌다.")


_ROUNDS = {"round1": "1차 · 규칙 보정용", "round2": "2차 · 평가", "round3": "3차 · 필지 평가(부속 분리 규칙 보정에도 사용)",
           "round4": "4차 · 미등재 독립/부속 평가", "review1": "검수 1차"}
_STRATA = {"new_A": "신규 의심 A", "new_C": "신규 C(판독만)", "unregistered": "미등재(조각 단위)", "gone_AB": "소멸 의심(전수)",
           "unreg_linear": "미등재 · 도로·하천류 지목", "seen_linear": "판독만 · 도로·하천류 지목", "unreg_attached": "미등재 · 부속 의심",
           **{f"unreg_{g}": f"미등재 · 지목 {g}" for g in ["대", "전", "답", "목", "임", "기타"]}}


def _qa_rows(qa_root: Path) -> tuple[str, dict]:
    rows, stats = [], {}
    for p in sorted(qa_root.glob("*/qa_labels.csv")):
        for _, r in summarize(p).iterrows():
            stats[(p.parent.name, r["stratum"])] = r
            prec = (f'{r["precision"] * 100:.0f}% <span class="small">({r["ci_lo"] * 100:.0f}~{r["ci_hi"] * 100:.0f})</span>'
                    if r["precision"] == r["precision"] else "-")
            rows.append(f'<tr><td>{_ROUNDS.get(p.parent.name, p.parent.name)}</td><td>{_STRATA.get(r["stratum"], r["stratum"])}</td>'
                        f'<td class="n">{r["n"]}</td><td class="n">{r["구조물 맞음"]}</td><td class="n">{r["구조물 아님"]}</td>'
                        f'<td class="n">{r["판단 불가"]}</td><td class="n">{prec}</td></tr>')
    return "".join(rows), stats


def _pct(d: dict) -> str:
    return f'{d["precision"] * 100:.0f}%' if d else "-"


def _ci(d: dict) -> str:
    return f'{d["ci_lo"] * 100:.0f}~{d["ci_hi"] * 100:.0f}%' if d else ""


def _frac(r) -> str:
    return f'{int(r["구조물 맞음"])}/{int(r["구조물 맞음"] + r["구조물 아님"])}' if r is not None else "-"


def _figure(path: Path, caption: str) -> str:
    return f'<figure><img src="{_embedded_image(path, 900)}" alt="{_e(caption)}" loading="lazy"><figcaption>{caption}</figcaption></figure>'


def _body(cfg: dict) -> str:
    ic = cfg["interpretation"]
    out = Path(ic["out_dir"])
    t1, t2 = ic["epochs"]
    epochs = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    d1, d2 = str(epochs[t1]["date"]), str(epochs[t2]["date"])
    s = json.loads((out / "reports" / "interpretation_summary.json").read_text(encoding="utf-8"))
    parcels = gpd.read_file(out / "vectors" / "parcel_interpretation.gpkg")
    objs = gpd.read_file(out / "vectors" / "structure_change_objects.gpkg")
    clf, rc, base = s["classifier"], s["rules"], cfg["compensation"]
    n = len(parcels)
    st = parcels[f"status_{t1}"].value_counts()
    has = int(sum(st.get(k, 0) for k, _, _ in _STATUS[:3]))
    unreg = parcels[parcels["unregistered_flag"]]
    u_ind, u_att, u_low = (unreg[unreg["unregistered_conf"] == c] for c in ("독립", "부속 의심", "저신뢰"))
    new_a = objs[(objs["kind"] == "new") & (objs["tier"] == "A")].sort_values("area_m2", ascending=False).reset_index(drop=True)
    gone_ab = objs[(objs["kind"] == "gone") & objs["tier"].isin(["A", "B"])]
    qa_html, qa = _qa_rows(out / "qa")
    r3, r4 = out / "qa" / "round3" / "qa_labels.csv", out / "qa" / "round4" / "qa_labels.csv"
    p_loose = stratified_precision(r3, "unreg_", exclude=("unreg_linear",)) if r3.exists() else {}
    p_ind = stratified_precision(r4, "unreg_", exclude=("unreg_attached",)) if r4.exists() else {}
    p_att = stratified_precision(r4, "unreg_attached") if r4.exists() else {}
    a3, g3, l3 = qa.get(("round3", "new_A")), qa.get(("round3", "gone_AB")), qa.get(("round3", "unreg_linear"))
    rec = clf["footprint_recall"][t1]
    ref = s.get("new_recall_reference", {}).get("전체", {})
    window = " ~ ".join(ic.get("reference_window", ["", ""]))
    late, gap, _ = _timing(base.get("baseline_date"), d1)
    proj = cfg["project"]

    H = []
    # 머리말
    H.append(f'''<header class="top"><div class="wrap">
<div class="eyebrow">{_e(proj["official_name"])} · 보상 실무 참고자료</div>
<h1>{_e(proj["short_name"])} 필지 구조물 판독</h1>
<p class="lede">항공사진 두 시기로 지구 안 모든 필지의 구조물 유무를 판독하고, 건물 레이어에 없는 구조물과 두 시기 사이의 신규·소멸 의심을 골랐다.
물건조사·현장조사에서 먼저 볼 필지를 정하는 보조자료이며, 불법 여부나 보상 대상 여부를 판단하지 않는다.</p>
<div class="meta"><span>영상 <b>{d1}</b> · <b>{d2}</b> 항공 정사영상</span><span>판독 필지 <b>{n:,}</b></span>
<span>잠정 기준일 <b>{_e(base.get("baseline_date"))}</b> (미확정)</span><span>작성 <b>{date.today():%Y-%m-%d}</b></span></div>
</div></header><main class="wrap">''')

    # 핵심 수치
    H.append(f'''<div class="kpis">
<div class="kpi"><div class="k">{d1} 구조물 있음</div><div class="v">{has:,}<small>필지</small></div><div class="d">건물 레이어 또는 영상 판독 기준</div></div>
<div class="kpi red"><div class="k">미등재 구조물 · 독립</div><div class="v">{len(u_ind):,}<small>필지</small></div><div class="d">실제 미등재 구조물 {_pct(p_ind)} (표본)</div></div>
<div class="kpi amber"><div class="k">{d1[:4]}→{d2} 신규 의심</div><div class="v">{len(new_a)}<small>건</small></div><div class="d">실제 신규 {_frac(a3)} (표본)</div></div>
<div class="kpi"><div class="k">소멸 의심</div><div class="v">{len(gone_ab)}<small>건</small></div><div class="d">실제 철거 {_frac(g3)} (표본)</div></div>
</div>''')

    notice = ['<p><b>모든 정확도 수치는 AI가 원본 영상을 보고 단 검수 라벨로 계산한 잠정값이다.</b> 사람 재판독 전에는 목록을 고르는 데만 쓴다.</p>']
    if late:
        notice.append(f'<p><b>영상이 기준일보다 {gap}개월 뒤다.</b> 잠정 기준일 {base["baseline_date"]}(주민공람일) 이후 {d1}까지 지은 구조물도 '
                      f'"{d1} 있음"에 들어 있다. 기준일 당시 현황을 보려면 그 무렵 영상이 따로 필요하다.</p>')
    H.append(f'<div class="notice">{"".join(notice)}</div>')

    # 쓰는 순서
    H.append(f'''<section><h2>이 결과를 쓰는 순서</h2>
<p>정확도가 높은 목록부터 본다. 각 목록의 필지는 첨부 CSV·GPKG에 있고, 영상 확인용 조각 이미지가 함께 있다.</p>
<ol class="steps">
<li><span class="no">1</span><div><b>독립 미등재 구조물 {len(u_ind):,}필지를 물건조서와 대조</b>
<span class="why">건물 레이어에 없는 창고·비닐하우스·컨테이너·가설물. 표본 10건 중 약 {round(p_ind.get("precision", 0) * 10)}건이 실제 미등재 구조물이었다.</span></div>
<span class="pill p-ok">우선 사용</span></li>
<li><span class="no">2</span><div><b>신규 의심 {len(new_a)}건 · 소멸 의심 {len(gone_ab)}건을 영상으로 전수 확인</b>
<span class="why">실제 신축·철거만 골라 현장조사와 인허가 대조로 넘긴다. 정밀도가 낮고 놓치는 것도 많아 인허가 목록을 함께 본다.</span></div>
<span class="pill p-mid">확인 후 사용</span></li>
<li><span class="no">3</span><div><b>부속 의심 {len(u_att):,}필지에서 증축부만 골라 보기</b>
<span class="why">대부분 등재 건물 지붕의 가장자리다. 표본의 {_pct(p_att)}만 별도 구조물(증축·부속 건물)이었다.</span></div>
<span class="pill p-mid">선별 사용</span></li>
<li><span class="no">4</span><div><b>도로·하천류 지목 {len(u_low)}필지는 마지막에</b>
<span class="why">아스팔트·차량·도로 도색이 대부분이다(표본 {_frac(l3)}).</span></div>
<span class="pill p-low">참고만</span></li>
</ol>
<p class="small">"없음" 판정은 구조물이 없다는 근거가 아니다. 판독기는 건물 레이어 건물의 {rec["recall"] * 100:.0f}%만 잡고, 50㎡ 미만 건물은 대부분 놓친다.</p></section>''')

    # 2022 현황
    ov = _overview_png(cfg, parcels, objs, t1, out / "reports" / "figures" / "overview.png")
    legend = "".join(f'<span><i class="sw" style="background:var({tok})"></i>{k}</span>' for k, tok, _ in _STATUS if k != "없음")
    legend += '<span><i class="ring"></i>신규 의심</span>'
    dong = parcels.assign(dong=_dong(parcels["addr"].fillna("")))
    groups = []
    for name, g in dong.groupby("dong"):
        c = g[f"status_{t1}"].value_counts()
        groups.append((name, len(g), [(c.get(k, 0), tok) for k, tok, _ in _STATUS[:3]], int((g["unregistered_conf"] == "독립").sum())))
    groups = [g for g in groups if g[0] != "미상" and sum(v for v, _ in g[2]) > 0]  # 경계 자투리 동 제외
    groups.sort(key=lambda r: -sum(v for v, _ in r[2]))
    peak = max(sum(v for v, _ in r[2]) for r in groups) or 1
    bars = "".join(
        f'<div class="bar"><span class="lab">{_e(nm)}</span><span class="track">'
        + "".join(f'<span style="width:{v / peak * 100:.2f}%;background:var({tok})"></span>' for v, tok in segs if v)
        + f'</span><span class="num">{sum(v for v, _ in segs):,} / {tot:,}</span></div>'
        for nm, tot, segs, _ in groups)
    meaning = {"있음(등재+판독)": "건물 레이어와 영상 판독이 모두 구조물", "있음(등재만)": "레이어에는 있으나 판독이 놓침(대부분 판독 누락)",
               "있음(판독만)": "레이어에 없는데 영상에 구조물", "없음": "두 증거 모두 없음(판독 누락 가능)", "영상 부족": "지구 경계 자투리 필지"}
    srows = "".join(f'<tr><td><i class="sw" style="background:var({tok})"></i>{k}</td><td class="n">{st.get(k, 0):,}</td><td>{meaning[k]}</td></tr>'
                    for k, tok, _ in _STATUS)
    H.append(f'''<section><h2>{d1} 필지 현황</h2>
<p>영상 판독과 건물 레이어(VWorld 도로명주소 건물)를 필지별로 겹쳐 다섯 상태로 나눴다. 두 증거가 갈리는 필지가 확인 대상이다.</p>
<div class="split"><div class="map"><img src="{_embedded_image(ov, 1400)}" alt="필지 상태 개요도"><div class="legend">{legend}</div></div>
<div><div class="tbl"><table><thead><tr><th>상태</th><th class="n">필지</th><th>뜻</th></tr></thead><tbody>{srows}</tbody></table></div>
<h3>동별 구조물 필지</h3><div class="bars">{bars}</div><p class="small">막대 = 구조물 있는 필지(색은 상태), 숫자 = 구조물 필지 / 전체 필지.</p></div></div></section>''')

    # 미등재
    top = u_ind.sort_values("unregistered_m2", ascending=False).head(12)
    trs = "".join(f'<tr><td class="pnu">{_e(r.pnu)}</td><td>{_e(r.addr)}</td><td>{_e(r.jimok)}</td><td class="n">{int(r.unregistered_n)}</td>'
                  f'<td class="n">{r.unregistered_m2:,.0f}</td></tr>' for _, r in top.iterrows())
    examples = ""
    if r4.exists():
        q = pd.read_csv(r4, encoding="utf-8-sig")
        edge = q["note"].fillna("").str.startswith("[")
        ind, att = q[q["stratum"] != "unreg_attached"], q[q["stratum"] == "unreg_attached"]
        picks = [(ind[ind["label"] == "structure"].head(2), "독립 · 실제 미등재"), (att[edge.reindex(att.index)].head(1), "부속 의심 · 등재 지붕 가장자리"),
                 (ind[(ind["label"] == "not_structure") & ~edge.reindex(ind.index)].head(1), "독립 · 오탐")]
        examples = "".join(_figure(out / "qa" / "round4" / f"{sid}.jpg", f"<b>{tag}</b> · {_e(note)}")
                           for frame, tag in picks for sid, note in zip(frame["sample_id"], frame["note"]))
    H.append(f'''<section><h2>건물 레이어에 없는 구조물</h2>
<p>건물 레이어에서 1m 넘게 떨어진 곳에 {rc["min_object_m2"]}㎡ 이상, 사각형에 가까운 구조물 덩어리가 판독된 필지다.
덩어리가 지붕을 따라 등재 건물과 이어지면 레이어 외곽선보다 큰 지붕의 가장자리일 수 있어 "부속 의심"으로 따로 뒀다.</p>
<div class="tbl"><table><thead><tr><th>구분</th><th class="n">필지</th><th class="n">실제 미등재 구조물</th><th class="n">95% 구간</th><th>주된 오탐</th></tr></thead><tbody>
<tr><td><b>독립</b></td><td class="n">{len(u_ind):,}</td><td class="n">{_pct(p_ind)}</td><td class="n">{_ci(p_ind)}</td><td>포장면·주차장·수목·운동장</td></tr>
<tr><td>부속 의심</td><td class="n">{len(u_att):,}</td><td class="n">{_pct(p_att)}</td><td class="n">{_ci(p_att)}</td><td>등재 건물 지붕 가장자리</td></tr>
<tr><td>도로·하천류 지목</td><td class="n">{len(u_low)}</td><td class="n">{_frac(l3)}</td><td class="n"></td><td>아스팔트·차량·도로 도색</td></tr>
</tbody></table></div>
<p class="small">4차 필지 표본(지목별 층화, 등재 건물의 일부가 아닌 구조물만 정답). 도로·하천류는 3차 표본. 지붕 가장자리까지 정답으로 치면 {_pct(p_loose)}였다(3차).</p>
<h3>독립 미등재 · 면적 상위 12필지</h3>
<div class="tbl"><table><thead><tr><th>PNU</th><th>주소</th><th>지목</th><th class="n">덩어리</th><th class="n">면적㎡</th></tr></thead><tbody>{trs}</tbody></table></div>
<p class="small">전체 목록: <span class="mono">parcel_interpretation.csv</span>에서 <span class="mono">unregistered_conf = 독립</span>.</p>
<h3>검수 예시</h3><div class="chips">{examples}</div>
<p class="small">노란 선 = 판독된 덩어리, 흰 선 = 필지 경계.</p></section>''')

    # 신규·소멸
    verdicts = _qa_verdicts(out / "qa", new_a)
    reviewed = sum(v != _VERDICT[None] for v in verdicts)
    ntr = "".join(f'<tr><td class="mono">A{i + 1:02d}</td><td class="pnu">{_e(r.pnu)}</td><td class="n">{r.area_m2:,.0f}</td>'
                  f'<td class="n">{r.change_prob:.2f}</td><td>{verdicts[i]}</td></tr>' for i, r in new_a.iterrows())
    ev = sorted((out / "evidence" / "new_A").glob("new_A_*.jpg"))
    chips = "".join(_figure(p, f'<b>A{int(p.stem.rsplit("_", 1)[1]) + 1:02d}</b> · 왼쪽 {d1}, 오른쪽 {d2}') for p in ev[:6])
    H.append(f'''<section><h2>{d1[:4]}→{d2} 신규·소멸 의심</h2>
<p>{d1}에는 구조물이 아니고 {d2}에는 구조물인 덩어리 가운데, 기존 화소 변화탐지도 변화로 본 것만 남겼다(두 방법 일치, 그림자·불규칙 모양 제외). 소멸은 반대 방향이다.</p>
<div class="notice"><p><b>목록을 그대로 쓰지 않는다.</b> 표본에서 실제 신규는 {_frac(a3)}, 실제 철거는 {_frac(g3)}였다.
오탐은 사면 천막, 지붕 도색·피복 교체, 포장 공사, 주차 차량이었다. 행정자료상 {window}에 착공·사용승인된 지구 내 필지 {ref.get("parcels", "-")}곳 중 신규로 잡은 곳은 {ref.get("A_or_B", "-")}곳이다.
{d2} 영상은 촬영 월일을 몰라 신축 시점을 특정할 수 없다.</p></div>
<h3>신규 의심 {len(new_a)}건</h3>
<div class="tbl"><table><thead><tr><th>번호</th><th>PNU</th><th class="n">면적㎡</th><th class="n">변화확률</th><th>표본 검수</th></tr></thead><tbody>{ntr}</tbody></table></div>
<p class="small">{reviewed}건은 검수 표본과 위치가 같아 결과를 붙였다(AI 판독). "미검수"와 "실제 신규"·"판단 불가"를 영상으로 확인한다. 소멸 의심 {len(gone_ab)}건은 <span class="mono">structure_change_objects.gpkg</span>(kind = gone)에 있다.</p>
<h3>근거 영상 · 면적 상위 6건</h3><div class="chips">{chips}</div></section>''')

    # 신뢰도
    lr, by = clf["legacy_rule"], rec["by_size"]
    stage1 = f' (이웃 조각 정보를 쓰기 전 {clf["stage1"]["auc"]:.3f})' if clf.get("stage1") else ""
    H.append(f'''<section><h2>판독을 얼마나 믿을 수 있나</h2>
<div class="tbl"><table><thead><tr><th>지표</th><th class="n">값</th><th>뜻</th></tr></thead><tbody>
<tr><td>건물 재현율 ({d1})</td><td class="n">{rec["recall"] * 100:.0f}%</td><td>건물 레이어 건물 중 절반 이상이 구조물로 판독된 비율. 20~50㎡ {by["20-50㎡"] * 100:.0f}% · 50~100㎡ {by["50-100㎡"] * 100:.0f}% · 100~300㎡ {by["100-300㎡"] * 100:.0f}% · 300㎡ 이상 {by["300㎡+"] * 100:.0f}%</td></tr>
<tr><td>조각 판별력 (AUC)</td><td class="n">{clf["auc"]:.3f}</td><td>600m 블록 단위로 학습·평가 지역을 나눠 잰 값{stage1}</td></tr>
<tr><td>원래 판독_코드 규칙</td><td class="n">재현율 {lr["recall"] * 100:.1f}%</td><td>1m Wayback 영상에 맞춘 임계값은 이 항공사진에서 거의 작동하지 않아 학습형 판독기로 바꿨다</td></tr>
</tbody></table></div>
<details><summary>회차별 표본 검수 결과 (AI 판독)</summary><div>
<div class="tbl"><table><thead><tr><th>회차</th><th>층</th><th class="n">표본</th><th class="n">맞음</th><th class="n">아님</th><th class="n">불명</th><th class="n">정밀도(95%)</th></tr></thead><tbody>{qa_html}</tbody></table></div>
<p class="small">규칙을 고칠 때 본 표본은 평가에 다시 쓰지 않았다. 1·2차의 신규·미등재 수치는 이후 바뀐 판독기 이전 값이다. 사람 재판독 표: <span class="mono">qa/human_review.csv</span></p></div></details>
<details><summary>판독 방법</summary><div class="prose">
<p>원본 0.25m 영상을 0.5m 격자로 지구 전체를 한 번에 조각내고(Felzenszwalb 분할), 조각마다 색·밝기 균일성·형태·크기·방향별 그림자·질감을 계산했다.
건물 레이어를 약한 정답으로 학습한 분류기가 조각의 구조물 확률을 내고, 이웃 조각 확률을 더 쓰는 두 번째 단계로 큰 지붕이 여러 조각으로 갈리는 문제를 줄였다.
두 시기 비교는 확률이 확실히 낮다가 확실히 높아진 곳만 신규로 보고, 정합 오차 1m와 그림자를 뺐다. 중앙선 매포 구간용 판독_코드의 판독 요소를 이어받았고 임계값은 다시 학습했다.</p></div></details></section>''')

    # 기준일·확인 사항
    src_rows = "".join(f'<tr><td class="n">{_e(x.get("notice_date"))}</td><td>{_e(x["title"])}</td><td>{_e(x["notice_number"])}</td></tr>'
                       for x in cfg.get("official_sources", []))
    H.append(f'''<section><h2>기준일과 사업부서 확인 사항</h2>
<p>잠정 기준일은 광명시·시흥시 사업 연혁에 적힌 주민공람 및 의견청취일 <b>{_e(base.get("baseline_date"))}</b>이다(이전 잠정값 {_e(base.get("baseline_previous"))} 지구지정고시일).
공공주택지구 이주대책 기준은 원칙적으로 주민공람공고일이지만 공고 원문을 확인하지 못했다.</p>
<ol class="steps">
<li><span class="no">1</span><div><b>보상계획공고(2025-08-27, 2026-07-31) 원문의 이주대책기준일·권리산정기준일</b><span class="why">확정되면 설정의 기준일을 바꾸고 보고서를 다시 만든다.</span></div><span class="pill p-na">미확인</span></li>
<li><span class="no">2</span><div><b>{d2} 항공사진의 실제 촬영 월일</b><span class="why">신규 구조물이 언제 생겼는지 말하려면 필요하다.</span></div><span class="pill p-na">미확인</span></li>
<li><span class="no">3</span><div><b>기준일 무렵(2020 하반기~2021-02) 정사영상</b><span class="why">같은 판독을 돌리면 기준일 당시 현황과 이후 신축을 직접 가릴 수 있다.</span></div><span class="pill p-na">미확보</span></li>
</ol>
<details><summary>관련 고시·공고</summary><div><div class="tbl"><table><thead><tr><th class="n">일자</th><th>고시·공고</th><th>번호</th></tr></thead><tbody>{src_rows}</tbody></table></div></div></details></section>''')

    # 산출물
    H.append(f'''<footer><p><b>첨부 산출물</b> (<span class="mono">outputs/{proj["id"]}/interpretation/</span>) ·
필지 판정 <span class="mono">reports/parcel_interpretation.csv</span>, <span class="mono">vectors/parcel_interpretation.gpkg</span> ·
신규·소멸·미등재 덩어리 <span class="mono">vectors/structure_change_objects.gpkg</span> · 근거 영상 <span class="mono">evidence/new_A/</span> · 검수 표본 <span class="mono">qa/</span></p>
<p>영상: {d1} 항공 정사영상(원본 3.8cm ECW → 0.25m), {d2} 항공 정사영상(0.25m, {d1} 기준 정합 RMSE 0.42m). 지적: VWorld 연속지적도. 건물: VWorld 도로명주소 건물.
판독 설정 <span class="mono">config/projects/{proj["id"]}.yaml</span> · 보정 기록 <span class="mono">reports/interpretation_calibration.md</span></p></footer></main>''')

    title = f'{proj["short_name"]} 필지 구조물 판독'
    return (f'<title>{title}</title><meta name="claude:viewport-fit-sides" content="cover">{_FONTS}<style>{_CSS}</style>'
            + "".join(H))


def generate(cfg: dict) -> Path:
    reports = Path(cfg["interpretation"]["out_dir"]) / "reports"
    body = _body(cfg)
    (reports / f'{cfg["project"]["id"]}_parcel_interpretation_report.body.html').write_text(body, encoding="utf-8")
    out = reports / f'{cfg["project"]["id"]}_parcel_interpretation_report.html'
    out.write_text('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>'
                   + body + "</body></html>", encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    print(generate(load_project(ap.parse_args().project)))


if __name__ == "__main__":
    main()
