from pathlib import Path

import arcpy

from src.arcpy_pipeline.env import resolve_feature_path


def test_resolve_feature_path_selects_single_geopackage_layer(tmp_path):
    gpkg = tmp_path / "single.gpkg"
    arcpy.management.CreateSQLiteDatabase(str(gpkg), "GEOPACKAGE")
    arcpy.management.CreateFeatureclass(
        str(gpkg), "sample", "POINT", spatial_reference=arcpy.SpatialReference(4326)
    )

    resolved = resolve_feature_path(gpkg)

    assert Path(resolved).parent == gpkg
    assert Path(resolved).name.endswith("sample")
    assert arcpy.Exists(resolved)
