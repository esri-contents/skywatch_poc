"""밴드 이름 -> 역할(Red/Green/Blue/NIR) 매핑 - Sentinel-2/RGB/RGBNIR 공용.

기존 코드(to_grayscale, visualize._read_true_color 등)는 band_order가 항상
Sentinel-2 밴드 이름(B02/B03/B04/B08)이라고 가정하고 인덱스를 하드코딩했다.
국가철도공단 Demo에서는 SkyWatch/항공영상이 RGB(3-band) 또는 RGBNIR(4-band)
GeoTIFF로 들어올 수 있어, 밴드 이름이 "B02" 대신 "red"/"blue" 같은 role
이름이거나 config에서 지정한 임의 이름일 수 있다.

이 모듈은 band_order(문자열 리스트)를 role(red/green/blue/nir) -> 인덱스
매핑으로 변환한다. Sentinel-2 이름은 기존과 동일하게 인식되므로(B02->blue 등)
기존 4-band 경로는 동작이 전혀 바뀌지 않는다.
"""

from __future__ import annotations

# Sentinel-2 밴드 이름 -> 공통 role. 대소문자 구분 없이 비교한다.
SENTINEL2_ROLE_ALIASES = {
    "b02": "blue",
    "b03": "green",
    "b04": "red",
    "b08": "nir",
}


def resolve_band_roles(band_order: list[str]) -> dict[str, int]:
    """band_order를 role(red/green/blue/nir 등) -> 인덱스 매핑으로 변환한다.

    Sentinel-2 이름(B02/B03/B04/B08, 대소문자 무관)은 자동으로 blue/green/red/nir로
    치환된다. 그 외 이름은 소문자로 그대로 role로 취급한다(예: "red", "nir",
    "Red" -> "red"). 임의로 순서를 추측하지 않으며, 밴드 이름은 GeoTIFF의
    band description 또는 config의 band_order/band_schema에서 와야 한다.

    Args:
        band_order: 스택의 밴드 이름 목록 (예: ["B02","B03","B04","B08"],
            ["red","green","blue"], ["blue","green","red","nir"]).

    Returns:
        {"red": idx, "green": idx, "blue": idx, ...} 형태 매핑.

    Raises:
        ValueError: red/green/blue 중 하나라도 band_order에서 찾을 수 없을 때.
    """
    roles: dict[str, int] = {}
    for i, b in enumerate(band_order):
        key = str(b).strip().lower()
        role = SENTINEL2_ROLE_ALIASES.get(key, key)
        roles[role] = i

    missing = [r for r in ("red", "green", "blue") if r not in roles]
    if missing:
        raise ValueError(
            f"[BAND] band_order에서 RGB 역할을 찾을 수 없습니다: {missing} "
            f"(band_order={list(band_order)}). Sentinel-2 이름(B02/B03/B04/B08) 또는 "
            "role 이름(red/green/blue[/nir])을 사용하세요. 밴드 순서를 임의로 "
            "추측하지 않습니다."
        )
    return roles


def determine_band_order(
    tif_path,
    fallback_band_order: list[str] | None = None,
) -> list[str]:
    """GeoTIFF의 band description을 우선 사용하고, 없으면 config fallback을 쓴다.

    실제 GeoTIFF에 band description(예: rasterio dst.descriptions)이 있으면
    그것을 신뢰하고, 없을 때만 config에서 지정한 band_order/band_schema를
    쓴다. 둘 다 없으면 밴드 순서를 임의로 추측하지 않고 예외를 낸다.

    Args:
        tif_path: 밴드 순서를 알아낼 GeoTIFF 경로.
        fallback_band_order: description이 없을 때 쓸 band 이름 목록
            (config의 imagery.band_order 등에서 옴).

    Returns:
        밴드 이름 목록 (파일의 밴드 개수와 동일한 길이).
    """
    import rasterio

    with rasterio.open(tif_path) as src:
        descriptions = src.descriptions
        count = src.count

    if descriptions and all(d for d in descriptions):
        return list(descriptions)

    if not fallback_band_order:
        raise ValueError(
            f"[BAND] {tif_path}에 band description이 없고 fallback band_order도 "
            "지정되지 않았습니다. GeoTIFF band description을 채우거나 project "
            "config의 imagery.band_order를 지정하세요. 밴드 순서를 임의로 "
            "추측하지 않습니다."
        )
    if len(fallback_band_order) != count:
        raise ValueError(
            f"[BAND] {tif_path}의 밴드 개수({count})와 config band_order 길이"
            f"({len(fallback_band_order)})가 다릅니다: {fallback_band_order}"
        )
    return list(fallback_band_order)
