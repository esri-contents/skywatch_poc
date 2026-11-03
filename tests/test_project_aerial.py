"""로컬 항공사진 프로젝트 지원(보고서 문구, 인허가 PNU) 테스트."""

from datetime import date

from src.data import project_acquisition
from src.evaluation.generate_project_deliverables import _epoch_date, _sensor


def test_epoch_date_accepts_year_only():
    assert _epoch_date("2022-12-01") == date(2022, 12, 1)
    assert _epoch_date("2024") == date(2024, 1, 1)
    assert _epoch_date("미상") is None


def test_sensor_defaults_to_sentinel2_and_reads_local_config():
    s2 = _sensor({"imagery": {"collection": "sentinel-2-l2a"}})
    assert (s2["label"], s2["px"], s2["kind"]) == ("Sentinel-2", 10.0, "위성")
    aerial = _sensor({"imagery": {"source": "local", "sensor_label": "항공 정사영상", "resolution_m": 1.0}})
    assert (aerial["label"], aerial["px_area"], aerial["kind"]) == ("항공 정사영상", 1.0, "항공사진")


def test_permit_pnu_maps_plat_code_to_pnu_land_type(monkeypatch, tmp_path):
    """platGbCd(0=대지, 1=산)는 PNU 11번째 자리(1=일반, 2=산)로 바꿔야 지적과 매칭된다."""

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            items = [
                {"sigunguCd": "41210", "bjdongCd": "10100", "platGbCd": "0", "bun": "78", "ji": "0"},
                {"sigunguCd": "41210", "bjdongCd": "10100", "platGbCd": "1", "bun": "5", "ji": "2"},
            ]
            return {"response": {"body": {"items": {"item": items}, "totalCount": 2}}}

    monkeypatch.setenv("DATA_GO_KR_API_KEY", "test")
    monkeypatch.setattr(project_acquisition.requests, "get", lambda *a, **k: _Resp())
    out = tmp_path / "permits.json"
    project_acquisition.acquire_building_permits({"paths": {"building_permits": str(out)}}, ["4121010100"])
    import json

    pnus = [item["pnu"] for item in json.loads(out.read_text(encoding="utf-8"))]
    assert pnus == ["4121010100100780000", "4121010100200050002"]


def test_register_pnu_maps_plat_code_to_pnu_land_type(monkeypatch):
    """건축물대장 표제부도 인허가와 같은 PNU 변환을 써야 지적 PNU와 매칭된다."""
    from src.data import download

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            items = [{"sigunguCd": "41210", "bjdongCd": "10100", "platGbCd": "1", "bun": "5", "ji": "2"}]
            return {"response": {"body": {"items": {"item": items}, "totalCount": 1}}}

    monkeypatch.setattr(download, "_require_key", lambda *a: "test")
    monkeypatch.setattr(download.requests, "get", lambda *a, **k: _Resp())
    assert [i["pnu"] for i in download.fetch_building_title_info("41210", "10100")] == ["4121010100200050002"]


def test_report_flags_imagery_taken_after_baseline():
    from src.evaluation.generate_interpretation_report import _timing
    late, months, text = _timing("2021-02-24", "2022-12-01")
    assert late and months == 21 and "기준일 당시 현황이 아니다" in text
    assert _timing("2022-11-29", "2022-12-01")[0] is False


def test_register_codes_drop_legal_dongs_that_only_touch_the_aoi():
    """경계에 걸친 건물 1동의 PNU로 이웃 시의 법정동 전체를 수집하지 않는다."""
    import geopandas as gpd
    from shapely.geometry import Point, box

    buildings = gpd.GeoDataFrame({"pnu": ["4121010500100010000", "4119410600101910000"]},
                                 geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:5186")
    cadastre = gpd.GeoDataFrame({"pnu": ["4121010500100010000", "4119410600101910000"]},
                                geometry=[box(0, 0, 1000, 1000), box(0, 0, 10, 10)], crs="EPSG:5186")
    codes = project_acquisition.register_legal_dong_codes(buildings, cadastre, aoi_area_m2=1_000_100)
    assert codes == ["4121010500"]
