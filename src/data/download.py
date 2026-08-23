"""공공데이터 자동 수집 클라이언트.

이 모듈은 API로 자동 수집이 가능한 데이터만 다룬다. 확인된 공식 서비스:
- VWorld WFS `lt_c_spbd` (도로명주소건물) - 실제 호출로 검증됨 (건물 footprint,
  층수, PNU 등 제공. 연면적/사용승인일 등 상세 건축물대장 속성은 없음).
- 공공데이터포털(data.go.kr) 국토교통부_GIS건물통합정보(WMS/WFS)
  https://www.data.go.kr/data/15123970/openapi.do - 아직 실제 호출 검증 안 됨
  (NO_OPENAPI_SERVICE_ERROR, 활용신청 상태 확인 필요).
- 공공데이터포털(data.go.kr) 국토교통부_건축HUB_건축물대장정보 서비스
  https://www.data.go.kr/data/15134735/openapi.do - 아직 실제 호출 검증 안 됨
  (상동).

정사영상(항공사진)은 국토지리정보원 국토정보플랫폼(map.ngii.go.kr)에서
로그인 후 전용 대용량 파일전송 프로그램(GUI)으로만 다운로드 가능함을
확인했다. 스크립트로 자동화할 수 있는 공개 API가 없어 이 모듈에서
다루지 않는다 (수동 다운로드 필요, 아래 download_imagery 참고).

모든 요청은 .env 에 저장된 API Key를 사용한다. Key를 코드에
하드코딩하지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

logger = logging.getLogger("download")

load_dotenv()

VWORLD_API_KEY = os.getenv("VWORLD_API_KEY")
DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY")

VWORLD_BASE_URL = "https://api.vworld.kr"


class MissingApiKeyError(RuntimeError):
    """필요한 API Key가 .env 에 설정되지 않았을 때 발생."""


def _require_key(key: str | None, env_name: str) -> str:
    if not key:
        raise MissingApiKeyError(
            f"[DATA] {env_name} 가 설정되어 있지 않습니다. "
            f".env 파일에 {env_name}=발급받은키 형태로 추가한 뒤 다시 실행하세요."
        )
    return key


VWORLD_BUILDING_LAYER = "lt_c_spbd"  # 도로명주소건물 (VWorld WFS GetCapabilities로 확인됨)


def download_vworld_wfs_layer(
    layer_name: str,
    bbox: tuple[float, float, float, float],
    out_path: str | Path,
    bbox_crs: str = "EPSG:4326",
    output_format: str = "application/json",
    page_size: int = 1000,
) -> Path:
    """VWorld WFS 서비스에서 지정한 layer를 bbox 범위로 전량 다운로드한다 (페이지네이션).

    layer_name 은 VWorld WFS GetCapabilities 응답에서 실제로 확인된 값만
    사용한다 (예: 건물 데이터는 VWORLD_BUILDING_LAYER="lt_c_spbd", 도로명주소건물 -
    확인일 기준 이 키에 건축물대장 속성이 결합된 별도 레이어는 없었다).

    주의: BBOX 파라미터에 CRS 접미사를 붙이면(예: "...,EPSG:4326") VWorld가
    좌표축 순서를 다르게 해석해 빈 결과를 반환하는 현상이 실측 확인되었다.
    따라서 BBOX는 좌표만, SRSNAME은 별도 파라미터로 분리해서 보낸다.

    Args:
        layer_name: VWorld GetCapabilities로 확인된 정확한 레이어명.
        bbox: (minx, miny, maxx, maxy), bbox_crs 기준.
        out_path: 저장할 GeoJSON 경로.
        bbox_crs: bbox 좌표계 (SRSNAME으로 전달).
        output_format: WFS 응답 포맷.
        page_size: 페이지당 최대 feature 수 (VWorld 기본 상한 1000).

    Returns:
        저장된 파일 경로.
    """
    key = _require_key(VWORLD_API_KEY, "VWORLD_API_KEY")
    # VWorld는 STARTINDEX 상한이 1000으로 확인됨(page_size=1000 기준 최대 2000건/쿼리).
    # totalFeatures가 이를 넘으면 bbox를 4분할해 재귀적으로 나눠 받는다.
    max_fetchable = page_size * 2

    def _fetch_bbox(b: tuple[float, float, float, float]) -> list[dict[str, Any]]:
        first_params: dict[str, Any] = {
            "SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
            "TYPENAME": layer_name, "BBOX": ",".join(str(v) for v in b),
            "SRSNAME": bbox_crs, "OUTPUT": output_format,
            "MAXFEATURES": page_size, "STARTINDEX": 0, "KEY": key,
        }
        resp = requests.get(f"{VWORLD_BASE_URL}/req/wfs", params=first_params, timeout=60)
        resp.raise_for_status()
        first_page = resp.json()
        total = first_page.get("totalFeatures", 0)
        features = list(first_page.get("features", []))

        if total > max_fetchable:
            minx, miny, maxx, maxy = b
            midx, midy = (minx + maxx) / 2, (miny + maxy) / 2
            logger.info(
                "[DATA] bbox=%s totalFeatures=%d > %d, 4분할 재시도",
                b, total, max_fetchable,
            )
            quads = [
                (minx, miny, midx, midy), (midx, miny, maxx, midy),
                (minx, midy, midx, maxy), (midx, midy, maxx, maxy),
            ]
            merged: list[dict[str, Any]] = []
            for q in quads:
                merged.extend(_fetch_bbox(q))
            return merged

        start_index = page_size
        while len(features) < total and start_index <= page_size:
            params = dict(first_params, STARTINDEX=start_index)
            resp = requests.get(f"{VWORLD_BASE_URL}/req/wfs", params=params, timeout=60)
            resp.raise_for_status()
            page = resp.json()
            page_features = page.get("features", [])
            if not page_features:
                break
            features.extend(page_features)
            start_index += page_size
        logger.info("[DATA] bbox=%s totalFeatures=%d 수신=%d", b, total, len(features))
        return features

    all_features = _fetch_bbox(bbox)
    dedup = {f["id"]: f for f in all_features if "id" in f}
    all_features = list(dedup.values()) if dedup else all_features

    result = {"type": "FeatureCollection", "features": all_features}

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    logger.info("[DATA] 저장 완료: %s (%d개 feature, 중복제거 후)", out_path, len(all_features))
    return out_path


def download_data_go_kr(
    base_endpoint: str,
    operation: str,
    query_params: dict[str, Any],
    out_path: str | Path,
) -> Path:
    """공공데이터포털(data.go.kr) API 공통 호출 함수.

    base_endpoint / operation은 data.go.kr에서 실제로 신청/승인받은
    서비스의 정확한 값을 사용해야 한다(버전이 자주 바뀌므로 임의로
    하드코딩하지 않는다). 예: 건축물대장정보 서비스 신청 후 마이페이지에서
    제공되는 활용신청 상세정보의 Endpoint를 그대로 사용한다.

    Args:
        base_endpoint: data.go.kr에서 제공한 서비스 base URL.
        operation: 오퍼레이션명 (예: getBrBasisOulnInfo 등, 서비스 문서 확인 필요).
        query_params: 오퍼레이션별 요청 파라미터 (지역코드 등).
        out_path: 저장 경로 (raw XML/JSON 그대로 저장).

    Returns:
        저장된 파일 경로.
    """
    key = _require_key(DATA_GO_KR_API_KEY, "DATA_GO_KR_API_KEY")
    params = {"serviceKey": key, **query_params}
    url = f"{base_endpoint.rstrip('/')}/{operation}"
    logger.info("[DATA] data.go.kr 요청: %s", url)
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    logger.info("[DATA] 저장 완료: %s", out_path)
    return out_path


def download_imagery(*_args: Any, **_kwargs: Any) -> None:
    """정사영상 자동 다운로드 - 현재 미구현.

    국토지리정보원 국토정보플랫폼(map.ngii.go.kr)은 일반적으로 로그인 후
    수동 신청/다운로드가 필요하다. 자동화 가능한 공식 API가 확인되면
    이 함수를 구현한다. 그 전까지는 수동 다운로드 데이터를
    data/raw/imagery/2022, data/raw/imagery/2024 에 배치해야 한다.
    """
    raise NotImplementedError(
        "[DATA] 정사영상은 국토정보플랫폼(map.ngii.go.kr)에서 회원 로그인 후 "
        "전용 대용량 파일전송 프로그램으로만 다운로드 가능함을 확인했습니다 "
        "(스크립트 자동화 불가). 통합검색에서 '고양 창릉'을 검색해 정사영상을 "
        "선택하고 수동으로 받은 TIFF 파일을 data/raw/imagery/2022, "
        "data/raw/imagery/2024 에 넣어주세요."
    )
