"""REQ06 - 필요한 시점에 최신 위성영상 확보 (아카이브 조회 + 신규촬영 요청서).

LH 설문에서 "실제 업무 도입 가치"와 나란히 최고점(4.69/5)을 받은 항목이고,
애로사항 3·4번("현장 상황을 적시에 파악하기 어려움", "최신 영상과
공간데이터 확보가 어려움")에 직접 대응한다.

두 갈래로 나눠 다룬다:

1. **아카이브(무료·즉시)**: Sentinel-2 L2A를 Microsoft Planetary Computer
   STAC API로 조회·수집한다. API Key가 필요 없고 2015년부터 5일 주기로
   존재해, 과거 시점 소급 복원(REQ09)에도 쓰인다. 다만 10m 해상도라
   개별 건물 단위 판독에는 한계가 있다(이 PoC가 실측으로 확인한 사실).
2. **신규 촬영(tasking, 유료·계약 필요)**: 원하는 날짜에 찍어 달라고
   요청하는 것은 SkyWatch 등 상용 사업자와의 계약이 있어야 한다.
   이 모듈은 계약 협의에 바로 쓸 수 있는 **요구사항 문서를 자동 생성**하는
   데까지 담당한다.

**의존성 주의**: Baseline은 `pystac-client` / `planetary-computer` 패키지를
썼지만 ArcGIS Pro 파이썬 환경에는 없다. STAC은 평범한 REST API이고 서명도
단순 GET 한 번이라, 여기서는 `requests`만으로 직접 호출한다 - ArcGIS Pro
환경에 패키지를 추가 설치하면 Pro 자체가 불안정해질 수 있어 피했다.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger("arcpy_pipeline.imagery_tasking")

STAC_SEARCH_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS_SIGN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
DEFAULT_COLLECTION = "sentinel-2-l2a"


def search_archive(
    bbox: list[float],
    date_range: str,
    max_cloud_pct: float = 20.0,
    collection: str = DEFAULT_COLLECTION,
    limit: int = 100,
    timeout: int = 60,
) -> list[dict]:
    """STAC API로 아카이브 장면을 검색한다 (구름량 오름차순).

    Args:
        bbox: [minx, miny, maxx, maxy] (EPSG:4326). 장면 **검색용** 범위이며
            최종 분석 AOI가 아니다 - clip은 반드시 AOI 폴리곤으로 한다.
        date_range: "YYYY-MM-DD/YYYY-MM-DD".
        max_cloud_pct: 허용 최대 구름량(%).
        collection: STAC 컬렉션 id.
        limit: 최대 반환 개수.

    Returns:
        [{"id", "datetime", "cloud_cover", "assets": {band: href}}, ...]
    """
    body = {
        "collections": [collection],
        "bbox": bbox,
        "datetime": date_range,
        "query": {"eo:cloud_cover": {"lt": max_cloud_pct}},
        "limit": limit,
    }
    resp = requests.post(STAC_SEARCH_URL, json=body, timeout=timeout)
    resp.raise_for_status()
    features = resp.json().get("features", [])

    scenes = [
        {
            "id": f["id"],
            "datetime": f["properties"].get("datetime", ""),
            "cloud_cover": float(f["properties"].get("eo:cloud_cover", 100.0)),
            "platform": f["properties"].get("platform"),
            "assets": {k: v.get("href") for k, v in (f.get("assets") or {}).items()},
        }
        for f in features
    ]
    scenes.sort(key=lambda s: s["cloud_cover"])
    logger.info(
        "[IMAGERY] 아카이브 검색 %d건 (bbox=%s, %s, cloud<%.0f%%)",
        len(scenes), bbox, date_range, max_cloud_pct,
    )
    return scenes


def latest_available(
    bbox: list[float],
    within_days: int = 90,
    max_cloud_pct: float = 20.0,
    collection: str = DEFAULT_COLLECTION,
) -> dict | None:
    """"지금 당장 쓸 수 있는 가장 최신 영상"을 찾는다 (REQ06의 핵심).

    현장 상황을 적시에 파악하려면 "최근 N일 안에 구름 적은 장면이 있는가"에
    바로 답할 수 있어야 한다.

    Returns:
        가장 최근 장면 dict, 없으면 None.
    """
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=within_days)
    scenes = search_archive(
        bbox, f"{start.isoformat()}/{today.isoformat()}", max_cloud_pct, collection
    )
    if not scenes:
        logger.warning("[IMAGERY] 최근 %d일 내 조건에 맞는 장면 없음", within_days)
        return None
    newest = max(scenes, key=lambda s: s["datetime"])
    age = (today - datetime.fromisoformat(newest["datetime"].replace("Z", "+00:00")).date()).days
    newest["age_days"] = age
    logger.info(
        "[IMAGERY] 최신 가용 영상: %s (%s, 구름 %.1f%%, %d일 전)",
        newest["id"], newest["datetime"][:10], newest["cloud_cover"], age,
    )
    return newest


def sign_href(href: str, timeout: int = 30) -> str:
    """Planetary Computer의 서명된 다운로드 URL을 받는다.

    SAS 토큰은 약 1시간 후 만료되므로 **밴드마다 새로 서명**해야 한다
    (Baseline에서 긴 다운로드 도중 만료되는 문제를 실제로 겪고 고친 부분).
    """
    resp = requests.get(SAS_SIGN_URL, params={"href": href}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()["href"]


def download_bands(
    scene: dict,
    bands: list[str],
    out_dir: str | Path,
    timeout: int = 300,
) -> list[Path]:
    """장면의 지정 밴드를 내려받는다 (이미 받은 파일은 건너뜀).

    이어받기 가능하도록 밴드 단위로 서명·저장한다. 각 파일은 먼저
    `<band>.tif.part`에 스트리밍하고, 전체가 성공적으로 끝난 뒤에만
    최종 파일명으로 원자적 rename한다 - 스트리밍 도중 연결이 끊기면(실측
    확인: 큰 전체타일 COG 다운로드 중 `ConnectionResetError`가 실제로
    발생했다) `.part` 파일만 잘린 채로 남고 최종 경로는 만들어지지 않으므로,
    "파일 존재 = 완전히 받음"이라는 스킵 조건이 항상 참으로 유지된다.
    이 보장이 없으면 재실행 시 잘린 파일을 "이미 있음"으로 건너뛰어
    손상된 밴드로 조용히 스택을 만들게 된다.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for band in bands:
        href = scene["assets"].get(band)
        if not href:
            raise KeyError(f"[IMAGERY] '{band}' 밴드가 장면에 없습니다: {scene['id']}")
        out_path = out_dir / f"{scene['id']}_{band}.tif"
        if out_path.exists():
            logger.info("[IMAGERY] 이미 존재, 건너뜀: %s", out_path.name)
            saved.append(out_path)
            continue
        tmp_path = out_path.with_suffix(out_path.suffix + ".part")
        logger.info("[IMAGERY] 다운로드: %s %s", scene["id"], band)
        try:
            with requests.get(sign_href(href), stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            tmp_path.replace(out_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        saved.append(out_path)
    return saved


def availability_report(
    bbox: list[float],
    years: list[int],
    max_cloud_pct: float = 20.0,
    season: tuple[int, int] | None = (4, 6),
) -> list[dict]:
    """연도별 영상 가용성 표 (계절 일치 장면 우선).

    변화탐지에서 T1/T2의 계절을 맞추는 것은 선택이 아니라 필수다. 식생
    상태와 태양고도가 달라지면 건물이 안 바뀌어도 화소값이 크게 변해
    오탐이 급증하기 때문이다. season을 주면 그 월 범위 안의 장면만 센다.

    Args:
        bbox: 검색 bbox (WGS84).
        years: 조회할 연도들.
        max_cloud_pct: 허용 구름량.
        season: (시작월, 종료월). None이면 연중 전체.

    Returns:
        연도별 dict 목록.
    """
    rows = []
    for year in years:
        if season:
            m1, m2 = season
            last_day = 30 if m2 in (4, 6, 9, 11) else 31
            rng = f"{year}-{m1:02d}-01/{year}-{m2:02d}-{last_day}"
        else:
            rng = f"{year}-01-01/{year}-12-31"
        try:
            scenes = search_archive(bbox, rng, max_cloud_pct)
        except Exception as e:
            logger.warning("[IMAGERY] %d년 조회 실패: %s", year, e)
            rows.append({"year": year, "window": rng, "scene_count": None,
                         "best_date": None, "best_cloud_pct": None, "status": "조회실패"})
            continue
        best = scenes[0] if scenes else None
        rows.append({
            "year": year,
            "window": rng,
            "scene_count": len(scenes),
            "best_date": best["datetime"][:10] if best else None,
            "best_cloud_pct": round(best["cloud_cover"], 2) if best else None,
            "best_scene_id": best["id"] if best else None,
            "status": "가용" if best else "해당 조건 장면 없음",
        })
    return rows


def build_tasking_request(
    out_path: str | Path,
    aoi_name: str,
    aoi_area_km2: float,
    measured: dict,
    target_dates: list[str],
    baseline_date: str | None = None,
) -> Path:
    """상용 위성 신규촬영(tasking) 요구사항 문서를 자동 생성한다.

    이 PoC가 **실측으로 확인한 수치**를 근거로 요구 사양을 도출한다.
    "고해상도가 필요하다"는 주장보다 "평균 변화면적이 N m²로 나와
    개별 건물이 분리되지 않았다"는 실측치가 협상에서 훨씬 강하다.

    Args:
        out_path: 저장할 markdown 경로.
        aoi_name: 대상 지구명.
        aoi_area_km2: AOI 면적.
        measured: {"mean_change_area_m2", "polygon_count", "gsd_m",
                   "min_detectable_note"} 등 실측 결과.
        target_dates: 요청 촬영 시점 목록.
        baseline_date: 보상 기준일 (있으면 촬영 시점 근거로 명시).

    Returns:
        저장된 파일 경로.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    mean_area = measured.get("mean_change_area_m2")
    gsd = measured.get("gsd_m", 10.0)
    lines = [
        "# 상용 위성영상 신규촬영(Tasking) 요구사항",
        "",
        f"- **작성일**: {date.today().isoformat()}",
        f"- **대상 지구**: {aoi_name} ({aoi_area_km2:.2f} km²)",
        "- **작성 근거**: 본 PoC의 Sentinel-2 Baseline 실측 결과",
        "",
        "## 1. 왜 상용 영상이 필요한가 (실측 근거)",
        "",
        f"- 무료 아카이브(Sentinel-2, GSD {gsd:g}m)로 검출된 변화 폴리곤은 "
        f"{measured.get('polygon_count', 'N/A')}개, "
        f"**평균 면적 {mean_area:,.0f} m²**였다."
        if mean_area else "- 평균 변화면적 실측치 미산출 (파이프라인 실행 후 자동 반영).",
        f"- 이는 대형 단지·대규모 토지조성 규모만 분리된다는 뜻이며, 개별 "
        f"단독주택 단위(수십~수백 m²)는 {gsd:g}m 픽셀 안에 묻혀 원천적으로 "
        "분리되지 않는다.",
        f"- 후처리 최소면적 임계 중 한 픽셀 면적({gsd * gsd:.0f} m²)보다 작은 "
        "값들은 현재 해상도에서 애초에 성립할 수 없는 조건이었다.",
        "- 역으로, 광역 1차 스크리닝에는 무료 영상이 충분히 유효하다. "
        "**상용 영상은 1차 스크리닝으로 좁혀진 구역의 정밀 확인에 투입**하는 것이 "
        "비용 대비 효과가 크다.",
        "",
        "## 2. 요구 사양",
        "",
        "| 항목 | 요구 수준 | 근거 |",
        "|---|---|---|",
        "| 공간해상도(GSD) | **0.5m 이하 권장, 1m 이하 필수** | 개별 건물 외곽 분리에 최소 10~20배 세밀도 필요 |",
        "| 정사보정 | 정사보정 완료본 | 건물 옆면 겹침이 footprint 오차를 유발 |",
        "| Off-nadir | 15도 이내 권장 | 고층 건물 기울어짐 최소화 |",
        "| 밴드 | RGB + NIR 이상 | NIR은 식생/나대지 구분에 필수 |",
        "| 구름량 | 5% 미만 | 본 PoC 기준(T1 0.01%, T2 2.1%)과 정합 |",
        "| 계절 | 기존 T1/T2와 동일 계절(4~6월) | 식생·광량 차이로 인한 오탐 억제 |",
        "| 포맷 | GeoTIFF (COG 권장) | 파이프라인이 임의 CRS를 EPSG:5186으로 재투영 |",
        "| API | STAC 또는 이에 준하는 REST | 기존 수집 모듈 구조 재사용 |",
        "",
        "## 3. 요청 촬영 시점",
        "",
    ]
    for d in target_dates:
        lines.append(f"- {d}")
    if baseline_date:
        lines += [
            "",
            f"> **보상 기준일({baseline_date}) 전후 비교**가 목적이라면, 기준일 직전과 "
            "직후를 각각 포함하도록 촬영 시점을 잡아야 한다. 기준일을 걸치는 "
            "한 쌍의 영상만으로는 변화가 기준일 전인지 후인지 특정할 수 없다.",
        ]

    lines += [
        "",
        "## 4. 확인 필요 상업 조건",
        "",
        f"- $/km² 단가, 최소 주문 면적 (본 AOI {aoi_area_km2:.2f} km² 규모 소량 주문 가능 여부)",
        "- 아카이브 vs 신규촬영(tasking) 단가 차이",
        "- 다시기(멀티템포럴) 묶음 할인 여부",
        "- 촬영 요청 후 인도까지 소요기간(latency) — 적시성이 핵심 요구사항",
        "- PoC/평가판 영상 지원 여부",
        "",
        "## 5. 도입 후 비교 검증 계획",
        "",
        "동일 AOI·동일 파이프라인에 상용 영상을 투입해 다음을 직접 비교한다.",
        "",
        "| 지표 | 현재(Sentinel-2) | 상용영상 |",
        "|---|---|---|",
        f"| 공간해상도 | {gsd:g} m | 요청 사양 |",
        f"| 평균 검출 변화면적 | {f'{mean_area:,.0f} m²' if mean_area else 'N/A'} | 재측정 |",
        "| 건물 단위 재현율(recall) | 측정 불가(해상도 한계) | 재측정 |",
        "| 오탐률 | Human Validation Sample 기준 | 재측정 |",
        "| 인도 소요기간 | 아카이브 즉시 | 재측정 |",
        "| km²당 비용 | 0 (무료) | 재측정 |",
        "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[IMAGERY] Tasking 요구사항 문서 생성: %s", out_path)
    return out_path
