"""형식 변환 보조 스크립트 - ArcGIS Pro 파이썬(arcgispro-py3)으로 실행한다(osgeo.gdal만 사용).

  <Pro>\\bin\\Python\\envs\\arcgispro-py3\\python.exe -I gdal_convert.py <원본> <출력.tif> <목표 해상도 m>

분석 파이썬의 rasterio(pip 휠)에는 ECW·MrSID 드라이버가 없고, Pro 환경의 rasterio는 재투영 모듈이 깨져 있어
변환만 Pro의 GDAL에 맡긴다. 좌표·좌표계는 원본 그대로 두고(재투영 없음) 밴드를 RGB 8비트로 맞추며,
원본이 목표 해상도보다 촘촘하면 원본 피라미드를 써서 목표 해상도 근처로 줄인다(크기 비율로 줄이므로
좌표 단위가 도·피트여도 된다). 마지막 줄에 결과 JSON을 쓴다.
"""

import json
import math
import sys

from osgeo import gdal

gdal.UseExceptions()


def pixel_m(ds, gt):
    """화소 크기(m). 경위도면 중심 위도로 환산, 좌표계가 없으면 좌표 단위를 m로 본다."""
    px = math.hypot(gt[1], gt[4])
    srs = ds.GetSpatialRef()
    if srs is not None and srs.IsGeographic():
        lat = gt[3] + gt[5] * ds.RasterYSize / 2
        return px * 111320 * max(math.cos(math.radians(lat)), 0.1)
    if srs is not None and srs.IsProjected():
        return px * srs.GetLinearUnits()
    return px


def convert(src: str, dst: str, res: float) -> dict:
    ds = gdal.Open(src)
    gt = ds.GetGeoTransform(can_return_null=True)
    georef = gt is not None and tuple(gt) != (0, 1, 0, 0, 0, 1)
    bands = [1, 2, 3] if ds.RasterCount >= 3 else [1, 1, 1]
    opts = {"bandList": bands, "outputType": gdal.GDT_Byte, "resampleAlg": "average",
            "creationOptions": ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER", "NUM_THREADS=ALL_CPUS"]}
    if ds.GetRasterBand(1).DataType != gdal.GDT_Byte:
        st = [ds.GetRasterBand(b).ComputeStatistics(True) for b in sorted(set(bands))]
        opts["scaleParams"] = [[min(s[0] for s in st), max(s[1] for s in st), 1, 255]] * 3
    px = pixel_m(ds, gt) if georef else None
    f = res / px if px else 1.0  # 축소 배율(1보다 크면 줄인다)
    if not georef and max(ds.RasterXSize, ds.RasterYSize) > 60000:
        f = max(ds.RasterXSize, ds.RasterYSize) / 60000
    if f > 1.25:
        opts["width"], opts["height"] = max(1, int(ds.RasterXSize / f)), max(1, int(ds.RasterYSize / f))
    last = [-1]

    def cb(p, *_):
        if int(p * 10) != last[0]:
            last[0] = int(p * 10)
            print(f"   형식 변환 {last[0] * 10}%", flush=True)
        return 1

    gdal.Translate(dst, ds, callback=cb, **opts)
    return {"driver": ds.GetDriver().ShortName, "size": [ds.RasterXSize, ds.RasterYSize], "bands": ds.RasterCount,
            "dtype": gdal.GetDataTypeName(ds.GetRasterBand(1).DataType), "src_res": px, "georef": georef}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # -I로 실행하면 PYTHONIOENCODING이 무시된다
    sys.stderr.reconfigure(encoding="utf-8")
    print(json.dumps(convert(sys.argv[1], sys.argv[2], float(sys.argv[3]))), flush=True)
