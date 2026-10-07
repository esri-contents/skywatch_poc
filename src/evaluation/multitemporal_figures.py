"""다중시점 Screening 결과 figure 6종 (Master AOI / Before-After / Intensity / Polygon / Hotspot / ROI).

모든 figure는 outputs/<project>/figures/ 에 저장된다(저장소 정책상 PNG는 비커밋).
배경 영상은 comparisons/<pair>/rasters/{t1,t2}_aligned.tif (분석 CRS, 동일 grid)를 쓴다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from rasterio.plot import plotting_extent  # noqa: E402

from .visualize import _read_true_color  # noqa: E402

matplotlib.rcParams["font.family"] = "Malgun Gothic"
matplotlib.rcParams["axes.unicode_minus"] = False

logger = logging.getLogger("multitemporal_figures")

DISCLAIMER = "Sentinel-2 10m 기반 change candidate - 지장물/보상대상 판정 아님 (고해상도 검증 필요)"


def _band_order(path: Path) -> list[str]:
    with rasterio.open(path) as src:
        return list(src.descriptions)


def _pair_dir(cfg: dict, pair: dict) -> Path:
    return Path(cfg["paths"]["output_root"]) / "comparisons" / pair["pair"] / "rasters"


def _span_pair(pairs: list[dict]) -> dict:
    """가장 긴 기간(첫 연도 -> 마지막 연도)을 덮는 쌍."""
    return max(pairs, key=lambda p: p["year_to"] - p["year_from"])


def _plot_aoi(ax, aoi: gpd.GeoDataFrame, color="yellow", lw=1.6) -> None:
    aoi.boundary.plot(ax=ax, color=color, linewidth=lw)


def _plot_railway(ax, railway: gpd.GeoDataFrame | None, extent=None) -> None:
    if railway is None or railway.empty:
        return
    rw = railway
    if extent is not None:
        rw = rw.cx[extent[0]:extent[1], extent[2]:extent[3]]
    if "railway" not in rw:
        return
    if "service" in rw:
        rw = rw[rw["service"].isna()]  # 야드/측선은 선형이 복잡해 그림에서 제외
    status = rw["railway"]
    rw[status == "rail"].plot(ax=ax, color="red", linewidth=1.4, label="철도(OSM, 비공식)")
    rw[status != "rail"].plot(ax=ax, color="white", linewidth=1.0, linestyle="--", label="폐선/기타(OSM)")


def _bg(ax, tif: Path, title: str):
    rgb, extent, _ = _read_true_color(tif, _band_order(tif))
    ax.imshow(rgb, extent=extent)
    ax.set_title(title, fontsize=11)
    ax.set_xlabel("X (EPSG:5186, m)")
    ax.set_ylabel("Y (EPSG:5186, m)")
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.tick_params(labelsize=7)
    return extent


def _footer(fig) -> None:
    fig.text(0.5, 0.005, DISCLAIMER, ha="center", fontsize=8, color="gray")


def make_all_figures(cfg: dict, pairs: list[dict], results: dict, records: dict[int, dict]) -> dict[str, Path]:
    out = Path(cfg["paths"]["output_root"]) / "figures"
    out.mkdir(parents=True, exist_ok=True)
    crs = cfg["project"]["analysis_crs"]
    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(crs)
    ri_path = Path(cfg["paths"].get("ri_boundaries", ""))
    ri = gpd.read_file(ri_path).to_crs(crs) if ri_path.is_file() else None
    rw_path = Path(cfg["paths"].get("railway", ""))
    railway = gpd.read_file(rw_path).to_crs(crs) if rw_path.is_file() else None

    span = _span_pair(pairs)
    d = _pair_dir(cfg, span)
    t1, t2 = d / "t1_aligned.tif", d / "t2_aligned.tif"
    y1, y2 = span["year_from"], span["year_to"]
    d1, d2 = records[y1]["date"], records[y2]["date"]
    polygons, hs, roi = results["polygons"], results["hotspots"], results["roi"]
    figs: dict[str, Path] = {}

    # Figure 1 - Master AOI overview
    fig, ax = plt.subplots(figsize=(8, 7))
    extent = _bg(ax, t2, f"Fig.1 Master AOI overview - Sentinel-2 {d2}")
    if ri is not None:
        ri.boundary.plot(ax=ax, color="white", linewidth=0.5, alpha=0.6)
    _plot_aoi(ax, aoi)
    _plot_railway(ax, railway, extent)
    area_km2 = aoi.geometry.union_all().area / 1e6
    ax.plot([], [], color="yellow", label=f"Master AOI {area_km2:.2f} km² (잠정, 행정경계)")
    ax.legend(loc="lower left", fontsize=8)
    _footer(fig)
    figs["fig1_aoi_overview"] = out / "fig1_aoi_overview.png"
    fig.savefig(figs["fig1_aoi_overview"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Figure 2 - Before / After
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5))
    _bg(axes[0], t1, f"Before: {d1}")
    _bg(axes[1], t2, f"After: {d2}")
    for ax in axes:
        _plot_aoi(ax, aoi, lw=1.0)
    fig.suptitle(f"Fig.2 Sentinel-2 Before/After ({y1} vs {y2})")
    _footer(fig)
    figs["fig2_before_after"] = out / "fig2_before_after.png"
    fig.savefig(figs["fig2_before_after"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Figure 3 - Change intensity
    fig, ax = plt.subplots(figsize=(8, 7))
    ipath = results.get("intensity_path")
    if ipath and Path(ipath).exists():
        with rasterio.open(ipath) as src:
            arr = np.ma.masked_equal(src.read(1), src.nodata)
            ext = plotting_extent(src)
        im = ax.imshow(arr, extent=ext, cmap="magma", vmin=0, vmax=1)
        fig.colorbar(im, ax=ax, shrink=0.8, label="change score (연도쌍 최대값, 0~1)")
        _plot_aoi(ax, aoi, color="cyan", lw=1.0)
    ax.set_title("Fig.3 Change intensity (change_probability 최대값)")
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.tick_params(labelsize=7)
    _footer(fig)
    figs["fig3_change_intensity"] = out / "fig3_change_intensity.png"
    fig.savefig(figs["fig3_change_intensity"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Figure 4 - Change polygon (연도쌍별 색)
    fig, ax = plt.subplots(figsize=(8, 7))
    _bg(ax, t2, f"Fig.4 Change polygons ({len(polygons)}건)")
    _plot_aoi(ax, aoi, lw=1.0)
    if len(polygons):
        cmap = plt.get_cmap("tab10")
        for i, (pair, grp) in enumerate(polygons.groupby("pair")):
            grp.plot(ax=ax, color=cmap(i % 10), alpha=0.65, edgecolor=cmap(i % 10), linewidth=0.5)
            ax.plot([], [], color=cmap(i % 10), linewidth=6, label=f"{pair} ({len(grp)})")
        ax.legend(loc="lower left", fontsize=8, title="연도쌍(polygon 수)")
    else:
        ax.text(0.5, 0.5, "Sentinel-2에서는 유의미한 변화가 검출되지 않음", transform=ax.transAxes,
                ha="center", color="white", fontsize=12, bbox=dict(facecolor="black", alpha=0.5))
    _footer(fig)
    figs["fig4_change_polygons"] = out / "fig4_change_polygons.png"
    fig.savefig(figs["fig4_change_polygons"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Figure 5 - Hotspot map
    fig, ax = plt.subplots(figsize=(8, 7))
    extent = _bg(ax, t2, f"Fig.5 Change hotspots ({len(hs)}건)")
    _plot_aoi(ax, aoi, lw=1.0)
    _plot_railway(ax, railway, extent)
    if len(hs):
        hs.boundary.plot(ax=ax, color="cyan", linewidth=1.4)
        hs[hs["recommended_for_highres"]].boundary.plot(ax=ax, color="lime", linewidth=2.4)
        for _, h in hs[hs["recommended_for_highres"]].iterrows():
            ax.annotate(h["hotspot_id"], (h["centroid_x"], h["centroid_y"]), color="white", fontsize=8,
                        ha="center", bbox=dict(facecolor="black", alpha=0.6, pad=1.5))
        ax.plot([], [], color="lime", linewidth=2.4, label="고해상도 확인 추천")
        ax.plot([], [], color="cyan", linewidth=1.8, label="기타 hotspot")
        ax.legend(loc="lower left", fontsize=8)
    _footer(fig)
    figs["fig5_hotspots"] = out / "fig5_hotspots.png"
    fig.savefig(figs["fig5_hotspots"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Figure 6 - SkyWatch recommended ROI
    fig, ax = plt.subplots(figsize=(8, 7))
    _bg(ax, t2, f"Fig.6 SkyWatch 50cm 추천 ROI ({len(roi)}건)")
    _plot_aoi(ax, aoi, lw=1.0)
    if len(roi):
        roi.boundary.plot(ax=ax, color="orange", linewidth=2.2)
        hs[hs["recommended_for_highres"]].boundary.plot(ax=ax, color="lime", linewidth=1.5)
        for _, r in roi.iterrows():
            c = r.geometry.centroid
            ax.annotate(f"{r['roi_id']}\n{r['hotspot_id']} · {r['roi_area_km2']:.2f}km²", (c.x, r.geometry.bounds[3]),
                        color="white", fontsize=8, ha="center", va="bottom",
                        bbox=dict(facecolor="black", alpha=0.55, pad=1.5))
    else:
        ax.text(0.5, 0.5, "추천 ROI 없음 (Sentinel-2 기준 충족 hotspot 없음)", transform=ax.transAxes,
                ha="center", color="white", fontsize=12, bbox=dict(facecolor="black", alpha=0.5))
    _footer(fig)
    figs["fig6_skywatch_roi"] = out / "fig6_skywatch_roi.png"
    fig.savefig(figs["fig6_skywatch_roi"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    logger.info("[FIG] %d개 figure 저장: %s", len(figs), out)
    return figs
