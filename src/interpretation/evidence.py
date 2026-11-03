"""판독 근거 영상 조각: 객체·필지마다 두 시기 0.25m 원본을 나란히 자르고 윤곽을 그린다.

판독_코드의 chips/sheets와 같은 역할이다. 판정을 그대로 믿지 말고 사람이 이 조각을 보고
확정하도록, 보고서와 검수 표본에 붙인다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .obia import read_tile

def _font(size: int = 12):
    """한글이 나오는 글꼴. 기본 비트맵 글꼴은 한글을 네모로 그린다."""
    for name in ("malgun.ttf", "NanumGothic.ttf", "NotoSansCJK-Regular.ttc", "AppleGothic.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


FONT = _font()


def _rings(g):
    for p in getattr(g, "geoms", [g]):
        if p.geom_type == "Polygon":
            yield list(p.exterior.coords)


def chip_pair(images: dict[str, str], dates: dict[str, str], geom, outline_geoms: list, out: Path,
              half_m: float = 30.0, px: int = 360, title: str = "", parcel=None) -> Path:
    """geom 중심으로 시기별 조각을 만들어 가로로 붙인다. 노랑 = 판독 객체, 흰색 = 필지."""
    c = geom.centroid
    x0, y0, x1, y1 = geom.bounds
    s = max(half_m, (x1 - x0) / 2 + 8, (y1 - y0) / 2 + 8)
    b = (c.x - s, c.y - s, c.x + s, c.y + s)

    def tf(xy):
        return ((xy[0] - b[0]) / (2 * s) * px, (b[3] - xy[1]) / (2 * s) * px)

    tiles = []
    for e, path in images.items():
        rgb = read_tile(path, b, 2 * s / px)
        im = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize((px, px))
        dr = ImageDraw.Draw(im)
        if parcel is not None:
            for ring in _rings(parcel):
                dr.line([tf(p) for p in ring], fill=(255, 255, 255), width=1)
        for g in outline_geoms:
            for ring in _rings(g):
                dr.line([tf(p) for p in ring], fill=(255, 220, 0), width=2)
        dr.rectangle((0, 0, 92, 14), fill=(0, 0, 0))
        dr.text((3, 2), dates.get(e, e), fill=(255, 255, 255), font=FONT)
        tiles.append(im)
    head = 16 if title else 0
    sheet = Image.new("RGB", (len(tiles) * px + (len(tiles) - 1) * 6, px + head), "white")
    if title:
        ImageDraw.Draw(sheet).text((2, 2), title, fill=(0, 0, 0), font=FONT)
    for i, im in enumerate(tiles):
        sheet.paste(im, (i * (px + 6), head))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    return out
