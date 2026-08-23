"""Sentinel-2 위성영상 자동 수집 (Microsoft Planetary Computer STAC).

NGII 항공정사영상은 로그인 후 GUI 프로그램으로만 받을 수 있어 자동화가
불가능하다 (download.py 참고). 반면 Sentinel-2 L2A는 Microsoft Planetary
Computer의 공개 STAC 카탈로그를 통해 API Key 없이 전량 자동 수집이
가능하므로, 완전 자동화된 Baseline 파이프라인의 1차 영상 소스로 사용한다.

주의:
- 해상도는 10m(가시광/NIR 밴드 기준)로, 개별 단독주택 단위의 신축/증축은
  탐지가 어렵다. 대형 아파트단지/대규모 토지조성 등 큰 변화 탐지에는
  유효하다. 이 한계는 README/Known Limitations에도 명시한다.
- 이 모듈의 bbox 탐색 인자는 "장면 검색용" 좌표일 뿐, 실제 분석 AOI가
  아니다. Change Detection/Clip에는 별도로 확보한 공식 AOI 경계를
  사용해야 한다.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import planetary_computer
import requests
from pystac_client import Client

logger = logging.getLogger("download_satellite")

STAC_API_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"


def search_sentinel2(
    bbox: tuple[float, float, float, float],
    date_range: str,
    max_cloud_cover: float = 20.0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Sentinel-2 L2A 장면을 검색한다.

    Args:
        bbox: (minx, miny, maxx, maxy), EPSG:4326 기준. 장면 검색용
            대략적인 범위이며 최종 분석 AOI가 아니다.
        date_range: STAC datetime 형식, 예: "2022-01-01/2022-12-31".
        max_cloud_cover: 허용 최대 구름량(%).
        limit: 최대 반환 개수.

    Returns:
        구름량 오름차순으로 정렬된 STAC item(dict) 목록.
    """
    catalog = Client.open(STAC_API_URL)
    search = catalog.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=date_range,
        query={"eo:cloud_cover": {"lt": max_cloud_cover}},
        limit=limit,
    )
    items = list(search.items())
    items.sort(key=lambda it: it.properties.get("eo:cloud_cover", 100))
    logger.info(
        "[DATA] Sentinel-2 검색 결과 %d건 (bbox=%s, date_range=%s, cloud<%.0f%%)",
        len(items), bbox, date_range, max_cloud_cover,
    )
    return [item.to_dict() for item in items]


def download_sentinel2_bands(
    item: dict[str, Any],
    bands: list[str],
    out_dir: str | Path,
) -> list[Path]:
    """선택한 밴드 COG 파일을 다운로드한다 (서명된 URL 사용).

    Args:
        item: search_sentinel2()이 반환한 STAC item dict 하나.
        bands: 밴드 asset 키 목록, 예: ["B02", "B03", "B04", "B08"].
        out_dir: 저장 디렉터리.

    Returns:
        저장된 파일 경로 목록.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    signed_item = planetary_computer.sign(item)

    saved: list[Path] = []
    item_id = signed_item["id"]
    for band in bands:
        asset = signed_item["assets"].get(band)
        if asset is None:
            raise KeyError(f"[DATA] '{band}' 밴드가 이 item에 없습니다: {item_id}")
        href = asset["href"]
        out_path = out_dir / f"{item_id}_{band}.tif"
        logger.info("[DATA] 다운로드: %s -> %s", band, out_path)
        resp = requests.get(href, stream=True, timeout=120)
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
        saved.append(out_path)

    logger.info("[DATA] Sentinel-2 item %s 다운로드 완료 (%d개 밴드)", item_id, len(saved))
    return saved
