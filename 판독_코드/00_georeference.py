"""0단계: Wayback 다운로드 JPG를 EPSG:5186 GeoTIFF로 변환

input/wayback_raw/*.jpg (EPSG:3857, 줌 17 또는 18) → input/wayback/*_5186.tif
줌 17은 1m, 줌 18은 0.5m 격자로 재투영합니다(쌍선형 보간).
"""
import glob, os
import numpy as np, rasterio
from rasterio.transform import Affine, array_bounds
from rasterio.warp import reproject, Resampling, calculate_default_transform
from PIL import Image
import config as C

Image.MAX_IMAGE_PIXELS = None
C.IMAGERY.mkdir(parents=True, exist_ok=True)
for f in sorted(glob.glob(str(C.RAW_JPG / "*.jpg"))):
    name = os.path.basename(f)[:-4]
    zoom = name[-3:]
    px, x, y = C.JPG_GEOREF[zoom]
    a = np.array(Image.open(f).convert("RGB")).transpose(2, 0, 1)
    src_t = Affine(px, 0, x - px / 2, 0, -px, y + px / 2)
    res = 1.0 if zoom == "z17" else 0.5
    t, w, h = calculate_default_transform("EPSG:3857", "EPSG:5186", a.shape[2], a.shape[1],
                                          *array_bounds(a.shape[1], a.shape[2], src_t), resolution=res)
    out = np.zeros((3, h, w), np.uint8)
    for b in range(3):
        reproject(a[b], out[b], src_transform=src_t, src_crs="EPSG:3857", dst_transform=t, dst_crs="EPSG:5186",
                  resampling=Resampling.bilinear)
    with rasterio.open(C.IMAGERY / f"{name}_5186.tif", "w", driver="GTiff", width=w, height=h, count=3, dtype="uint8",
                       crs="EPSG:5186", transform=t, compress="deflate", tiled=True) as d:
        d.write(out)
    print(f"{name}: {a.shape[2]}x{a.shape[1]} → {w}x{h} ({res}m)")
