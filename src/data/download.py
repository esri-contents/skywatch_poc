"""공공데이터 자동 수집 클라이언트.

이 모듈은 API로 자동 수집이 가능한 데이터만 다룬다.
- VWorld 오픈API (건물통합정보 등 공간정보)
- 공공데이터포털(data.go.kr) API (건축물대장정보 등 행정정보)

정사영상(항공사진)은 국토지리정보원 국토정보플랫폼에서 로그인 후
수동으로 신청/다운로드해야 하는 경우가 대부분이며, 자동화 가능한
공개 API가 확인되기 전까지는 이 모듈에서 다루지 않는다.
확인되는 즉시 imagery 다운로드 함수를 추가한다.

모든 요청은 .env 에 저장된 API Key를 사용한다. Key를 코드에
하드코딩하지 않는다.
"""

from __future__ import annotations

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


def download_vworld_wfs_layer(
    layer_name: str,
    bbox: tuple[float, float, float, float],
    out_path: str | Path,
    bbox_crs: str = "EPSG:4326",
    output_format: str = "GML3",
) -> Path:
    """VWorld WFS 서비스에서 지정한 layer를 bbox 범위로 다운로드한다.

    layer_name 은 VWorld 개발자센터에서 해당 API Key로 승인받은
    데이터 서비스 목록에서 정확한 레이어명(예: 건물통합정보 관련 레이어)을
    확인한 뒤 지정해야 한다. 레이어명을 임의로 추정하지 않는다.

    Args:
        layer_name: VWorld에 등록된 정확한 레이어명.
        bbox: (minx, miny, maxx, maxy).
        out_path: 저장할 파일 경로.
        bbox_crs: bbox 좌표계.
        output_format: WFS 응답 포맷.

    Returns:
        저장된 파일 경로.
    """
    key = _require_key(VWORLD_API_KEY, "VWORLD_API_KEY")
    params: dict[str, Any] = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAME": layer_name,
        "BBOX": ",".join(str(v) for v in bbox),
        "SRSNAME": bbox_crs,
        "OUTPUT": output_format,
        "KEY": key,
    }
    logger.info("[DATA] VWorld WFS 요청: layer=%s bbox=%s", layer_name, bbox)
    resp = requests.get(f"{VWORLD_BASE_URL}/req/wfs", params=params, timeout=60)
    resp.raise_for_status()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    logger.info("[DATA] 저장 완료: %s", out_path)
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
        "[DATA] 정사영상 자동 다운로드 API가 아직 확인되지 않았습니다. "
        "국토지리정보원 국토정보플랫폼에서 수동으로 다운로드한 파일을 "
        "data/raw/imagery/2022 및 data/raw/imagery/2024 에 넣어주세요."
    )
