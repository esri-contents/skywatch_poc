"""파이프라인 실행 재현성 기록 (run_manifest.json).

solafune-sentinel2-change의 run_manifest.json(입력 체크섬, 파라미터, seed,
패키지 버전 기록) 패턴을 차용. "이 결과가 어떤 입력/파라미터/코드 버전으로
나왔는지"를 결과물과 분리해서 남겨, 나중에 파라미터를 바꿔 재실행했을 때
이전 실행과 구분하고 재현할 수 있게 한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

logger = logging.getLogger("manifest")

_TRACKED_PACKAGES = [
    "rasterio", "geopandas", "shapely", "pyproj", "fiona",
    "numpy", "pandas", "opencv-python", "scikit-image", "scikit-learn",
    "libpysal", "esda",
]


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return None


def _package_versions() -> dict[str, str | None]:
    versions = {}
    for pkg in _TRACKED_PACKAGES:
        try:
            versions[pkg] = version(pkg)
        except PackageNotFoundError:
            versions[pkg] = None
    return versions


def build_run_manifest(
    input_paths: dict[str, str | Path],
    params: dict,
    out_path: str | Path,
    seed: int = 42,
) -> Path:
    """입력 파일 체크섬 + 파라미터 + 패키지 버전을 담은 run_manifest.json을 저장한다.

    Args:
        input_paths: {"t1": ..., "t2": ..., "buildings": ..., "aoi": ...} 형태.
            존재하지 않는 파일은 checksum 없이 경로만 기록한다.
        params: 이번 실행에 사용된 파라미터(threshold_method, 실제 임계값,
            ensemble_weights 등)를 그대로 담아 저장.
        out_path: 저장할 run_manifest.json 경로.
        seed: 이번 실행에 사용된 random seed.

    Returns:
        저장된 파일 경로.
    """
    inputs_record = {}
    for label, p in input_paths.items():
        p = Path(p)
        entry = {"path": str(p)}
        if p.exists() and p.is_file():
            entry["sha256"] = _sha256_of_file(p)
        inputs_record[label] = entry

    manifest = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "git_commit": _git_commit(),
        "inputs": inputs_record,
        "params": params,
        "package_versions": _package_versions(),
    }

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    logger.info("[MANIFEST] 저장 완료: %s", out_path)
    return out_path
