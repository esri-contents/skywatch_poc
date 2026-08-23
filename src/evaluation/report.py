"""Human Validation Sample 생성 (STEP 23)."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

logger = logging.getLogger("report")


def build_human_validation_sample(
    results_path: str | Path,
    out_path: str | Path,
    target_n: dict[str, int] = None,
    random_state: int = 42,
) -> pd.DataFrame:
    """등급별 계층표본을 추출해 사람이 검수할 CSV를 만든다.

    현재 PoC AOI의 실제 후보 수가 목표 표본 수(HIGH 50/MEDIUM 30/LOW 20)보다
    적은 경우, 해당 등급의 후보를 전수 포함한다(계층표본이 아니라 전수조사가 됨).

    Args:
        results_path: building_change_results.gpkg 경로.
        out_path: 저장할 CSV 경로.
        target_n: {"HIGH": 50, "MEDIUM": 30, "LOW": 20} 형태의 목표 표본 수.
        random_state: 표본 추출 시드.

    Returns:
        저장된 표본 DataFrame.
    """
    target_n = target_n or {"HIGH": 50, "MEDIUM": 30, "LOW": 20}
    gdf = gpd.read_file(results_path)

    parts = []
    for tier, n in target_n.items():
        pool = gdf[gdf["inspection_priority"] == tier]
        sample_n = min(n, len(pool))
        if sample_n < len(pool):
            sampled = pool.sample(n=sample_n, random_state=random_state)
        else:
            sampled = pool
        logger.info("[EVAL] %s: 목표 %d건 중 %d건 (모집단 %d건)", tier, n, sample_n, len(pool))
        parts.append(sampled)

    sample = pd.concat(parts, ignore_index=True) if parts else gdf.iloc[0:0]

    building_id = sample["pnu"] if "pnu" in sample.columns else None
    out_df = pd.DataFrame({
        "change_id": sample.get("change_id"),
        "building_id": building_id,
        "predicted_class": sample.get("change_type"),
        "confidence": sample.get("confidence"),
        "priority_score": sample.get("priority_score"),
        "inspection_priority": sample.get("inspection_priority"),
        "manual_class": "",
        "is_correct": "",
        "comment": "",
    })

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)
    logger.info("[EVAL] Human Validation Sample 저장: %s (%d건)", out_path, len(out_df))
    return out_df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    build_human_validation_sample(
        "outputs/vectors/building_change_results.gpkg",
        "outputs/reports/human_validation_sample.csv",
    )
