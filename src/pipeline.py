"""Goyang Changneung Building Change Intelligence PoC - 전체 파이프라인 진입점.

현재는 실제 데이터(정사영상, 건물통합정보, AOI)가 확보되지 않아 각 단계가
구현되지 않았다. 데이터 확보 후 STEP 7 (Raster Preprocessing)부터 순차적으로
구현한다. 이 스텁은 CLI 인터페이스 형태만 고정해 향후 SkyWatch 등 다른
영상 소스를 그대로 투입할 수 있게 한다.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("pipeline")


def run_change_detection(
    t1_path: str | Path,
    t2_path: str | Path,
    aoi_path: str | Path,
    building_path: str | Path,
    config_path: str | Path = "config/config.yaml",
) -> None:
    """전체 Change Detection 파이프라인 실행.

    Args:
        t1_path: T1(과거) 영상 경로 또는 디렉터리.
        t2_path: T2(현재) 영상 경로 또는 디렉터리.
        aoi_path: 분석 대상 지역(AOI) 벡터 파일 경로.
        building_path: 건물통합정보 벡터 파일 경로.
        config_path: 파라미터 설정 파일 경로.
    """
    for label, p in [("t1", t1_path), ("t2", t2_path), ("aoi", aoi_path), ("buildings", building_path)]:
        if not Path(p).exists():
            raise FileNotFoundError(
                f"[DATA] {label} 경로가 존재하지 않습니다: {p}\n"
                "실제 데이터를 확보한 뒤 다시 실행하세요. 가짜 데이터로 진행하지 않습니다."
            )

    raise NotImplementedError(
        "[PIPELINE] Change Detection 파이프라인은 아직 구현되지 않았습니다. "
        "Phase 1(Data Inventory) 완료 후 STEP 7부터 순차 구현 예정입니다."
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Goyang Changneung Building Change Intelligence PoC")
    parser.add_argument("--t1", required=True, help="T1(과거) 영상 경로")
    parser.add_argument("--t2", required=True, help="T2(현재) 영상 경로")
    parser.add_argument("--buildings", required=True, help="건물통합정보 벡터 경로")
    parser.add_argument("--aoi", required=True, help="AOI 벡터 경로")
    parser.add_argument("--config", default="config/config.yaml", help="설정 파일 경로")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_change_detection(args.t1, args.t2, args.aoi, args.buildings, args.config)


if __name__ == "__main__":
    main()
