"""2단계: 규칙 기반 판독

필지·시점마다 편입 부분 영상을 0.5m 격자로 읽어 비슷한 색·밝기의 조각으로 나누고(Felzenszwalb 분할),
조각마다 판독 기준표의 항목을 수치로 계산해 규칙으로 '있음 / 없음 / 불확실'을 정합니다.

판독 기준표 → 수치 규칙
- 지붕면: 식생이 아니고(ExG ≤ 0.03), 밝기가 균일하며(L 표준편차 ≤ 4), 주변과 밝기 차이가 남(대비 ≥ 10)
- 직선 윤곽: 최소 외접 사각형 대비 면적 비율 0.65~1.3, 볼록도(solidity) ≥ 0.75, 장단축비 ≤ 4 (선로·도로·화차처럼 긴 것 제외)
- 그림자: 조각 둘레 3m 띠 안에 조각보다 L이 25 이상 어두운 화소가 10% 이상, 또는 대비 ≥ 15
- 있음: 위 조건을 모두 갖추고 면적 30㎡ 이상, 편입 부분 안에 70% 이상 들어간 조각이 하나 이상
- 불확실: 지붕면·윤곽 조건은 갖췄지만 면적 12~30㎡이거나 경계에 걸쳤거나 그림자·대비가 약한 조각만 있음
- 없음: 해당 조각 없음 / 영상 없음: 편입 부분이 영상 범위 밖

기준값은 데모 판독 결과(interpretation.csv)에 맞춰 조정한 값이며 config.py의 RULE에서 바꿀 수 있습니다.

실행: python 02_interpret.py                         # 전체 판독
      python 02_interpret.py --compare interpretation.csv   # 판독 후 데모 판독 결과와 일치율 출력
출력: output/interpretation_rule.csv (필지·시점 판독), output/candidates.gpkg (판독 근거가 된 건물 후보 조각)
"""
import argparse
import cv2
import geopandas as gpd, numpy as np, pandas as pd, rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from scipy.ndimage import binary_dilation
from shapely.geometry import shape
from skimage.color import rgb2lab
from skimage.measure import regionprops
from skimage.segmentation import felzenszwalb
import config as C

R = C.RULE
EPOCHS = list(C.EPOCHS)


def segments(geom, ds):
    """편입 부분 주변 영상을 읽어 조각별 특징을 계산합니다."""
    res = R["res_m"]
    x0, y0, x1, y1 = geom.bounds
    b = (x0 - 6, y0 - 6, x1 + 6, y1 + 6)
    W, H = max(int((b[2] - b[0]) / res), 1), max(int((b[3] - b[1]) / res), 1)
    img = ds.read(window=from_bounds(*b, ds.transform), boundless=True, out_shape=(3, H, W),
                  resampling=Resampling.bilinear).astype(np.float32) / 255
    tr = rasterio.transform.from_bounds(*b, W, H)
    inside = ~geometry_mask([geom], (H, W), tr)
    rgb = img.transpose(1, 2, 0)
    L = rgb2lab(rgb)[..., 0]
    s3 = rgb.sum(axis=2) + 1e-6                      # 색좌표(chromatic coordinates) r, g, b
    exg = (2 * rgb[..., 1] - rgb[..., 0] - rgb[..., 2]) / s3   # ExG = 2g - r - b (Woebbecke et al., 1995)
    seg = felzenszwalb(rgb, scale=60, sigma=0.6, min_size=int(6 / res ** 2)) + 1
    ring_px = int(R["ring_m"] / res)
    out = []
    for r in regionprops(seg):
        area = r.area * res * res
        if area < 6 or area > R["area_max"]:
            continue
        m = seg == r.label
        fin = inside[m].mean()
        if fin < 0.3:
            continue
        ring = binary_dilation(m, iterations=ring_px) & ~m
        if not ring.any():
            continue
        lv, lr = L[m], L[ring]
        (_, _), (w, h), _ = cv2.minAreaRect(r.coords[:, ::-1].astype(np.float32))
        out.append(dict(
            area=area, fin=fin, Lstd=float(lv.std()), contrast=float(abs(lv.mean() - lr.mean())),
            shadow=float((lr < lv.mean() - R["shadow_dL"]).mean()), solidity=r.solidity,
            rect=r.area / max(w * h, 1), elong=max(w, h) / max(min(w, h), 1), exg=float(exg[m].mean()),
            mask=m, transform=tr))
    return out


def judge_segment(s):
    """조각 하나를 'strong'(건물), 'weak'(불확실 후보), None 으로 분류합니다."""
    roof = s["exg"] <= R["exg_max"] and s["Lstd"] <= R["lstd_max"] and s["contrast"] >= R["contrast_min"]
    shape_ok = (R["rect_lo"] <= s["rect"] <= R["rect_hi"] and s["solidity"] >= R["solidity_min"]
                and s["elong"] <= R["elong_max"] and s["fin"] >= 0.5)
    if not (roof and shape_ok and s["area"] >= R["area_min_weak"]):
        return None
    shadow = s["shadow"] >= R["shadow_frac"] or s["contrast"] >= R["contrast_strong"]
    if s["area"] >= R["area_min_strong"] and s["fin"] >= R["fin_strong"] and shadow:
        return "strong"
    return "weak"


def compare(ai_path, ref_path):
    """코드 판독과 기준 판독(데모)을 비교해 화면과 output/compare_result.txt에 기록합니다."""
    a = pd.read_csv(ai_path, dtype=str).set_index("hist_id")
    r = pd.read_csv(ref_path, dtype=str).set_index("hist_id")
    ids = a.index.intersection(r.index)
    cols = [f"struct_{k}" for k in EPOCHS]
    A = a.loc[ids, cols].values.ravel(); B = r.loc[ids, cols].values.ravel()
    has = lambda df: df[cols].eq("있음").any(axis=1)
    lines = [f"비교 필지 {len(ids)}개, 필지·시점 {len(A)}칸",
             f"전체 일치율 {(A == B).mean():.1%}",
             f"필지별 '구조물 있음' 여부 일치율 {(has(a.loc[ids]) == has(r.loc[ids])).mean():.1%}",
             "", "행: 기준 판독(interpretation.csv) / 열: 코드 판독",
             pd.crosstab(pd.Series(B, name="기준"), pd.Series(A, name="코드")).to_string()]
    text = "\n".join(lines)
    print(text)
    (C.WORK / "compare_result.txt").write_text(text + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--compare"); args = ap.parse_args()
    t = gpd.read_file(C.WORK / "targets.gpkg")
    ds = {k: rasterio.open(C.IMAGERY / tif) for k, (tif, _) in C.EPOCHS.items()}
    rows, cands = [], []
    for _, row in t.iterrows():
        marks = {}
        for k in EPOCHS:
            if float(row[f"valid_{k}"]) <= C.MIN_VALID_RATIO:
                marks[k] = "영상 없음"; continue
            found = []
            for s in segments(row.geometry, ds[k]):
                c = judge_segment(s)
                if c:
                    found.append(c)
                    for g, v in shapes(s["mask"].astype(np.uint8), mask=s["mask"], transform=s["transform"]):
                        cands.append(dict(hist_id=row.hist_id, epoch=k, grade=c, area_m2=round(s["area"], 1),
                                          contrast=round(s["contrast"], 1), shadow=round(s["shadow"], 2), geometry=shape(g)))
            marks[k] = "있음" if "strong" in found else ("불확실" if found else "없음")
        rows.append([row.hist_id, row.pnu, row.address, row.jimok, row.taken_m2] + [marks[k] for k in EPOCHS])
        print(row.hist_id, " ".join(marks[k] for k in EPOCHS))
    out = pd.DataFrame(rows, columns=["hist_id", "pnu", "address", "jimok", "taken_m2"] + [f"struct_{k}" for k in EPOCHS])
    out.to_csv(C.WORK / "interpretation_rule.csv", index=False, encoding="utf-8-sig")
    if cands:
        gpd.GeoDataFrame(cands, crs=C.CRS).to_file(C.WORK / "candidates.gpkg", layer="candidates")
    if args.compare:
        compare(C.WORK / "interpretation_rule.csv", args.compare)


if __name__ == "__main__":
    main()
