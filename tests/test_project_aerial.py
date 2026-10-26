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
