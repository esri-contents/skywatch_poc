"""ArcGIS Pro Add-in(광명시흥 판독) 실행기 - 항공사진 두 장으로 판독 전 과정을 돌려 공공기관 보고서 HTML까지 만든다.

  python -u -m src.interpretation.addin_run --t0 <기준 영상> --t0-date 2022-12-01 --t2 <비교 영상> --t2-date 2024 --out <폴더>
  python -u -m src.interpretation.addin_run --out <폴더> --report-only     # 표본 검수 입력 후 보고서만 다시 만든다

지구 경계·지적·건물·대장·허가와 판독 규칙은 광명시흥 프로젝트 설정(config/projects/gwangmyeong_siheung_aerial.yaml)을
그대로 쓰고, 영상·산출물만 바꾼 설정을 <폴더>/project.yaml로 남긴다. 저장소의 기존 결과·검수 라벨은 건드리지 않는다.
Add-in은 표준출력의 "@@STEP i/n 설명"(진행), "@@RESULT {json}"(완료), "@@ERROR 메시지"(실패) 줄을 읽고,
나머지 줄은 로그 창에 그대로 보인다.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import yaml

from .deliver import NAME as SHP_NAME

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "config" / "projects" / "gwangmyeong_siheung_aerial.yaml"
STEPS = ["기준 시기 영상 정리", "비교 시기 영상 정리", "화소 변화탐지(신규·소멸 교차 확인)",
         "조각화·판독기 학습·필지 판정", "보고서 작성"]
REVIEW_ROUND = "review1"  # 새 영상 실행의 검수 표본 회차(저장소 round1~4와 섞이지 않게 이름을 달리 둔다)
MIN_FREE_GB = 3
_DATA_KEYS = ("aoi", "buildings", "cadastre", "building_register", "building_permits",
              "building_register_atch", "building_register_basis", "gis_buildings", "buildings_ref")


def _step(i: int, text: str, n: int | None = None) -> None:
    print(f"@@STEP {i}/{n or len(STEPS)} {text}", flush=True)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _abs(p: str | None) -> str | None:
    return str((ROOT / p).resolve()) if p and not Path(p).is_absolute() else p


def window_end(d: str) -> str:
    """'2024' → '2024-12-31', '2024-05' → '2024-05-31', 전체 날짜는 그대로."""
    import pandas as pd
    parts = str(d).split("-")
    if len(parts) == 1:
        return f"{parts[0]}-12-31"
    if len(parts) == 2:
        return (pd.Timestamp(f"{d}-01") + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
    return str(d)


def window_start(d: str) -> str:
    parts = str(d).split("-")
    return f"{d}-01-01" if len(parts) == 1 else f"{d}-01" if len(parts) == 2 else str(d)


def reuse(src: str, base: dict) -> dict | None:
    """저장소 설정의 영상(변환본 또는 원본)과 같은 파일이면 이미 정리된 GeoTIFF를 그대로 쓴다."""
    s = Path(src).resolve()
    for e in base["imagery"]["epochs"]:
        if any(e.get(k) and (ROOT / e[k]).resolve() == s for k in ("path", "original")):
            return {"path": _abs(e["path"]), "source": str(s), "state": "reused", "reused_epoch": e["id"],
                    "spec": e.get("spec", ""), "registration": None}
    return None


def derive_config(base: dict, out: Path, imgs: dict, dates: dict, change: bool) -> dict:
    """광명시흥 설정에서 영상·산출물 경로만 바꾼 실행 설정."""
    cfg = copy.deepcopy(base)
    for k in _DATA_KEYS:
        cfg["paths"][k] = _abs(cfg["paths"].get(k))
    cfg["paths"].update(data_root=str(out / "data"), output_root=str(out), reports=str(out / "reports"),
                        output_gdb=str(out / "arcgis" / "addin.gdb"), output_aprx=str(out / "arcgis" / "addin.aprx"))
    cfg["analysis"]["base_config"] = _abs(cfg["analysis"]["base_config"])
    cfg["analysis"]["comparison_pairs"] = [["t0", "t2"]] if change else []
    cfg["imagery"]["provider"] = "ArcGIS Pro Add-in 입력 영상"
    cfg["imagery"]["report_note"] = "영상은 ArcGIS Pro Add-in(광명시흥 판독)으로 입력·정리"
    cfg["imagery"]["epochs"] = [{"id": e, "date": str(dates[e]), "item_id": f"addin_{e}", "path": imgs[e]["path"],
                                 "original": imgs[e]["source"], "spec": imgs[e]["spec"]} for e in ("t0", "t2")]
    ic = cfg["interpretation"]
    ic["out_dir"] = str(out / "interpretation")
    ic["epochs"] = ["t0", "t2"]
    ic["reference_window"] = [window_start(dates["t0"]), window_end(dates["t2"])]
    ic.pop("report_notes", None)  # 저장소 영상의 표본 검수에서 관찰한 문장 - 새 영상에는 맞지 않는다
    ic["qa"] = {**ic["qa"], "round": REVIEW_ROUND, "kind": "parcel", "parts": ["unreg", "linear", "gone", "new"],
                "reviewer": "담당자"}
    return cfg


def file_hash(path: str | Path, memo: Path | None = None) -> str:
    """파일 내용 SHA-256. memo(JSON)에 (경로, 크기, 수정시각)별로 기억해 같은 파일은 다시 읽지 않는다."""
    p = Path(path).resolve()
    st = p.stat()
    sig = f"{p}|{st.st_size}|{st.st_mtime_ns}"
    seen = json.loads(memo.read_text(encoding="utf-8")) if memo and memo.exists() else {}
    if sig in seen:
        return seen[sig]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    seen[sig] = h.hexdigest()
    if memo:
        memo.parent.mkdir(parents=True, exist_ok=True)
        memo.write_text(json.dumps(seen, indent=1), encoding="utf-8")
    return seen[sig]


def analysis_key(img_hashes: list[str], cfg: dict, data_hashes: list[str], change: bool) -> str:
    """분할·판독기·화소 변화탐지 결과를 결정하는 입력의 해시(영상, 기초자료, 관련 설정)."""
    ic = cfg["interpretation"]
    parts = {"img": img_hashes, "data": data_hashes, "change": change, "analysis": cfg["analysis"] if change else None,
             "interp": {k: ic[k] for k in ("resolution_m", "tile_core_m", "tile_pad_m", "segmentation", "classifier")}}
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _link(src: Path, dst: Path) -> None:
    """하드링크(같은 디스크면 공간을 더 쓰지 않음), 안 되면 복사."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _cached_files(cfg: dict) -> dict[str, Path]:
    """캐시에 두는 분석 결과(캐시 안 상대 경로 → 실행 폴더의 경로)."""
    io = Path(cfg["interpretation"]["out_dir"])
    seg = io / "segments"
    files = {f"segments/{p}": seg / p for p in ("segments_t0.parquet", "segments_t0.tif", "segments_t2.parquet",
                                                 "segments_t2.tif", "segment_prob.parquet")}
    files.update({f"reports/{p}": io / "reports" / p for p in ("classifier_metrics.json", "threshold_table.csv")})
    files["change_probability.tif"] = Path(cfg["paths"]["output_root"]) / "comparisons" / "t0_t2" / "rasters" / "change_probability.tif"
    return files


def _followup(args) -> bool:
    """판독 결과 폴더에 대한 후속 명령(보고서·현장조사·도면·후보 판독)인지 - 영상 입력이 필요 없다."""
    return bool(args.report_only or args.survey_export or args.survey_import or args.sheets or args.screen_export)


def run(args) -> dict:
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cfg_path = out / "project.yaml"
    t_start = time.time()
    if _followup(args):
        from ..evaluation import generate_interpretation_official as official
        from . import survey
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        info_path = out / "run_info.json"
        info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
        prev = info.get("result") or {}
        html = Path(prev.get("report") or Path(cfg["interpretation"]["out_dir"]) / "reports" / f'{cfg["project"]["id"]}_parcel_interpretation_official.html')
        shp = prev.get("appendix1")
        res = {}
        if args.screen_export:
            from . import screen
            _step(1, "후보 판독표·근거 영상 작성", n=1)
            res = {"screen": str(screen.export(cfg, log=_log))}
        elif args.survey_export:
            _step(1, "현장조사표·조사용 지오데이터베이스 작성", n=1)
            sdir = survey.export(cfg, log=_log)
            res = {"survey_dir": str(sdir), "survey_gdb": str(sdir / "survey.gdb"), "survey_xlsx": str(sdir / "현장조사표.xlsx")}
        elif args.sheets:
            from . import sheets
            _step(1, "필지 도면 PDF 작성", n=1)
            pdf, pages = sheets.export(cfg, out / f"필지도면_붙임1_{datetime.now():%Y%m%d_%H%M%S}.pdf", log=_log)
            res = {"sheets_pdf": str(pdf), "sheets_pages": pages}
        else:
            if args.survey_import:
                _step(1, "현장조사 결과 읽기", n=2)
                survey.import_results(cfg, args.survey_import, log=_log)
            _step(2 if args.survey_import else 1, STEPS[4], n=2 if args.survey_import else 1)
            # 이전 보고서·붙임 1 SHP는 그대로 두고(열려 있거나 Pro가 잠갔을 수 있음) 생성 시각을 붙여 새로 쓴다
            stamp = f"_{datetime.now():%Y%m%d_%H%M%S}"
            html = official.generate(cfg, config_path=str(cfg_path), suffix=stamp)
            shp = str(html.parent / f"{SHP_NAME}{stamp}" / f"{SHP_NAME}{stamp}.shp")
            _log(f"   보고서 저장: {html}")
            _log(f"   붙임 1 SHP 저장: {shp}")
        # 처음 실행 때 알린 주의 사항은 다시 띄우지 않는다(실행 이력·로그에는 남아 있음)
        result = {**_result(cfg, out, html, info, shp), "warnings": [], **res}
        if info and not (args.sheets or args.survey_export or args.screen_export):  # 보고서를 새로 쓴 경우만
            info["result"] = {k: v for k, v in result.items() if k not in res}
            info.setdefault("rebuilt_reports", []).append(str(html))
            info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return result

    free_gb = shutil.disk_usage(out).free / 1e9
    if free_gb < MIN_FREE_GB:  # 실행 하나에 분할·변화탐지·근거 영상 등 약 1~2GB(새 영상이면 정리본 +2GB)를 쓴다
        raise RuntimeError(f"출력 드라이브 여유 공간이 {free_gb:.1f}GB뿐입니다 - 최소 {MIN_FREE_GB}GB가 필요합니다. "
                           "공간을 확보하거나 다른 드라이브를 출력 폴더로 지정하세요.")
    base = yaml.safe_load(PROJECT.read_text(encoding="utf-8"))
    crs = base["project"]["analysis_crs"]
    ref = _abs(base["imagery"]["epochs"][0]["path"])
    aoi = _abs(base["paths"]["aoi"])
    from . import ingest
    cache = None if args.no_cache else Path(args.cache).resolve() if args.cache else out.parent / "_cache"
    memo = cache / "file_hashes.json" if cache else None
    imgs, dates, hashes = {}, {"t0": args.t0_date, "t2": args.t2_date}, {}
    for i, (e, src) in enumerate([("t0", args.t0), ("t2", args.t2)], 1):
        _step(i, f"{STEPS[i - 1]} ({Path(src).name})")
        _log("   영상 해시 계산")
        hashes[e] = file_hash(src, memo)
        dst = out / "data" / "imagery" / f"{e}_0.25m.tif"
        hit = cache / "imagery" / hashes[e][:24] if cache else None
        imgs[e] = reuse(src, base) if not args.no_reuse else None
        if imgs[e]:
            _log(f"   저장소에 정리된 영상과 같은 파일 - {Path(imgs[e]['path']).name} 재사용")
        elif hit and (hit / "ingest.json").exists():
            imgs[e] = json.loads((hit / "ingest.json").read_text(encoding="utf-8"))
            _link(hit / "image.tif", dst)
            ingest.ensure_rgb_descriptions(dst)
            imgs[e].update(path=str(dst), source=str(Path(src).resolve()), cached=True)
            _log(f"   같은 영상(해시 {hashes[e][:12]})을 정리한 결과가 있어 재사용")
        else:
            imgs[e] = ingest.prepare(src, dst, ref, aoi, crs, pro=Path(args.pro_python) if args.pro_python else None, log=_log)
            if hit:
                _link(dst, hit / "image.tif")
                (hit / "ingest.json").write_text(json.dumps(imgs[e], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        imgs[e]["sha256"] = hashes[e]
    cfg = derive_config(base, out, imgs, dates, change=not args.no_change)
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    info = {"started": datetime.now().astimezone().isoformat(timespec="seconds"), "inputs": imgs, "dates": dates,
            "change_detection": None, "reused_analysis": False}
    akey = adir = None
    if cache:
        # 분할 특징의 건물 겹침(fp_frac)·판독기 정답이 기준 건물 레이어에서 오므로 그 해시를 키에 넣는다
        data = [file_hash(cfg["paths"][k], memo) for k in ("aoi", "buildings_ref" if cfg["paths"].get("buildings_ref") else "buildings", "cadastre")]
        akey = analysis_key([hashes["t0"], hashes["t2"]], cfg, data, not args.no_change)
        adir = cache / "analysis" / akey
    if adir and (adir / "done.json").exists():
        for rel, dst in _cached_files(cfg).items():
            if (adir / rel).exists():
                _link(adir / rel, dst)
        info["reused_analysis"] = True
        info["change_detection"] = json.loads((adir / "done.json").read_text(encoding="utf-8")).get("change_detection")

    _step(3, STEPS[2])
    if info["reused_analysis"]:
        _log(f"   같은 입력(키 {akey[:12]})의 분할·판독기·변화탐지 결과를 재사용")
    elif args.no_change:
        _log("   건너뜀(선택) - 신규·소멸은 교차 확인 없이 C 등급으로만 나온다")
    else:
        try:
            from .. import project_pipeline
            stacks = project_pipeline.preprocess(cfg)
            res = project_pipeline.analyze(cfg, stacks)
            info["change_detection"] = {k: {kk: v[kk] for kk in ("candidate_count", "alignment")} for k, v in res.items()}
        except Exception as e:  # noqa: BLE001 - 교차 확인이 실패해도 판독은 계속한다
            _log(f"   [경고] 화소 변화탐지 실패 - 교차 확인 없이 계속: {e}")
            info["change_detection"] = {"error": str(e)}

    _step(4, STEPS[3])
    from . import pipeline
    fresh = not info["reused_analysis"]
    summary = pipeline.run(str(cfg_path), resegment=fresh, refit=fresh)
    change_failed = isinstance(info["change_detection"], dict) and "error" in info["change_detection"]
    if adir and fresh and not change_failed:  # 교차 확인이 실패한 결과는 캐시하지 않는다(다음 실행에서 다시 시도)
        for rel, src in _cached_files(cfg).items():
            if src.exists():
                _link(src, adir / rel)
        (adir / "done.json").write_text(json.dumps({"key": akey, "change_detection": info["change_detection"],
                                                    "created": datetime.now().astimezone().isoformat(timespec="seconds")},
                                                   ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    _step(5, STEPS[4])
    from ..evaluation import generate_interpretation_official as official
    html = official.generate(cfg, config_path=str(cfg_path))
    info["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
    info["minutes"] = round((time.time() - t_start) / 60, 1)
    info["summary"] = {k: summary[k] for k in ("parcels", "status_t1", "unregistered_parcels", "objects")}
    info["counts"] = counts(cfg)
    result = _result(cfg, out, html, info)
    info["result"] = result
    (out / "run_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def counts(cfg: dict) -> dict:
    """실행 이력에 보일 주요 수치(보고서 요약과 같은 기준)."""
    import pandas as pd
    io = Path(cfg["interpretation"]["out_dir"])
    t = pd.read_csv(io / "reports" / "parcel_interpretation.csv", encoding="utf-8-sig", usecols=["unregistered_conf"])
    o = json.loads((io / "reports" / "interpretation_summary.json").read_text(encoding="utf-8"))["objects"]
    n = lambda k: int(o.get(k, {}).get("n", 0))
    return {"parcels": len(t), "unreg_independent": int((t["unregistered_conf"] == "독립").sum()),
            "unreg_attached": int((t["unregistered_conf"] == "부속 의심").sum()),
            "unreg_noreg": int((t["unregistered_conf"] == "대장 없음").sum()),
            "new_A": n("new_A"), "gone_AB": n("gone_A") + n("gone_B")}


def _result(cfg: dict, out: Path, html: Path, info: dict, shp: str | None = None) -> dict:
    io = Path(cfg["interpretation"]["out_dir"])
    return {"report": str(html), "out": str(out), "parcels": str(io / "vectors" / "parcel_interpretation.gpkg"),
            "objects": str(io / "vectors" / "structure_change_objects.gpkg"),
            "csv": str(io / "reports" / "parcel_interpretation.csv"), "qa": str(io / "qa" / REVIEW_ROUND / "qa_labels.csv"),
            "screen": str(io / "screen" / "screen_labels.csv"),
            "appendix1": shp or str(io / "reports" / SHP_NAME / f"{SHP_NAME}.shp"),
            "image_t0": cfg["imagery"]["epochs"][0]["path"], "image_t2": cfg["imagery"]["epochs"][1]["path"],
            "status_field": f"status_{cfg['interpretation']['epochs'][0]}", "minutes": info.get("minutes"),
            "warnings": warnings(info)}


def warnings(info: dict) -> list[str]:
    """Add-in이 완료 후 창으로 알릴 주의 사항(로그 한 줄로는 놓치기 쉬운 것)."""
    out = []
    cd = info.get("change_detection")
    if isinstance(cd, dict) and "error" in cd:
        out.append(f"화소 변화탐지 실패 - 신규·소멸 A·B 등급이 없습니다({cd['error'][:200]})")
    for e, v in (info.get("inputs") or {}).items():
        if (v.get("registration") or {}).get("warning"):
            out.append(f"{'기준' if e == 't0' else '비교'} 시기 영상이 기준 영상과 위치가 일관되게 맞지 않습니다 - 좌표계를 확인하세요")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--t0")
    ap.add_argument("--t0-date")
    ap.add_argument("--t2")
    ap.add_argument("--t2-date")
    ap.add_argument("--pro-python", help="ArcGIS Pro 파이썬(형식 변환용). 생략하면 레지스트리에서 찾는다")
    ap.add_argument("--no-change", action="store_true", help="화소 변화탐지(교차 확인) 생략")
    ap.add_argument("--no-reuse", action="store_true", help="저장소 영상과 같은 파일이어도 다시 정리")
    ap.add_argument("--cache", help="재사용 캐시 폴더(기본: --out의 상위 폴더/_cache)")
    ap.add_argument("--no-cache", action="store_true", help="캐시를 읽지도 쓰지도 않음")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--survey-export", action="store_true", help="현장조사표(xlsx)·survey.gdb 작성")
    ap.add_argument("--survey-import", help="채운 현장조사표(xlsx) 또는 Field Maps gdb를 읽어 보고서에 반영")
    ap.add_argument("--sheets", action="store_true", help="붙임 1 필지 도면 PDF(필지마다 A4 1쪽) 작성")
    ap.add_argument("--screen-export", action="store_true", help="후보 판독표(screen_labels.csv)·근거 영상 작성(이전 판독은 이어받음)")
    args = ap.parse_args()
    if not _followup(args) and not all([args.t0, args.t0_date, args.t2, args.t2_date]):
        ap.error("--t0, --t0-date, --t2, --t2-date가 필요합니다")
    os.chdir(ROOT)
    try:
        print("@@RESULT " + json.dumps(run(args), ensure_ascii=False), flush=True)
    except Exception as e:  # noqa: BLE001 - Add-in이 읽을 수 있게 한 줄로 남긴다
        traceback.print_exc()
        print(f"@@ERROR {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
