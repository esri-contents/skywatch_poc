"""Baseline PoC 결과의 정적 시각화 (True-color, Before/After, 우선순위 지도).

이전에 outputs/figures/*.png로 존재하던 산출물(before_after_change.png,
priority_map.png 등)이 재사용 가능한 스크립트 없이 일회성으로 만들어져
있었다 - 파이프라인을 다시 돌릴 때마다(예: robust_cva 도입, 새 T 시점
추가) 수동으로 다시 그려야 했다. 이 모듈로 대체해 pipeline 산출물만
있으면 언제든 동일한 그림을 재생성할 수 있게 한다.

matplotlib은 Agg 백엔드를 명시적으로 강제한다 (headless 환경에서
TkAgg로 자동 선택되어 실패하는 문제 - solafune-sentinel2-change에서도
동일하게 겪은 문제라 미리 방지).
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = "Malgun Gothic"  # 한글 라벨(Windows 기본 한글 글꼴) - DejaVu Sans는 한글 미지원
matplotlib.rcParams["axes.unicode_minus"] = False

import geopandas as gpd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from rasterio.plot import plotting_extent  # noqa: E402

logger = logging.getLogger("visualize")

PRIORITY_COLORS = {"HIGH": "#e6194b", "MEDIUM": "#f58230", "LOW": "#ffdc32"}
CHANGE_TYPE_COLORS = {
    "NEW_BUILDING": "#e6194b",
    "EXPANSION_OR_RECONSTRUCTION": "#f58230",
    "OTHER_CHANGE": "#787878",
    "DEMOLITION": "#4646c8",
}


def _read_true_color(
    stack_path: str | Path,
    band_order: list[str] = ("B02", "B03", "B04", "B08"),
    percentile: tuple[float, float] = (2, 98),
) -> tuple[np.ndarray, tuple, object]:
    """스택 GeoTIFF를 (H, W, 3) 0~1 true-color 배열로 읽는다 (percentile stretch, NoData=0 제외)."""
    idx = {b: i for i, b in enumerate(band_order)}
    with rasterio.open(stack_path) as src:
        arr = src.read().astype(np.float32)
        extent = plotting_extent(src)
        transform = src.transform
        nodata = src.nodata

    rgb = arr[[idx["B04"], idx["B03"], idx["B02"]]]
    valid = np.all(arr != nodata, axis=0) if nodata is not None else np.ones(arr.shape[1:], dtype=bool)

    out = np.zeros((rgb.shape[1], rgb.shape[2], 3), dtype=np.float32)
    for b in range(3):
        band = rgb[b]
        vals = band[valid]
        if vals.size == 0:
            continue
        lo, hi = np.percentile(vals, percentile)
        if hi <= lo:
            hi = lo + 1
        stretched = np.clip((band - lo) / (hi - lo), 0, 1)
        out[:, :, b] = np.where(valid, stretched, 0)

    return out, extent, transform


def plot_before_after_grid(
    t1_path: str | Path,
    t2_path: str | Path,
    change_prob_path: str | Path,
    building_results_path: str | Path,
    t1_label: str,
    t2_label: str,
    out_path: str | Path,
) -> Path:
    """T1/T2 true-color + Change Probability + 변화유형 분류 4패널 그림.

    Args:
        t1_path/t2_path: T1/T2 스택 GeoTIFF (EPSG:5186 등 분석 CRS).
        change_prob_path: change_probability.tif.
        building_results_path: building_change_results.gpkg (분석 CRS 유지본).
        t1_label/t2_label: 패널 제목에 쓸 날짜 라벨 (예: "2022-05-17").
        out_path: 저장할 PNG 경로.

    Returns:
        저장된 파일 경로.
    """
    t1_rgb, extent, _ = _read_true_color(t1_path)
    t2_rgb, _, _ = _read_true_color(t2_path)

    with rasterio.open(change_prob_path) as src:
        prob = src.read(1)

    results = gpd.read_file(building_results_path)
    if results.crs is not None:
        with rasterio.open(t2_path) as src:
            results = results.to_crs(src.crs)

    fig, axes = plt.subplots(1, 4, figsize=(20, 5.5))

    axes[0].imshow(t1_rgb, extent=extent)
    axes[0].set_title(f"T1 · {t1_label}")

    axes[1].imshow(t2_rgb, extent=extent)
    axes[1].set_title(f"T2 · {t2_label}")

    axes[2].imshow(t2_rgb * 0.5, extent=extent)
    prob_masked = np.ma.masked_where(prob < 0.05, prob)
    axes[2].imshow(prob_masked, extent=extent, cmap="magma", vmin=0, vmax=1, alpha=0.85)
    axes[2].set_title("Change Probability")

    axes[3].imshow(t2_rgb * 0.5 + 0.25, extent=extent)
    for change_type, color in CHANGE_TYPE_COLORS.items():
        subset = results[results["change_type"] == change_type]
        if len(subset):
            subset.plot(ax=axes[3], color=color, edgecolor="none", alpha=0.9)
    axes[3].set_title("Classification")
    axes[3].legend(
        handles=[Patch(facecolor=c, label=t) for t, c in CHANGE_TYPE_COLORS.items()],
        loc="lower left", fontsize=6, framealpha=0.8,
    )

    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("[VIZ] 저장 완료: %s", out_path)
    return out_path


def plot_site_qa_contact_sheet(
    t1_path: str | Path,
    t2_path: str | Path,
    change_polygons_path: str | Path,
    site_ids: list[str],
    out_dir: str | Path,
    prefix: str,
    labels: dict[str, str] | None = None,
    buffer_m: float = 60.0,
    sites_per_page: int = 10,
) -> list[Path]:
    """HIGH 등급 site_id별 T1/T2 chip을 모아 육안검수용 contact sheet를 만든다.

    site별로 이미지를 하나씩 열어보는 대신, 여러 site를 한 장에 모아 빠르게
    훑어볼 수 있게 한다. site 수가 많으면 sites_per_page 단위로 여러 페이지로
    나눈다.

    Args:
        t1_path/t2_path: T1/T2 스택 GeoTIFF.
        change_polygons_path: change_polygons.gpkg (site_id == change_id 기준 bounds 추출용).
        site_ids: 검수할 site_id(=change_id) 목록.
        out_dir: 저장 디렉터리.
        prefix: 파일명 접두사 (예: "qa_2022_2024_high").
        labels: {site_id: "부가 라벨 문자열"} - 있으면 타이틀에 함께 표시.
        buffer_m: 폴리곤 bounds 주변 여유(m).
        sites_per_page: 페이지당 site 수.

    Returns:
        저장된 페이지 PNG 경로 목록.
    """
    t1_rgb, _, t1_transform = _read_true_color(t1_path)
    t2_rgb, _, _ = _read_true_color(t2_path)

    polys = gpd.read_file(change_polygons_path)
    labels = labels or {}

    chips = []
    for site_id in site_ids:
        matches = polys[polys["change_id"] == site_id]
        if matches.empty:
            logger.warning("[VIZ] site_id=%s 를 change_polygons에서 찾지 못함 - 건너뜀", site_id)
            continue
        minx, miny, maxx, maxy = matches.iloc[0].geometry.bounds
        minx, miny, maxx, maxy = minx - buffer_m, miny - buffer_m, maxx + buffer_m, maxy + buffer_m

        row0, col0 = rasterio.transform.rowcol(t1_transform, minx, maxy)
        row1, col1 = rasterio.transform.rowcol(t1_transform, maxx, miny)
        row0, row1 = sorted((max(0, row0), max(0, row1)))
        col0, col1 = sorted((max(0, col0), max(0, col1)))
        row1 = min(row1, t1_rgb.shape[0])
        col1 = min(col1, t1_rgb.shape[1])
        if row1 <= row0 or col1 <= col0:
            continue

        chips.append((site_id, t1_rgb[row0:row1, col0:col1], t2_rgb[row0:row1, col0:col1]))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    for start in range(0, len(chips), sites_per_page):
        batch = chips[start : start + sites_per_page]
        fig, axes = plt.subplots(len(batch), 2, figsize=(6, 2.8 * len(batch)))
        if len(batch) == 1:
            axes = axes.reshape(1, 2)
        for i, (site_id, c1, c2) in enumerate(batch):
            title = f"{site_id}  {labels.get(site_id, '')}".strip()
            axes[i, 0].imshow(c1)
            axes[i, 0].set_ylabel(title, fontsize=8, rotation=0, ha="right", va="center")
            axes[i, 0].set_xticks([])
            axes[i, 0].set_yticks([])
            axes[i, 1].imshow(c2)
            axes[i, 1].set_xticks([])
            axes[i, 1].set_yticks([])
            if i == 0:
                axes[i, 0].set_title("T1")
                axes[i, 1].set_title("T2")
        fig.tight_layout()
        page_num = start // sites_per_page + 1
        out_path = out_dir / f"{prefix}_page{page_num}.png"
        fig.savefig(out_path, dpi=130)
        plt.close(fig)
        pages.append(out_path)
        logger.info("[VIZ] QA contact sheet 저장: %s (%d개 site)", out_path, len(batch))

    return pages


def plot_cadastre_context(
    basemap_path: str | Path,
    cadastre_path: str | Path,
    building_results_path: str | Path,
    aoi_path: str | Path,
    out_path: str | Path,
) -> Path:
    """지적(필지) 경계 위에 변화 후보를 겹쳐 "업무자료 연계"를 보여주는 그림.

    LH 데모 15~21분 구간("변화 후보와 사업지구·지적·건축물 연계")용 -
    build_cadastre.py로 만든 필지 데이터가 실제로 변화 후보와 겹쳐 보이는지
    확인하는 근거 자료.

    Args:
        basemap_path: 배경 true-color 스택 GeoTIFF.
        cadastre_path: build_cadastre.py 결과 GeoPackage.
        building_results_path: building_change_results.gpkg.
        aoi_path: AOI 벡터.
        out_path: 저장할 PNG 경로.

    Returns:
        저장된 파일 경로.
    """
    rgb, extent, _ = _read_true_color(basemap_path)
    with rasterio.open(basemap_path) as src:
        dst_crs = src.crs

    cadastre = gpd.read_file(cadastre_path).to_crs(dst_crs)
    results = gpd.read_file(building_results_path).to_crs(dst_crs)
    aoi = gpd.read_file(aoi_path).to_crs(dst_crs)

    fig, ax = plt.subplots(figsize=(9, 11))
    ax.imshow(rgb, extent=extent)
    cadastre.boundary.plot(ax=ax, color="#ffffff", linewidth=0.3, alpha=0.6)
    aoi.boundary.plot(ax=ax, color="#008060", linewidth=1.5)

    for tier in ["LOW", "MEDIUM", "HIGH"]:
        subset = results[results["inspection_priority"] == tier]
        if len(subset):
            subset.plot(ax=ax, color=PRIORITY_COLORS[tier], edgecolor="none", alpha=0.95)

    ax.legend(
        handles=[Patch(facecolor=c, label=t) for t, c in PRIORITY_COLORS.items()]
        + [Patch(facecolor="none", edgecolor="#ffffff", label=f"필지 경계 ({len(cadastre)}개)")],
        loc="lower left", fontsize=9, framealpha=0.85,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("변화 후보 - 지적(필지) 연계")

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("[VIZ] 저장 완료: %s", out_path)
    return out_path


def plot_priority_map(
    basemap_path: str | Path,
    building_results_path: str | Path,
    aoi_path: str | Path,
    out_path: str | Path,
) -> Path:
    """T2(또는 최신 시점) true-color 위에 AOI 경계 + 현장조사 우선순위(HIGH/MED/LOW)를 표시.

    Args:
        basemap_path: 배경으로 쓸 true-color 스택 GeoTIFF.
        building_results_path: building_change_results.gpkg.
        aoi_path: AOI 벡터.
        out_path: 저장할 PNG 경로.

    Returns:
        저장된 파일 경로.
    """
    rgb, extent, _ = _read_true_color(basemap_path)

    with rasterio.open(basemap_path) as src:
        dst_crs = src.crs

    results = gpd.read_file(building_results_path).to_crs(dst_crs)
    aoi = gpd.read_file(aoi_path).to_crs(dst_crs)

    fig, ax = plt.subplots(figsize=(9, 11))
    ax.imshow(rgb, extent=extent)
    aoi.boundary.plot(ax=ax, color="#008060", linewidth=1.5)

    # HIGH가 가장 위(마지막)에 그려지도록 순서를 고정 - LOW/MEDIUM에 가려지지 않게.
    for tier in ["LOW", "MEDIUM", "HIGH"]:
        subset = results[results["inspection_priority"] == tier]
        if len(subset):
            subset.plot(ax=ax, color=PRIORITY_COLORS[tier], edgecolor="none", alpha=0.95)

    ax.legend(
        handles=[Patch(facecolor=c, label=t) for t, c in PRIORITY_COLORS.items()],
        loc="lower left", fontsize=9, framealpha=0.85,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("현장조사 우선순위 (HIGH/MEDIUM/LOW)")

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    logger.info("[VIZ] 저장 완료: %s", out_path)
    return out_path
