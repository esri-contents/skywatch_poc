"""보고서용 그림: 지도(범례·축척·방위·동 이름), 판독 과정 예시, 분포도, 정확도 그래프, 근거 영상 묶음.

모든 그림은 흰 바탕 인쇄용이며 맑은 고딕으로 한글을 쓴다. 반환값은 PNG 경로.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import FancyArrow, Patch, Rectangle  # noqa: E402

for _f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
    if any(_f in f.name for f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f
        break
plt.rcParams.update({"axes.unicode_minus": False, "font.size": 9})

STATUS = [("있음(등재+판독)", "#1f6f78"), ("있음(등재만)", "#8fb8bd"), ("있음(판독만)", "#c8414b"), ("영상 부족", "#9aa3aa")]
C_IND, C_ATT, C_LOW = "#c8414b", "#e9a23b", "#8a8f94"
C_NOREG = "#7b4fa0"  # 미등재 · 도면 있음·대장 없음(v2.0)
C_NEW, C_GONE = "#ffb703", "#3a7bd5"


def _dong(addr: pd.Series) -> pd.Series:
    return addr.str.extract(r"(?:광명시|시흥시)\s+(\S+동)")[0].fillna("미상")


def _read_rgb(path, bounds, px):
    w, h = max(1, int((bounds[2] - bounds[0]) / px)), max(1, int((bounds[3] - bounds[1]) / px))
    with rasterio.open(path) as ds:
        a = ds.read([1, 2, 3], window=from_bounds(*bounds, ds.transform), out_shape=(3, h, w),
                    boundless=True, resampling=Resampling.average)
    return a.transpose(1, 2, 0)


def _read_band(path, bounds, px, resampling=Resampling.nearest):
    w, h = max(1, int((bounds[2] - bounds[0]) / px)), max(1, int((bounds[3] - bounds[1]) / px))
    with rasterio.open(path) as ds:
        return ds.read(1, window=from_bounds(*bounds, ds.transform), out_shape=(h, w), boundless=True,
                       fill_value=ds.nodata or 0, resampling=resampling)


def _decorate(ax, bounds, bar_m: int, loc: str = "lower left"):
    """축척 막대와 방위표."""
    x0, y0, x1, y1 = bounds
    W, H = x1 - x0, y1 - y0
    bx, by = (x0 + W * 0.04, y0 + H * 0.03) if loc == "lower left" else (x1 - W * 0.04 - bar_m, y0 + H * 0.03)
    ax.add_patch(Rectangle((bx - W * 0.012, by - H * 0.012), bar_m + W * 0.024, H * 0.05, facecolor="white", alpha=.85, edgecolor="none", zorder=8))
    ax.add_patch(Rectangle((bx, by), bar_m / 2, H * 0.006, facecolor="black", zorder=9))
    ax.add_patch(Rectangle((bx + bar_m / 2, by), bar_m / 2, H * 0.006, facecolor="white", edgecolor="black", lw=.6, zorder=9))
    ax.text(bx + bar_m / 2, by + H * 0.012, f"{bar_m:,} m" if bar_m < 1000 else f"{bar_m / 1000:g} km", ha="center", va="bottom", fontsize=8, zorder=9)
    nx, ny = x1 - W * 0.06, y1 - H * 0.08
    ax.add_patch(FancyArrow(nx, ny, 0, H * 0.04, width=W * 0.012, head_width=W * 0.035, head_length=H * 0.018, color="black", zorder=9))
    ax.text(nx, ny - H * 0.012, "N", ha="center", va="top", fontsize=10, fontweight="bold", zorder=9,
            bbox=dict(facecolor="white", edgecolor="none", alpha=.8, pad=1))


def _dong_labels(ax, parcels):
    d = parcels.assign(dong=_dong(parcels["addr"].fillna("")))
    d = d[d["dong"] != "미상"]
    for name, g in d.groupby("dong"):
        if len(g) < 50:
            continue
        p = g.geometry.union_all().representative_point()
        ax.text(p.x, p.y, name, ha="center", va="center", fontsize=10, fontweight="bold", color="#111", zorder=7,
                bbox=dict(facecolor="white", edgecolor="#555", alpha=.85, boxstyle="round,pad=0.25", lw=.5))


def _base(cfg, aoi, t1_path, px=6.0, figsize=(7.0, 13.0)):
    x0, y0, x1, y1 = aoi.total_bounds
    pad = 60
    b = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    img = _read_rgb(t1_path, b, px)
    fig, ax = plt.subplots(figsize=figsize, dpi=200)
    ax.imshow(img, extent=(b[0], b[2], b[1], b[3]), alpha=.55)
    ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_axis_off()
    return fig, ax, b


def overview_map(cfg, parcels, objs, t1: str, out: Path) -> Path:
    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(parcels.crs)
    path = {e["id"]: e["path"] for e in cfg["imagery"]["epochs"]}[t1]
    fig, ax, b = _base(cfg, aoi, path)
    for status, color in STATUS:
        sub = parcels[parcels[f"status_{t1}"] == status]
        if len(sub):
            sub.plot(ax=ax, color=color, alpha=.85, linewidth=0, zorder=3)
    aoi.boundary.plot(ax=ax, color="#111", linewidth=1.3, zorder=4)
    new = objs[(objs["kind"] == "new") & (objs["tier"] == "A")]
    if len(new):
        c = new.geometry.centroid
        ax.scatter(c.x, c.y, s=70, facecolors="none", edgecolors=C_NEW, linewidths=2, zorder=6)
    _dong_labels(ax, parcels)
    _decorate(ax, b, 1000)
    handles = [Patch(color=c, label=s) for s, c in STATUS] + [
        Line2D([], [], marker="o", ls="", mfc="none", mec=C_NEW, mew=2, ms=9, label="신규 의심(A)"),
        Line2D([], [], color="#111", lw=1.3, label="지구 경계")]
    ax.legend(handles=handles, loc="upper left", fontsize=8.5, frameon=True, framealpha=.92, title=f"필지 상태({t1})" if False else "필지 상태", title_fontsize=9)
    return _save(fig, out)


def distribution_maps(cfg, parcels, objs, t1: str, out: Path) -> Path:
    """왼쪽: 미등재 구분별 필지, 오른쪽: 신규·소멸 의심 위치."""
    aoi = gpd.read_file(cfg["paths"]["aoi"]).to_crs(parcels.crs)
    path = {e["id"]: e["path"] for e in cfg["imagery"]["epochs"]}[t1]
    x0, y0, x1, y1 = aoi.total_bounds
    pad = 60
    b = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    img = _read_rgb(path, b, 8.0)
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 10.2), dpi=200)
    for ax in axes:
        ax.imshow(img, extent=(b[0], b[2], b[1], b[3]), alpha=.45)
        aoi.boundary.plot(ax=ax, color="#111", linewidth=1.0, zorder=4)
        ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_axis_off()
    ax = axes[0]
    for conf, color in [("저신뢰", C_LOW), ("부속 의심", C_ATT), ("대장 없음", C_NOREG), ("독립", C_IND)]:
        sub = parcels[parcels["unregistered_conf"] == conf]
        if len(sub):
            sub.plot(ax=ax, color=color, linewidth=.3, edgecolor=color, zorder=3)
            c = sub.geometry.representative_point()
            ax.scatter(c.x, c.y, s=7 if conf != "독립" else 11, color=color, zorder=5, linewidths=0)
    ax.set_title("(가) 미등재 구조물 필지", fontsize=10.5, fontweight="bold", loc="left")
    ax.legend(handles=[Patch(color=C_IND, label=f'독립 · 도면·대장 없음 ({(parcels["unregistered_conf"] == "독립").sum()}필지)'),
                       Patch(color=C_NOREG, label=f'대장 없음 · 도면 있음 ({(parcels["unregistered_conf"] == "대장 없음").sum()})'),
                       Patch(color=C_ATT, label=f'부속 의심 ({(parcels["unregistered_conf"] == "부속 의심").sum()})'),
                       Patch(color=C_LOW, label=f'저신뢰 ({(parcels["unregistered_conf"] == "저신뢰").sum()})')],
              loc="upper left", fontsize=8, framealpha=.92)
    _decorate(ax, b, 1000)
    ax = axes[1]
    for kind, tiers, color, mk, label in [("gone", ["A", "B"], C_GONE, "s", "소멸 의심(A·B)"), ("new", ["A"], C_NEW, "o", "신규 의심(A)")]:
        o = objs[(objs["kind"] == kind) & objs["tier"].isin(tiers)]
        if len(o):
            c = o.geometry.centroid
            ax.scatter(c.x, c.y, s=np.clip(o["area_m2"], 20, 400) * .45 + 20, marker=mk, facecolors="none", edgecolors=color, linewidths=1.8, zorder=6,
                       label=f"{label} {len(o)}건")
    ax.set_title("(나) 신규·소멸 의심 위치(크기 = 면적)", fontsize=10.5, fontweight="bold", loc="left")
    ax.legend(loc="upper left", fontsize=8, framealpha=.92)
    _dong_labels(ax, parcels)
    _decorate(ax, b, 1000)
    fig.tight_layout()
    return _save(fig, out)


def method_panels(cfg, parcels, objs, buildings, t1: str, out: Path, half_m: float = 70.0) -> Path:
    """판독 과정 4단계: 원본 영상 → 조각 → 구조물 확률 → 필지 판정. 독립 미등재 객체가 많은 곳을 고른다."""
    ic = cfg["interpretation"]
    o = Path(ic["out_dir"])
    path = {e["id"]: e["path"] for e in cfg["imagery"]["epochs"]}[t1]
    u = objs[(objs["kind"] == "unregistered") & (objs["tier"] == "U")]
    c = u.geometry.centroid
    xy = np.c_[c.x, c.y]
    dens = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1) ** .5
    k = int(np.argmax((dens < half_m).sum(1) + (u["area_m2"].to_numpy() > 60) * .5))
    cx, cy = xy[k]
    b = (cx - half_m, cy - half_m, cx + half_m, cy + half_m)
    img = _read_rgb(path, b, 0.25)
    seg = _read_band(o / "segments" / f"segments_{t1}.tif", b, 0.25)
    prob = _read_band(o / "rasters" / f"structure_prob_{t1}.tif", b, 0.25).astype(float)
    prob[prob == 255] = np.nan
    edge = np.zeros(seg.shape, bool)
    edge[:-1] |= seg[:-1] != seg[1:]
    edge[:, :-1] |= seg[:, :-1] != seg[:, 1:]
    fig, axes = plt.subplots(2, 2, figsize=(9.6, 9.8), dpi=200)
    ext = (b[0], b[2], b[1], b[3])
    titles = ["① 원본 항공 정사영상", "② 조각화(색·밝기가 비슷한 화소 묶음)", "③ 조각별 구조물 확률", "④ 판정: 기준 건물·대장 대조"]
    for ax, t in zip(axes.ravel(), titles):
        ax.set_title(t, fontsize=10.5, fontweight="bold", loc="left")
        ax.set_xlim(b[0], b[2]); ax.set_ylim(b[1], b[3]); ax.set_axis_off()
    axes[0, 0].imshow(img, extent=ext)
    ov = img.copy(); ov[edge] = (255, 230, 0)
    axes[0, 1].imshow(ov, extent=ext)
    axes[1, 0].imshow(img.mean(2), extent=ext, cmap="gray", alpha=.6)
    im = axes[1, 0].imshow(prob / 100, extent=ext, cmap="magma", vmin=0, vmax=1, alpha=.75)
    cb = fig.colorbar(im, ax=axes[1, 0], fraction=.04, pad=.01); cb.set_label("구조물 확률", fontsize=8); cb.ax.tick_params(labelsize=7)
    ax = axes[1, 1]
    ax.imshow(img, extent=ext, alpha=.75)
    from shapely.geometry import box
    win = box(*b)
    bl = buildings[buildings.intersects(win)]
    pc = parcels[parcels.intersects(win)]
    pc.boundary.plot(ax=ax, color="white", linewidth=.8)
    if "reg" in bl:  # v2.0: 대장 연계 = 청록, 대장 미연계 윤곽 = 회색
        bl[bl["reg"] != "연계"].plot(ax=ax, facecolor="#9aa3aa", alpha=.45, edgecolor="#9aa3aa", linewidth=1)
        bl = bl[bl["reg"] == "연계"]
    bl.plot(ax=ax, facecolor="#1f6f78", alpha=.45, edgecolor="#1f6f78", linewidth=1)
    uu = objs[(objs["kind"] == "unregistered") & objs.intersects(win)]
    for tier, color in [("U", C_IND), ("R", C_NOREG), ("UA", C_ATT)]:
        s = uu[uu["tier"] == tier]
        if len(s):
            s.plot(ax=ax, facecolor="none", edgecolor=color, linewidth=2.2)
    ax.legend(handles=[Patch(facecolor="#1f6f78", alpha=.5, label="대장 연계 건물(등재)"), Patch(facecolor="#9aa3aa", alpha=.5, label="대장 없는 건물 윤곽"),
                       Patch(facecolor="none", edgecolor=C_IND, lw=2, label="미등재 · 독립"), Patch(facecolor="none", edgecolor=C_NOREG, lw=2, label="미등재 · 대장 없음"),
                       Patch(facecolor="none", edgecolor=C_ATT, lw=2, label="미등재 · 부속 의심"), Line2D([], [], color="white", lw=1.5, label="필지 경계")],
              loc="lower right", fontsize=7.5, framealpha=.9, facecolor="#333", labelcolor="white")
    for a in axes.ravel():
        _decorate(a, b, 20)
    fig.tight_layout()
    return _save(fig, out)


def precision_chart(rows: list[tuple[str, float, float, float, str]], out: Path) -> Path:
    """rows = (이름, 정밀도, 하한, 상한, 색). 가로 막대 + 95% 구간."""
    fig, ax = plt.subplots(figsize=(7.2, .48 * len(rows) + .8), dpi=200)
    y = np.arange(len(rows))[::-1]
    for yi, (name, p, lo, hi, col) in zip(y, rows):
        ax.barh(yi, p * 100, color=col, height=.55)
        if lo == lo and hi == hi:
            ax.plot([lo * 100, hi * 100], [yi, yi], color="#111", lw=1.2)
            ax.plot([lo * 100] * 2, [yi - .12, yi + .12], color="#111", lw=1.2)
            ax.plot([hi * 100] * 2, [yi - .12, yi + .12], color="#111", lw=1.2)
        ax.text(min(p * 100, 100) + 1.5 if not (hi == hi) else hi * 100 + 1.5, yi, f"{p * 100:.0f}%", va="center", fontsize=8.5)
    ax.set_yticks(y, [r[0] for r in rows], fontsize=9)
    ax.set_xlim(0, 110); ax.set_xticks([0, 25, 50, 75, 100], ["0", "25", "50", "75", "100%"])
    ax.grid(axis="x", color="#ddd", lw=.6); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.set_xlabel("표본 정밀도(막대)와 95% 신뢰구간(선)", fontsize=8.5)
    fig.tight_layout()
    return _save(fig, out)


def recall_chart(rec: dict[str, dict], out: Path) -> Path:
    """건물 크기별 재현율(시기별 묶음 막대)."""
    cats = ["20-50㎡", "50-100㎡", "100-300㎡", "300㎡+"]
    fig, ax = plt.subplots(figsize=(7.2, 2.8), dpi=200)
    x = np.arange(len(cats))
    for i, (lab, r) in enumerate(rec.items()):
        v = [r["by_size"][c] * 100 for c in cats]
        bars = ax.bar(x + (i - .5) * .36, v, width=.34, color=["#1f6f78", "#8fb8bd"][i % 2], label=f'{lab} (전체 {r["recall"] * 100:.0f}%)')
        for bb, vv in zip(bars, v):
            ax.text(bb.get_x() + bb.get_width() / 2, vv + 1.5, f"{vv:.0f}", ha="center", fontsize=8)
    ax.set_xticks(x, ["20~50㎡", "50~100㎡", "100~300㎡", "300㎡ 이상"])
    ax.set_ylim(0, 70); ax.set_ylabel("재현율(%)")
    ax.grid(axis="y", color="#ddd", lw=.6); ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    return _save(fig, out)


def _save(fig, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight", pad_inches=.05, facecolor="white")
    plt.close(fig)
    return out
