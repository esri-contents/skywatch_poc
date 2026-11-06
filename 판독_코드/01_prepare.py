"""1단계: 판독 대상 추출과 판독 자료 준비

- 필지와 공단 편입 용지를 겹쳐 편입 부분(필지 ∩ 편입 용지)을 계산합니다.
- 편입 면적 30㎡ 이상 필지를 판독 대상으로 정하고, 북쪽부터 M001, M002 ... 번호를 붙입니다.
- Wayback 메타데이터와 공간 결합해 필지·시점마다 실제 촬영일을 붙입니다.
- 편입 부분 경계(빨강)와 필지 경계(흰색)를 겹친 시점별 영상 조각과, 8필지씩 묶은 판독 시트를 만듭니다.
- 판독 결과를 적을 빈 양식(interpretation_template.csv)을 만듭니다.

출력: output/targets.gpkg, output/taking_boundary.gpkg, output/chips/*.jpg, output/sheets/*.jpg, output/interpretation_template.csv
"""
import json
import geopandas as gpd, numpy as np, pandas as pd, rasterio
from rasterio.mask import mask
from rasterio.windows import from_bounds
from rasterio.enums import Resampling
from PIL import Image, ImageDraw, ImageFont
import config as C

C.WORK.mkdir(exist_ok=True); (C.WORK / "chips").mkdir(exist_ok=True); (C.WORK / "sheets").mkdir(exist_ok=True)

# 1. 편입 부분 계산
parcels = gpd.read_file(C.PARCELS).to_crs(C.CRS)
yongji_all = gpd.read_file(C.YONGJI).to_crs(C.CRS)
yongji_sec = yongji_all[yongji_all.section == C.YONGJI_SECTION]
aoi = parcels.union_all().convex_hull
yongji = yongji_sec.intersection(aoi)
yongji = yongji[~yongji.is_empty].union_all()          # 분석 범위 안의 편입 용지 (하나의 면)

parcels["part"] = parcels.intersection(yongji)
parcels["taken_m2"] = parcels["part"].area
t = parcels[parcels.taken_m2 >= C.MIN_TAKEN_M2].copy()
t["cy"] = t["part"].centroid.y
t = t.sort_values("cy", ascending=False).reset_index(drop=True)
t["hist_id"] = [f"M{i + 1:03d}" for i in range(len(t))]

# 2. 촬영일 결합 (편입 부분 대표점이 들어가는 메타데이터 폴리곤의 SRC_DATE2)
pts = gpd.GeoDataFrame(geometry=t["part"].representative_point(), crs=C.CRS)
for k, (_, meta) in C.EPOCHS.items():
    m = gpd.read_file(C.IMAGERY / meta)
    m = (m.set_crs(4326) if m.crs is None else m).to_crs(C.CRS)
    layer = sorted(m.metadata_layer.unique())[0]           # 가장 상세한 메타데이터 레벨
    m = m[m.metadata_layer == layer][["SRC_DATE2", "geometry"]]
    j = gpd.sjoin(pts, m, how="left", predicate="within")
    j = j[~j.index.duplicated()]
    t[f"img_{k}_date"] = pd.to_datetime(j["SRC_DATE2"], errors="coerce").dt.strftime("%Y-%m-%d").values

# 3. 영상 유효 비율 (편입 부분 안에서 영상 화소가 있는 비율)
for k, (tif, _) in C.EPOCHS.items():
    ratios = []
    with rasterio.open(C.IMAGERY / tif) as d:
        for g in t["part"]:
            try:
                a, _ = mask(d, [g], crop=True, filled=False, all_touched=True)
                inside = ~a.mask[0]
                ok = (a.data.sum(0) > 0) & inside
                ratios.append(round(ok.sum() / max(inside.sum(), 1), 2))
            except ValueError:
                ratios.append(0.0)
    t[f"valid_{k}"] = ratios

# 4. 영상 조각과 판독 시트
def rings(g):
    for p in ([g] if g.geom_type == "Polygon" else [q for q in getattr(g, "geoms", []) if q.geom_type == "Polygon"]):
        yield list(p.exterior.coords)

ds = {k: rasterio.open(C.IMAGERY / tif) for k, (tif, _) in C.EPOCHS.items()}
S = C.CHIP_PX
for _, r in t.iterrows():
    x0, y0, x1, y1 = r["part"].bounds
    s = max(x1 - x0, y1 - y0, 30) / 2 + C.CHIP_PAD_M
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    b = (cx - s, cy - s, cx + s, cy + s)
    tf = lambda c: ((c[0] - b[0]) / (b[2] - b[0]) * S, (b[3] - c[1]) / (b[3] - b[1]) * S)
    for k, d in ds.items():
        a = d.read(window=from_bounds(*b, d.transform), boundless=True, out_shape=(3, S, S), resampling=Resampling.bilinear)
        im = Image.fromarray(a.transpose(1, 2, 0)).convert("RGB"); dr = ImageDraw.Draw(im)
        for ring in rings(r.geometry): dr.line([tf(c) for c in ring], fill=(255, 255, 255), width=1)
        for ring in rings(r["part"]): dr.line([tf(c) for c in ring], fill=(255, 40, 40), width=2)
        im.save(C.WORK / "chips" / f"{r.hist_id}_{k}.jpg", quality=85)

font = ImageFont.load_default()
for si in range(0, len(t), 8):
    rows = t.iloc[si:si + 8]
    sh = Image.new("RGB", (4 * S + 110, len(rows) * S), "white"); dr = ImageDraw.Draw(sh)
    for j, (_, r) in enumerate(rows.iterrows()):
        dr.text((4, j * S + 4), f"{r.hist_id}\n{int(r.taken_m2)}m2", fill="black", font=font)
        for c, k in enumerate(C.EPOCHS):
            sh.paste(Image.open(C.WORK / "chips" / f"{r.hist_id}_{k}.jpg"), (110 + c * S, j * S))
    sh.save(C.WORK / "sheets" / f"sheet_{si // 8:02d}.jpg", quality=85)

# 5. 저장
keep = ["hist_id", "pnu", "ri", "jibun", "address", "jimok", "parcel_m2", "taken_m2"] + \
       [f"img_{k}_date" for k in C.EPOCHS] + [f"valid_{k}" for k in C.EPOCHS]
t["parcel_m2"] = t.geometry.area.round(1)
t["taken_m2"] = t["taken_m2"].round(1)
out = gpd.GeoDataFrame(t[keep], geometry=t["part"].values, crs=C.CRS)
out.to_file(C.WORK / "targets.gpkg", layer="targets")
tmpl = t[["hist_id", "pnu", "address", "jimok"]].copy()
for k in C.EPOCHS: tmpl[f"struct_{k}"] = ""
tmpl["note"] = ""
tmpl.to_csv(C.WORK / "interpretation_template.csv", index=False, encoding="utf-8-sig")
gpd.GeoDataFrame(geometry=[yongji], crs=C.CRS).to_file(C.WORK / "taking_boundary.gpkg")
print(f"판독 대상 {len(t)}필지, 영상 조각 {len(t) * len(C.EPOCHS)}장, 판독 시트 {(len(t) + 7) // 8}장")
