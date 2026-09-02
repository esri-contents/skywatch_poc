from types import SimpleNamespace

from src.publish.arcgis_online import (
    CHANGE_TYPE_COLORS,
    PRIORITY_COLORS,
    _operational_layer,
    build_web_map,
)

# src.buildings.classify defines these same four values, but importing that module
# here would pull in geopandas (a heavy geospatial dependency this test doesn't
# otherwise need) - so the literals are duplicated and cross-checked instead.
NEW_BUILDING = "NEW_BUILDING"
DEMOLITION = "DEMOLITION"
EXPANSION_OR_RECONSTRUCTION = "EXPANSION_OR_RECONSTRUCTION"
OTHER_CHANGE = "OTHER_CHANGE"


def _item(item_id="abc123", title="layer", url="https://example.com/FeatureServer"):
    return SimpleNamespace(id=item_id, title=title, url=url, homepage=f"https://example.com/home/{item_id}")


def test_change_type_colors_cover_all_classify_categories():
    assert set(CHANGE_TYPE_COLORS) == {NEW_BUILDING, DEMOLITION, EXPANSION_OR_RECONSTRUCTION, OTHER_CHANGE}


def test_priority_colors_cover_all_three_tiers():
    assert set(PRIORITY_COLORS) == {"HIGH", "MEDIUM", "LOW"}


def test_operational_layer_without_renderer_has_no_layer_definition():
    entry = _operational_layer(_item(), "AOI")
    assert entry["title"] == "AOI"
    assert entry["itemId"] == "abc123"
    assert "layerDefinition" not in entry
    assert "popupInfo" not in entry


def test_operational_layer_with_renderer_sets_unique_value_field():
    entry = _operational_layer(
        _item(), "건물 변화 결과 - 변화유형별",
        renderer_field="change_type", value_colors=CHANGE_TYPE_COLORS,
    )
    renderer = entry["layerDefinition"]["drawingInfo"]["renderer"]
    assert renderer["field1"] == "change_type"
    assert {v["value"] for v in renderer["uniqueValueInfos"]} == set(CHANGE_TYPE_COLORS)


def test_operational_layer_with_popup_fields():
    entry = _operational_layer(_item(), "건물 변화 결과", popup_fields=["change_type", "site_id"])
    field_names = [fi["fieldName"] for fi in entry["popupInfo"]["fieldInfos"]]
    assert field_names == ["change_type", "site_id"]


class _FakeJob:
    def __init__(self, item):
        self._item = item

    def result(self):
        return self._item


class _FakeFolder:
    def add(self, item_properties, **kwargs):
        item = _item(item_id="webmap1", title=item_properties["title"])
        item._add_kwargs = kwargs
        return _FakeJob(item)


class _FakeFolders:
    def __init__(self):
        self.folder = _FakeFolder()

    def get(self, *args, **kwargs):
        return self.folder

    def create(self, *args, **kwargs):
        return self.folder


class _FakeContent:
    def __init__(self):
        self.folders = _FakeFolders()

    def search(self, **kwargs):
        return []


class _FakeGis:
    def __init__(self):
        self.content = _FakeContent()
        self.users = SimpleNamespace(me=SimpleNamespace(username="tester"))


def test_build_web_map_orders_layers_per_handoff_section_9():
    gis = _FakeGis()
    webmap_item = build_web_map(
        gis, "테스트 Web Map",
        imagery_2022_item=_item("img2022", "T1"),
        imagery_2024_item=_item("img2024", "T2"),
        aoi_item=_item("aoi", "AOI"),
        change_polygons_item=_item("poly", "변화 폴리곤"),
        building_change_item=_item("bld", "건물 변화 결과"),
    )
    assert webmap_item.title == "테스트 Web Map"


def test_build_web_map_skips_missing_imagery():
    import json

    gis = _FakeGis()
    captured = {}
    original_add = gis.content.folders.folder.add

    def _capturing_add(item_properties, **kwargs):
        captured["text"] = json.loads(kwargs["text"])
        return original_add(item_properties, **kwargs)

    gis.content.folders.folder.add = _capturing_add
    build_web_map(
        gis, "테스트 Web Map 2",
        imagery_2022_item=None,
        imagery_2024_item=None,
        aoi_item=_item("aoi", "AOI"),
        change_polygons_item=_item("poly", "변화 폴리곤"),
        building_change_item=_item("bld", "건물 변화 결과"),
    )
    titles = [layer["title"] for layer in captured["text"]["operationalLayers"]]
    assert titles == [
        "창릉동 AOI",
        "변화 탐지 폴리곤",
        "건물 변화 결과 - 변화유형별",
        "건물 변화 결과 - 현장조사 우선순위",
    ]
