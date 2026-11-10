"""공공데이터 자동 수집 클라이언트.

이 모듈은 API로 자동 수집이 가능한 데이터만 다룬다. 확인된 공식 서비스:
- VWorld WFS `lt_c_spbd` (도로명주소건물) - 실제 호출로 검증됨 (건물 footprint,
  층수, PNU 등 제공. 연면적/사용승인일 등 상세 건축물대장 속성은 없음).
- 공공데이터포털(data.go.kr) 국토교통부_GIS건물통합정보(WMS/WFS)
  https://www.data.go.kr/data/15123970/openapi.do - 아직 실제 호출 검증 안 됨
  (NO_OPENAPI_SERVICE_ERROR, 활용신청 상태 확인 필요).
- 공공데이터포털(data.go.kr) 국토교통부_건축HUB_건축물대장정보 서비스
  https://www.data.go.kr/data/15134735/openapi.do - 정확한 엔드포인트 확인됨:
  https://apis.data.go.kr/1613000/BldRgstHubService/getBrBasisOulnInfo
  (raw HTML에서 확인, blog 등에서 흔히 보이는 1611000/BldRgstService는
  구버전으로 추정). 단, 이 키는 아직 해당 서비스에 활용신청이 승인되지
  않음 - 실제 호출 시 SERVICE_KEY_IS_NOT_REGISTERED_ERROR(reasonCode 30)
  반환 확인. data.go.kr 마이페이지에서 15134735 활용신청 후 재시도 필요.

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
import time
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
# 연속지적도(필지 경계). VWorld WFS GetCapabilities 응답에서 실제로 확인됨
# (2026-09-01) - 본번(대표 필지)과 부번(분할된 필지)이 별도 레이어로 나뉘어
# 있어 전체 필지를 얻으려면 둘 다 받아야 한다.
VWORLD_CADASTRE_LAYERS = ["lp_pa_cbnd_bonbun", "lp_pa_cbnd_bubun"]


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


# GIS건물통합정보(국토부 AL_D010) - VWorld 국가중점데이터(NED) WFS. 건물 윤곽마다 PNU, 건축물대장 연계 번호
# (buld_idntfc_no = 대장 관리번호), 위반건축물 여부(violt_bild), 용도·구조·사용승인일이 붙는다.
# 2026-11-10 확인: EPSG:5186 좌표 bbox에 ",EPSG:5186" 접미사를 붙여야 결과가 나오고(4326은 0건),
# 요청당 maxFeatures 상한 1000·startIndex 미지원으로 보여 넘치면 bbox를 4분할한다.
VWORLD_NED_BUILDING_URL = f"{VWORLD_BASE_URL}/ned/wfs/getBldgisSpceWFS"
VWORLD_NED_BUILDING_TYPENAME = "dt_d010"


def download_vworld_gis_buildings(bbox_5186: tuple[float, float, float, float], out_path: str | Path,
                                  page_size: int = 1000, min_size_m: float = 50.0) -> Path:
    """GIS건물통합정보를 EPSG:5186 bbox 범위로 전량 받아 GeoJSON으로 저장한다(gis_idntfc_no로 중복 제거)."""
    key = _require_key(VWORLD_API_KEY, "VWORLD_API_KEY")

    def _get(b):
        params = {"typename": VWORLD_NED_BUILDING_TYPENAME, "bbox": ",".join(f"{v:.2f}" for v in b) + ",EPSG:5186",
                  "srsName": "EPSG:5186", "maxFeatures": page_size, "resultType": "results",
                  "output": "application/json", "key": key, "domain": "localhost"}
        for attempt in range(6):
            try:
                r = requests.get(VWORLD_NED_BUILDING_URL, params=params, timeout=60)
                r.raise_for_status()
                return r.json()
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2 * (attempt + 1))

    def _fetch(b) -> list[dict[str, Any]]:
        page = _get(b)
        feats = page.get("features", [])
        total = page.get("totalFeatures") or page.get("numberMatched") or len(feats)
        if total > len(feats) and (b[2] - b[0]) > min_size_m:
            minx, miny, maxx, maxy = b
            mx, my = (minx + maxx) / 2, (miny + maxy) / 2
            out: list[dict[str, Any]] = []
            for q in ((minx, miny, mx, my), (mx, miny, maxx, my), (minx, my, mx, maxy), (mx, my, maxx, maxy)):
                out.extend(_fetch(q))
            return out
        if total > len(feats):
            logger.warning("[DATA] 건물통합정보 bbox=%s %d건 중 %d건만 받음(더 나눌 수 없음)", b, total, len(feats))
        return feats

    seen: dict[str, dict[str, Any]] = {}
    for f in _fetch(bbox_5186):
        k = str((f.get("properties") or {}).get("gis_idntfc_no") or f.get("id"))
        seen.setdefault(k, f)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection", "features": list(seen.values()),
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}}}
    out_path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    logger.info("[DATA] 건물통합정보 저장: %s (%d동)", out_path, len(seen))
    return out_path


BLD_RGST_HUB_BASE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService"
# raw HTML에서 확인된 정확한 엔드포인트. 활용신청 승인 전까지는
# SERVICE_KEY_IS_NOT_REGISTERED_ERROR(reasonCode 30)가 반환된다.


def download_building_register(
    sigungu_cd: str,
    bjdong_cd: str,
    out_path: str | Path,
    operation: str = "getBrBasisOulnInfo",
    num_of_rows: int = 100,
) -> Path:
    """건축HUB 건축물대장정보 서비스 호출 (법정동 단위 기본개요 조회).

    data.go.kr에서 15134735 서비스 활용신청이 승인된 이후에만 정상 동작한다.

    Args:
        sigungu_cd: 시군구코드 5자리 (예: 고양시덕양구="41281").
        bjdong_cd: 법정동코드 5자리 (예: 원흥동="10400").
        out_path: 저장 경로.
        operation: 오퍼레이션명 (기본: 기본개요 조회).
        num_of_rows: 페이지당 결과 수.

    Returns:
        저장된 파일 경로.
    """
    return download_data_go_kr(
        BLD_RGST_HUB_BASE_URL, operation,
        {"sigunguCd": sigungu_cd, "bjdongCd": bjdong_cd, "numOfRows": num_of_rows, "pageNo": 1, "_type": "json"},
        out_path,
    )


def fetch_building_title_info(
    sigungu_cd: str,
    bjdong_cd: str,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """getBrTitleInfo(표제부 - 연면적/사용승인일/주용도 등)를 법정동 전체 페이징 조회."""
    return fetch_building_register(sigungu_cd, bjdong_cd, "getBrTitleInfo", page_size)


def fetch_building_register(
    sigungu_cd: str,
    bjdong_cd: str,
    operation: str = "getBrTitleInfo",
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """건축HUB 건축물대장 정보 서비스(operation)를 법정동 전체 페이징 조회.

    operation 예: getBrTitleInfo(표제부), getBrBasisOulnInfo(기본개요 - 모든 대장 종류),
    getBrAtchJibunInfo(부속지번 - 대장의 대표지번 밖에 걸친 필지).

    각 결과 item에 sigunguCd+bjdongCd+platGbCd+bun+ji로 PNU(19자리)를
    재구성해 붙여, VWorld 건물 layer의 'pnu' 컬럼과 바로 join할 수 있게 한다.

    Args:
        sigungu_cd: 시군구코드 5자리.
        bjdong_cd: 법정동코드 5자리.
        page_size: 페이지당 결과 수.

    Returns:
        item(dict) 리스트. 각 item에 "pnu" 키가 추가되어 있다.
    """
    key = _require_key(DATA_GO_KR_API_KEY, "DATA_GO_KR_API_KEY")
    url = f"{BLD_RGST_HUB_BASE_URL}/{operation}"
    all_items: list[dict[str, Any]] = []
    page_no = 1
    while True:
        params = {
            "sigunguCd": sigungu_cd, "bjdongCd": bjdong_cd,
            "numOfRows": page_size, "pageNo": page_no,
            "ServiceKey": key, "_type": "json",
        }
        # 건축HUB는 503·빈 응답을 가끔 돌려준다 - 법정동을 처음부터 다시 받지 않도록 페이지 단위로 재시도
        for attempt in range(6):
            try:
                resp = requests.get(url, params=params, timeout=45)
                resp.raise_for_status()
                body = resp.json()["response"]["body"]
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(2 * (attempt + 1))
        total = int(body.get("totalCount", 0))
        items = body.get("items") or {}
        page_items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(page_items, dict):
            page_items = [page_items]

        for it in page_items:
            # platGbCd(0=대지, 1=산)와 PNU 11번째 자리(1=일반, 2=산)는 코드 체계가 다르다
            # (project_acquisition.acquire_building_permits와 같은 변환).
            plat = {"0": "1", "1": "2"}.get(str(it.get("platGbCd", "0")), str(it.get("platGbCd", "0")))
            it["pnu"] = (
                f"{it.get('sigunguCd','')}{it.get('bjdongCd','')}"
                f"{plat}{str(it.get('bun','0')).zfill(4)}{str(it.get('ji','0')).zfill(4)}"
            )
        all_items.extend(page_items)

        logger.info(
            "[DATA] 건축물대장 %s: bjdong=%s page=%d 누적=%d/%d",
            operation, bjdong_cd, page_no, len(all_items), total,
        )
        if len(all_items) >= total or not page_items:
            break
        page_no += 1

    return all_items


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
