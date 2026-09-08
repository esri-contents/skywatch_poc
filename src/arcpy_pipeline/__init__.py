"""LH 운영 파이프라인 (arcpy 기반).

`src/`의 기존 모듈(geopandas/rasterio 기반)은 공개 오픈소스 스택만으로
재현 가능한 **Baseline**으로 보존한다. 이 패키지는 그것을 LH의 실제 업무
환경(ArcGIS Pro / ArcGIS Online / ArcGIS Reality)에서 운영 가능하도록
arcpy로 다시 구현하고, LH가 제시한 11개 요구 기능을 추가한 **운영 경로**다.

왜 두 벌을 두는가:
- Baseline(src/*)은 ArcGIS 라이선스 없이도 결과를 재현·검증할 수 있어야
  한다(외부 검토, 논문/보고서 근거).
- 운영 경로(src/arcpy_pipeline/*)는 LH 업무 데이터(지적도, 건축물대장)와
  산출물 배포 경로(Web Map, PDF 보고서, 3D)가 전부 ArcGIS 생태계 안에
  있으므로 arcpy 네이티브여야 한다.

두 경로는 동일한 `config/config.yaml`을 읽고 동일한 스키마
(`change_type`, `priority_score`, `site_id` 등)를 산출하므로 결과를
직접 대조할 수 있다.

LH 요구 기능 ↔ 모듈 대응은 `config/lh_requirements.yaml`에 기계가 읽을 수
있는 형태로 기록되어 있고, `pipeline.py`가 실행 시 이 매트릭스를 검증한다.
"""
