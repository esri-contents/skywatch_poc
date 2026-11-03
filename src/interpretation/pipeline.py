"""필지 단위 구조물 판독 실행기.

  python -m src.interpretation.pipeline --project config/projects/gwangmyeong_siheung_aerial.yaml

단계: 1) 타일 분할·조각 특징(tiling) 2) 공간 교차검증 분류기(classifier) 3) 확률 래스터,
신규·소멸 객체, 필지 판정(parcels) 4) 근거 영상 조각(evidence) 5) 요약 JSON.
1)·2)는 결과가 있으면 재사용한다(--resegment, --refit으로 다시 실행).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from ..data.project_acquisition import load_project
from . import classifier, evidence, parcels, qa, reference, tiling


def run(project_config: str, resegment: bool = False, refit: bool = False, evidence_n: int = 40) -> dict:
    cfg = load_project(project_config)
    ic = cfg["interpretation"]
    out = Path(ic["out_dir"])
    epochs = {e["id"]: e for e in cfg["imagery"]["epochs"]}
    t1, t2 = ic["epochs"]
    images = {e: epochs[e]["path"] for e in (t1, t2)}
    dates = {e: str(epochs[e]["date"]) for e in (t1, t2)}
    crs = cfg["project"]["analysis_crs"]
    seg_dir = out / "segments"

    if resegment or not all((seg_dir / f"segments_{e}.parquet").exists() for e in images):
        print("1) 타일 분할")
        tiling.run(images, cfg["paths"]["aoi"], cfg["paths"]["buildings"], seg_dir, ic, crs=crs)

    (out / "reports").mkdir(parents=True, exist_ok=True)
    prob_path, metrics_path = seg_dir / "segment_prob.parquet", out / "reports" / "classifier_metrics.json"
    if refit or not (prob_path.exists() and metrics_path.exists()):
        print("2) 분류기(공간 블록 교차검증)")
        frames = {e: pd.read_parquet(seg_dir / f"segments_{e}.parquet") for e in images}
        d = classifier.prepare(frames, ic["classifier"]["block_m"])
        prob, metrics = classifier.fit_oof(d, ic["classifier"]["folds"])
        if ic["classifier"].get("auto_context"):
            print("   2단계 auto-context")
            adj = {e: classifier.adjacency(seg_dir / f"segments_{e}.tif") for e in images}
            stage1 = {k: metrics[k] for k in ("auc", "average_precision")}
            prob, metrics = classifier.fit_oof_context(d, prob, adj, ic["classifier"]["folds"])
            metrics["stage1"] = stage1
        d["prob"] = prob
        classifier.threshold_table(d, prob).to_csv(out / "reports" / "threshold_table.csv", index=False, encoding="utf-8-sig")
        d = d[["seg_id", "epoch", "x", "y", "area_m2", "fp_frac", "fp_near", "target", "block", "prob"]]
        d.to_parquet(prob_path, index=False)
        metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print("2) 분류기 결과 재사용(--refit으로 다시 학습)")
        d = pd.read_parquet(prob_path)
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))

    print("3) 확률 래스터·객체·필지")
    rc = ic["rules"]
    res = ic["resolution_m"]
    probs, profile = {}, None
    shadow = None
    (out / "rasters").mkdir(parents=True, exist_ok=True)
    for e in images:
        seg_path = seg_dir / f"segments_{e}.tif"
        s = d[d["epoch"] == e]
        probs[e], profile = parcels.paint(seg_path, s["seg_id"].to_numpy(), s["prob"].to_numpy())
        parcels.write_prob(out / "rasters" / f"structure_prob_{e}.tif", probs[e], profile)
        f = pd.read_parquet(seg_dir / f"segments_{e}.parquet", columns=["seg_id", "L"])
        L, _ = parcels.paint(seg_path, f["seg_id"].to_numpy(), f["L"].to_numpy())
        dark = np.nan_to_num(L, nan=100) < ic["segmentation"]["shadow_l_max"]
        shadow = dark if shadow is None else shadow | dark
    change = Path(cfg["paths"]["output_root"]) / "comparisons" / f"{t1}_{t2}" / "rasters" / "change_probability.tif"
    change_prob = parcels._resample_to(change, profile) if change.exists() else None
    bld = gpd.read_file(cfg["paths"]["buildings"]).to_crs(crs)
    bld = bld[bld.area > 0]
    masks = parcels.footprint_masks(bld, profile, rc["tolerance_m"])
    objs = pd.concat([
        parcels.change_objects(probs[t1], probs[t2], rc, res, profile["transform"], crs, change_prob, "new", shadow),
        parcels.change_objects(probs[t2], probs[t1], rc, res, profile["transform"], crs, change_prob, "gone", shadow),
        parcels.unregistered_objects(probs[t1], masks[1], rc, res, profile["transform"], crs, fp=masks[0]),
    ], ignore_index=True)
    objs = gpd.GeoDataFrame(objs, geometry="geometry", crs=crs)
    cad_all = gpd.read_file(cfg["paths"]["cadastre"]).to_crs(crs)
    cad = parcels.drop_aggregate_parcels(cad_all)
    print(f"  지적 {len(cad_all):,}개 중 상위(중복) 폴리곤 {len(cad_all) - len(cad):,}개 제외")
    table, objs = parcels.parcel_table(cad, probs, profile, res, bld, objs, rc, t1, t2, masks)
    metrics["footprint_recall"] = {e: parcels.footprint_recall(bld, probs[e], profile, rc["prob_hi"]) for e in images}
    (out / "vectors").mkdir(parents=True, exist_ok=True)
    table.to_file(out / "vectors" / "parcel_interpretation.gpkg", layer="parcels")
    objs.to_file(out / "vectors" / "structure_change_objects.gpkg", layer="objects")
    table.drop(columns="geometry").to_csv(out / "reports" / "parcel_interpretation.csv", index=False, encoding="utf-8-sig")

    print("4) 근거 영상 조각")
    ev = out / "evidence" / "new_A"
    for old in ev.glob("new_A_*.jpg"):  # 이전 실행의 객체 번호와 섞이지 않게
        old.unlink()
    new = objs[(objs["kind"] == "new") & (objs["tier"] == "A")].sort_values("area_m2", ascending=False)
    for i, (_, o) in enumerate(new.head(evidence_n).iterrows()):
        parcel = table.geometry.iloc[int(o["parcel_idx"])] if pd.notna(o.get("parcel_idx")) else None
        evidence.chip_pair(images, dates, o.geometry, [o.geometry], ev / f"new_A_{i:03d}.jpg",
                           title=f"A{i + 1:02d}  {o['area_m2']:.0f}m2  PNU {o.get('pnu', '')}", parcel=parcel)

    print("5) 검수 표본")
    qc = ic["qa"]
    if qc.get("kind") == "parcel":
        qa.build_parcel_round(out, images, dates, table, objs, t1, qc["round"], qc.get("per_group", 8), qc.get("per_linear", 20),
                              parts=tuple(qc.get("parts", ["unreg", "linear", "seen", "gone", "new"])),
                              per_attached=qc.get("per_attached", 20))
    else:
        qa.build_sample(out, images, dates, objs, d, seg_dir, t1, rc["prob_hi"], qc["round"], n=qc["per_stratum"])
    qa.export_for_human(out / "qa")

    print("6) 행정자료 기준 신규 재현율")
    start, end = ic["reference_window"]
    events = reference.construction_events(cfg["paths"].get("building_register"), cfg["paths"].get("building_permits"), start, end)
    recall = reference.new_structure_recall(table.drop(columns="geometry"), events)

    qa_rounds = {p.parent.name: qa.summarize(p).to_dict("records") for p in sorted((out / "qa").glob("*/qa_labels.csv"))}
    summary = {
        "qa": qa_rounds,
        "new_recall_reference": recall,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "epochs": dates, "classifier": metrics, "rules": rc,
        "segments": {e: int((d["epoch"] == e).sum()) for e in images},
        "parcels": int(len(table)),
        "status_t1": table[f"status_{t1}"].value_counts().to_dict(),
        "change": table["change"].value_counts().to_dict(),
        "unregistered_parcels": int(table["unregistered_flag"].sum()),
        "objects": {f"{k}_{t}": v for (k, t), v in objs.groupby(["kind", "tier"]).agg(
            n=("area_m2", "size"), m2=("area_m2", "sum")).round(1).to_dict("index").items()},
    }
    (out / "reports" / "interpretation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True)
    ap.add_argument("--resegment", action="store_true")
    ap.add_argument("--refit", action="store_true")
    args = ap.parse_args()
    print(json.dumps(run(args.project, args.resegment, args.refit or args.resegment), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
