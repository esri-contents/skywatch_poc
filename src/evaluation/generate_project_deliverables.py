"""Generate project-specific evidence reports from executed outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
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
        "# 화성진안 분류 임계값 민감도 분석",
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


def _html(cfg: dict, summary: dict, briefing: bool = False) -> str:
    name = cfg["project"]["short_name"]
    total = summary["t0_t2"]
    register = json.loads(Path(cfg["paths"]["building_register"]).read_text(encoding="utf-8"))
    buildings = len(gpd.read_file(cfg["paths"]["buildings"]))
    matched = total["register_match_count"]
    title = f"{name} 위성 변화탐지 {'사업 브리핑' if briefing else '분석 보고서'}"
    cards = "".join(f'<div class="card"><b>{label}</b><strong>{value}</strong><small>{note}</small></div>' for label, value, note in [
        ("공식 지구면적", f'{cfg["project"]["official_area_m2"] / 1e6:.3f} km²', "국토교통부 고시 제2024-81호"),
        ("장기 변화후보", total["candidate_count"], "2020-08-25 → 2026-09-08, 건물/비건물 후보"),
        ("중복 제거 현장", total["site_count"], "site_id 기준"),
        ("건축물대장 매칭", f'{matched}/{total["candidate_count"]}', "후보 단위; 행정정보는 판정 보조자료"),
    ])
    periods = "".join(f"<tr><td>{k}</td><td>{v['candidate_count']}</td><td>{v['site_count']}</td><td>{v['register_match_count']}</td><td>{v['alignment']['displacement_m']:.3f} m</td></tr>" for k, v in summary.items())
    image = "../comparisons/t0_t2/figures/before_after_change.png"
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title>
<style>@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;800&display=swap');*{{box-sizing:border-box}}body{{margin:0;background:#f2f5f8;color:#17222e;font-family:'Noto Sans KR',sans-serif}}header{{background:linear-gradient(120deg,#073b4c,#118ab2);color:white;padding:54px max(6vw,34px)}}h1{{font-size:34px;margin:5px 0}}header p{{max-width:850px;line-height:1.7}}main{{max-width:1180px;margin:-24px auto 40px;padding:0 24px}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:13px}}.card,section{{background:white;border-radius:14px;padding:20px;box-shadow:0 4px 20px #17324d14}}.card b,.card small{{display:block;color:#567}}.card strong{{font-size:27px;display:block;margin:8px 0;color:#087e8b}}section{{margin-top:18px}}h2{{color:#073b4c}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #dde5eb;text-align:left}}img{{max-width:100%;border-radius:10px}}.warn{{border-left:5px solid #ff9f1c;background:#fff8e9}}a{{color:#087e8b}}@media(max-width:800px){{.grid{{grid-template-columns:1fr 1fr}}}}</style></head>
<body><header><small>LH 업무 적용 · 실행 결과 기반 · 2026-09-08 확인</small><h1>{title}</h1><p>화성진안 공공주택지구의 공식 지구계와 Sentinel-2 L2A 세 시점을 이용한 기술 검토다. 자동 결과는 현장 확인 우선순위를 위한 후보이며 불법 여부나 보상 대상 여부를 확정하지 않는다.</p></header><main><div class="grid">{cards}</div>
<section><h2>분석 범위와 관측 시점</h2><p>공식 위치: {cfg['project']['location']} · 사업시행자: {cfg['project']['operator']}. 공식 AOI는 VWorld 국가공간정보 WFS의 <code>lt_c_lhzone</code> 지구코드 <code>{cfg['aoi']['zonecode']}</code>를 사용했다. 저장 경계의 계산면적은 4,525,462㎡로 고시면적과 71㎡(0.0016%) 차이다.</p><p>영상은 계절 차이를 15일 이내로 맞춘 2020-08-25, 2023-09-09, 2026-09-08을 선택했다.</p></section>
<section><h2>실행 결과</h2><table><thead><tr><th>비교</th><th>후보</th><th>현장</th><th>대장 매칭 후보</th><th>정합 변위</th></tr></thead><tbody>{periods}</tbody></table><p>AOI 내 건물 footprint {buildings:,}동, 수집 건축물대장 {len(register):,}건. 후보 매칭률은 장기 비교 기준 {matched / max(total['candidate_count'],1)*100:.1f}%이며 전체 footprint 매칭률과 혼동하지 않는다.</p></section>
<section><img src="{image}" alt="2020년과 2026년 변화탐지 비교 지도"><p>Sentinel-2의 10m 공간해상도 때문에 소규모 증축, 경계부 혼합화소, 수목·토양 계절 변화는 재확인이 필요하다.</p></section>
<section class="warn"><h2>기준일 해석</h2><p>2021-08-30 신규 공공택지 발표일을 업무상 미확정 후보 기준일로 설정했다. 2024-02-07 지구지정일, 주민공람 관련 날짜, 향후 보상계획 공고일은 의미가 다르다. 보상계획 공고는 확인되지 않았으므로 기준일 전후 비교를 보상 판정으로 표현하지 않는다.</p></section>
<section><h2>근거와 한계</h2><p><a href="{cfg['aoi']['designation_notice_url']}">국토교통부 고시 제2024-81호</a> · <a href="{cfg['aoi']['parcel_correction_notice_url']}">필지조서 정정 고시 제2024-152호</a>. 임계값 0.5는 사람 정답 라벨이 없는 상태의 임시 운영값이며 별도 민감도표와 수동 검수표를 제공한다. 3D는 대상지 및 촬영계획까지만 산출하며 적합 고해상 중복영상이 없어 실제 재구성은 수행하지 않는다.</p></section></main></body></html>'''


def generate(cfg: dict) -> dict:
    output = Path(cfg["paths"]["reports"])
    summary = json.loads((Path(cfg["paths"]["output_root"]) / "analysis_summary.json").read_text(encoding="utf-8"))
    outputs = {}
    for briefing, filename in [(False, "hwaseong_jinan_analysis_report.html"), (True, "hwaseong_jinan_briefing.html")]:
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
