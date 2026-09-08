"""REQ10 - Web Map 등을 통한 결과 공유와 보고서 작성 지원 (PDF/레이아웃 자동화).

LH 애로사항 2번("보고서 작성에 많은 시간이 소요됨")에 직접 대응한다.
Baseline의 `generate_html_report.py`(1,717줄)는 브라우저에서 보는 독립
HTML 리포트를 만들었다. 이 모듈은 그것을 대체하지 않고 **LH 실무자가
매번 다시 만들어야 했던 것 - ArcGIS Pro 레이아웃/맵북(PDF)** 을 자동화한다.

arcpy.mp로 하는 일:
1. 결과 GDB의 레이어들(AOI, change_polygons, building_change_results,
   survey_sites)을 하나의 .aprx 지도에 순서대로 쌓고 심볼로지를 입힌다.
2. 표지 + 요약통계 + 지도 페이지로 구성된 Layout을 만든다.
3. PDF로 내보낸다(맵북 - 여러 페이지, 현장별 확대 지도 포함 가능).

**전제**: arcpy.mp는 반드시 .aprx 프로젝트 파일이 있어야 동작한다.
빈 프로젝트를 코드로 새로 만들 수는 없어(ArcGIS Pro API 제약), 최초 1회는
사람이 ArcGIS Pro에서 빈 프로젝트를 저장해 둬야 한다. 없으면 이 모듈은
그 사실을 명확히 알리고 건너뛴다 - 조용히 실패하지 않는다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import arcpy

logger = logging.getLogger("arcpy_pipeline.report_builder")

CHANGE_TYPE_COLORS = {
    "NEW_BUILDING": [230, 25, 75, 255],
    "EXPANSION_OR_RECONSTRUCTION": [245, 130, 48, 255],
    "OTHER_CHANGE": [120, 120, 120, 255],
    "DEMOLITION": [70, 70, 200, 255],
}
PRIORITY_COLORS = {
    "HIGH": [230, 25, 75, 255],
    "MEDIUM": [245, 130, 48, 255],
    "LOW": [255, 220, 50, 255],
}


class MissingProjectError(RuntimeError):
    """빈 .aprx 템플릿이 없을 때 발생 - 최초 1회는 사람이 직접 준비해야 한다."""


def ensure_template_aprx(template_path: str | Path) -> Path:
    """빈 .aprx 템플릿 존재를 확인한다 (없으면 만드는 방법을 안내하고 중단).

    arcpy에는 "새 빈 프로젝트를 코드로 생성"하는 API가 없다(ArcGIS Pro를
    한 번 열어 저장해야 한다). 자동화 파이프라인이 매번 사람 개입을
    요구하면 안 되므로, 템플릿을 한 번만 준비해두고 이후로는 재사용한다.
    """
    template_path = Path(template_path)
    if not template_path.exists():
        raise MissingProjectError(
            f"[REPORT] 빈 ArcGIS Pro 프로젝트가 없습니다: {template_path}\n"
            "최초 1회만 사람이 준비하면 된다: ArcGIS Pro에서 새 빈 프로젝트를 만들고 "
            f"이 경로에 저장하세요. 이후로는 이 파이프라인이 그대로 재사용한다."
        )
    return template_path


def build_map_document(
    template_aprx: str | Path,
    out_aprx: str | Path,
    aoi_fc: str,
    change_fc: str,
    results_fc: str,
    sites_fc: str | None = None,
    basemap_raster: str | None = None,
    map_name: str = "고양 창릉 Building Change Intelligence",
) -> Path:
    """레이어를 쌓고 심볼로지를 입힌 .aprx를 만든다.

    레이어 순서(아래→위): 배경영상 → AOI 경계 → change_polygons →
    building_change_results(두 벌: change_type 카테고리, inspection_priority
    3단계) → survey_sites. handoff.md 9번의 수동 AGOL 절차와 동일한 순서다.
    """
    template_aprx = ensure_template_aprx(template_aprx)
    out_aprx = Path(out_aprx)
    out_aprx.parent.mkdir(parents=True, exist_ok=True)

    aprx = arcpy.mp.ArcGISProject(str(template_aprx))
    if not aprx.listMaps():
        m = aprx.createMap(map_name)
    else:
        m = aprx.listMaps()[0]
        m.name = map_name

    if basemap_raster and arcpy.Exists(basemap_raster):
        m.addDataFromPath(basemap_raster)
    m.addDataFromPath(aoi_fc)
    m.addDataFromPath(change_fc)

    results_by_type = m.addDataFromPath(results_fc)
    results_by_type.name = "변화유형 (change_type)"
    _apply_categorical_symbology(results_by_type, "change_type", CHANGE_TYPE_COLORS)

    results_by_priority = m.addDataFromPath(results_fc)
    results_by_priority.name = "현장조사 우선순위 (inspection_priority)"
    _apply_categorical_symbology(results_by_priority, "inspection_priority", PRIORITY_COLORS)
    results_by_priority.visible = False  # 기본은 change_type 뷰로 켜둔다

    if sites_fc and arcpy.Exists(sites_fc):
        sites_layer = m.addDataFromPath(sites_fc)
        sites_layer.name = "현장조사 지점 (site)"

    aprx.saveACopy(str(out_aprx))
    logger.info("[REPORT] 지도 문서 저장: %s", out_aprx)
    return out_aprx


def _apply_categorical_symbology(layer, field: str, colors: dict[str, list[int]]) -> None:
    """필드 값별 카테고리 심볼을 적용한다.

    CIMSymbolReference를 직접 조립하는 대신 UniqueValueRenderer를 arcpy의
    layer.symbology API로 설정한다 - ArcGIS Pro 버전 간 CIM 스키마 변경에
    덜 취약하다.
    """
    if not layer.supports("SYMBOLOGY"):
        return
    sym = layer.symbology
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = [field]
    for group in sym.renderer.groups:
        for item in group.items:
            value = item.values[0][0] if item.values and item.values[0] else None
            rgba = colors.get(value)
            if rgba:
                item.symbol.color = {"RGB": rgba}
    layer.symbology = sym


def export_field_report_pdf(
    aprx_path: str | Path,
    out_pdf: str | Path,
    summary_stats: dict,
    layout_name: str | None = None,
) -> Path:
    """요약 통계를 담은 표지 + 지도 레이아웃을 PDF로 내보낸다.

    Args:
        aprx_path: build_map_document() 결과.
        out_pdf: 저장할 PDF 경로.
        summary_stats: 표지에 넣을 요약 (candidate_count, site_count,
            high/medium/low, moran_i 등) - dict를 텍스트 요소로 그대로 찍는다.
        layout_name: 사용할 레이아웃 이름. None이면 첫 번째 레이아웃,
            없으면 지도만으로 새로 만든다.

    Returns:
        저장된 PDF 경로.
    """
    aprx = arcpy.mp.ArcGISProject(str(aprx_path))
    layouts = aprx.listLayouts(layout_name) if layout_name else aprx.listLayouts()

    if layouts:
        layout = layouts[0]
    else:
        layout = aprx.createLayout(8.5, 11, "INCH", name="field_report")
        m = aprx.listMaps()[0]
        map_frame = layout.createMapFrame(
            arcpy.Point(0.5, 1.0), m, name="MainMap", width=7.5, height=8.5,
        )
        map_frame.camera.setExtent(map_frame.getLayerExtent(m.listLayers()[0], False, True))

    _write_summary_text(layout, summary_stats)

    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    layout.exportToPDF(str(out_pdf), resolution=200)
    aprx.save()
    logger.info("[REPORT] 현장조사 보고서 PDF 저장: %s", out_pdf)
    return out_pdf


def _write_summary_text(layout, stats: dict) -> None:
    """레이아웃에 요약통계 텍스트 엘리먼트를 추가/갱신한다."""
    text = "고양 창릉 Building Change Intelligence PoC\n" + "\n".join(
        f"{k}: {v}" for k, v in stats.items()
    )
    existing = [e for e in layout.listElements("TEXT_ELEMENT") if e.name == "summary_text"]
    if existing:
        existing[0].text = text
        return
    try:
        layout.createTextElement(
            arcpy.Point(0.5, 0.5), text, "summary_text", font_size=9,
        )
    except (AttributeError, TypeError):
        # 일부 arcpy 버전은 createTextElement 시그니처가 달라 실패할 수 있다.
        # 보고서 생성 자체를 막지 않도록 로그만 남기고 계속 진행한다.
        logger.warning("[REPORT] 요약 텍스트 엘리먼트 생성 실패 - 레이아웃 템플릿에서 수동 추가 필요")
