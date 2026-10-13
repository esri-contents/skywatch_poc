"""AI 자연어 검색 연계용 전달 패키지 생성(Portal MCP / AI API 연계 담당자에게 전달).

만드는 것:
  parcels_ai_view.csv/.gpkg/.geojson  질의용으로 정리한 필지 표(영문 필드명 + 한글 별칭, 약 50개 필드)
  data_dictionary.csv                  필드 사전(한글명, 설명, 단위, 값 목록)
  sample_questions.csv                 예시 질문, 기대 답변(실제 데이터로 계산), 산출 조건
  answering_rules.md                   답변 규칙(해도 되는 말/하면 안 되는 말) - 시스템 프롬프트 초안
  README.md                            파일 설명, 연계 방법별 안내

모든 기대 답변은 이 표에서 실제로 계산한 값이다(하드코딩 없음).
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

TIER_CODE = {"먼저 확인": "FIRST_CHECK", "확인 권장": "RECOMMENDED", "변화 흔적 없음": "NO_CHANGE", "판단 보류": "UNDETERMINED"}
QCTX_KO = {"POTENTIAL_QUARRY_EXPANSION": "채석장 확장 의심", "QUARRY_INTERNAL_CHANGE": "기존 채석장 내부 노출지 증가",
           "EXPOSED_SURFACE_CHANGE": "노출지 변화(채석장 연계 미확인)", "POSSIBLE_REVEGETATION": "식생 회복 가능",
           "VERIFIED_QUARRY_AREA": "채석장 참조 영역", "GENERAL_VEGETATION_LOSS": "일반 식생 감소", "NONE": "해당 없음",
           "NOT_EVALUATED": "평가 보류"}
CONF_KO = {"HIGH": "높음", "MEDIUM": "보통", "LOW": "낮음", "NONE": "판단 보류"}
INTERVALS = ["2016→2018", "2018→2020", "2020→2022", "2022→2024", "2024→2026"]


def _build_table(bsi_dir: Path) -> gpd.GeoDataFrame:
    g = gpd.read_file(bsi_dir / "vectors" / "parcel_ndvi_bsi_stats.gpkg")
    s = pd.read_csv(bsi_dir / "tables" / "survey_screening_parcels.csv", dtype={"PNU": str}, encoding="utf-8-sig")
    g["pnu"] = g["pnu"].astype(str)
    s = s.rename(columns={"PNU": "pnu"}).drop_duplicates("pnu").set_index("pnu")
    out = gpd.GeoDataFrame({
        "pnu": g["pnu"], "ri": g["li_nm"], "jibun": g["jibun_display"], "address": g["addr"], "jimok": g["jimok"], "zoning": g["zoning"],
        "area_m2": g["area_in_aoi_m2"].round(1),
    }, geometry=g.geometry, crs=g.crs)
    t = s.reindex(out["pnu"])
    out["screening_tier_ko"] = t["확인_구분"].to_numpy()
    out["screening_tier"] = out["screening_tier_ko"].map(TIER_CODE)
    for iv, fld in zip(INTERVALS, ["chg_2016_2018", "chg_2018_2020", "chg_2020_2022", "chg_2022_2024", "chg_2024_2026"]):
        out[fld] = t[iv].to_numpy()
    out["summary_ko"] = t["한줄_판단"].to_numpy()
    out["action_ko"] = t["권고"].to_numpy()
    out["building_count"] = t["현재_건축물_수"].to_numpy()
    out["building_footprint_m2"] = t["현재_건축물_윤곽합_㎡"].to_numpy()
    out["robust_both_methods"] = t["두방법_모두_확인"].to_numpy()
    out["agri_context"] = t["농경지_맥락"].to_numpy()
    out["first_change_interval"] = g["first_change_interval_20m"].fillna(
        g["first_change_interval"]).astype(str).str.replace("_", "→").replace({"None": None, "nan": None}).to_numpy()
    out["composite_type_ko"] = g["composite_type_ko"].to_numpy()
    out["ndvi_change_type_ko"] = g["change_type_ko"].to_numpy()
    out["quarry_context"] = g["quarry_context"].to_numpy()
    out["quarry_context_ko"] = g["quarry_context"].map(QCTX_KO).to_numpy()
    out["dist_to_quarry_m"] = g["dist_to_quarry_m"].round(0).to_numpy()
    out["rail_distance_m"] = g["rail_distance_m"].round(0).to_numpy()
    out["within_300m_rail"] = g["within_300m"].to_numpy()
    out["exposed_increase_m2"] = g["exposed_increase_area_20m_m2"].round(0).to_numpy()
    out["veg_decline_m2"] = g["veg_decline_area_20m_m2"].round(0).to_numpy()
    out["veg_recovery_m2"] = g["veg_recovery_area_20m_m2"].round(0).to_numpy()
    out["ndvi10_decline_m2"] = g["decline_area_m2"].round(0).to_numpy()
    for y in (2016, 2018, 2020, 2022, 2024, 2026):
        out[f"ndvi_{y}"] = g[f"ndvi_mean_{y}"].round(3).to_numpy()
    for y in (2016, 2018, 2020, 2022, 2024, 2026):
        out[f"bsi_{y}"] = g[f"bsi_mean_{y}"].round(3).to_numpy()
    out["priority_A_rank"] = g["A_rank"].astype("Int64").to_numpy()
    out["priority_B_rank"] = g["B_rank"].astype("Int64").to_numpy()
    out["confidence_ko"] = g["composite_confidence"].map(CONF_KO).to_numpy()
    out["confusion_risk"] = g["confusion_risk"].fillna("").to_numpy()
    return out


DICT = [
    ("pnu", "PNU", "필지고유번호 19자리(문자열, 앞자리 0 보존)", "", ""),
    ("ri", "리", "법정리 이름(매포리·하괴리 등). **지번은 리마다 중복되므로 지번 조회 시 리 또는 PNU로 특정해야 함**", "", ""),
    ("jibun", "지번", "산 여부와 본번-부번(예: 산 17-26)", "", ""),
    ("address", "주소", "도로명이 아닌 지번 주소", "", ""),
    ("jimok", "지목", "공부상 지목(지번 접미 약칭에서 추출, 현황과 다를 수 있음)", "", "전·답·대·임야·도로 등"),
    ("zoning", "용도지역", "VWorld 용도지역(면적 50% 이상 겹친 것)", "", ""),
    ("area_m2", "필지 면적", "분석 AOI 안에 있는 필지 면적", "㎡", ""),
    ("screening_tier", "확인 구분(코드)", "물건조사 1차 선별 구분", "", "FIRST_CHECK / RECOMMENDED / NO_CHANGE / UNDETERMINED"),
    ("screening_tier_ko", "확인 구분", "먼저 확인=맨땅 증가 또는 식생 감소 지속, 확인 권장=식생 감소·회복 흔적, 변화 흔적 없음, 판단 보류=작은 필지·영상 부족", "", "먼저 확인/확인 권장/변화 흔적 없음/판단 보류"),
    ("chg_2016_2018", "2016→2018 변화", "해당 구간 영상 비교 결과", "", "맨땅 증가/식생 감소/식생 회복/변화 없음/판단 불가"),
    ("chg_2018_2020", "2018→2020 변화", "〃", "", "〃"), ("chg_2020_2022", "2020→2022 변화", "〃", "", "〃"),
    ("chg_2022_2024", "2022→2024 변화", "〃", "", "〃"), ("chg_2024_2026", "2024→2026 변화", "〃", "", "〃"),
    ("first_change_interval", "최초 변화 관측 구간", "변화 시점이 아니라 '처음 변화가 관측된 구간'(약 2년 간격)", "", "예: 2018→2020"),
    ("summary_ko", "한 줄 판단", "쉬운 말로 요약한 판단 문장", "", ""),
    ("action_ko", "권고", "다음 행동 안내", "", ""),
    ("building_count", "현재 건축물 수", "필지 안 도로명주소 건축물 수(가설건축물·비닐하우스 등 누락 가능, 현재 시점)", "동", ""),
    ("building_footprint_m2", "현재 건축물 윤곽 합계", "건축물 윤곽 면적 합(연면적 아님)", "㎡", ""),
    ("robust_both_methods", "두 방법 모두 확인", "단일 장면과 다중 장면 중앙값 분석 모두 후보", "", "True/False"),
    ("agri_context", "농경지 맥락", "지목·산림입지도·영농여건불리농지·토지피복 중 하나라도 농경지 근거(경작 변동 가능)", "", "True/False"),
    ("composite_type_ko", "복합 변화 유형", "NDVI+BSI 20m 기준 필지 유형", "", "노출지 증가 후보/식생 감소 후보/노출 지표 지속 후보/식생 회복 후보/식생 안정/판단 불확실 변화/자료 부족"),
    ("ndvi_change_type_ko", "NDVI 변화 유형", "NDVI 10m 기준 필지 유형", "", ""),
    ("quarry_context", "채석장 맥락(코드)", "OSM 참조 채석장(비공인) 대비 위치·변화 관계", "", "POTENTIAL_QUARRY_EXPANSION 등"),
    ("quarry_context_ko", "채석장 맥락", "〃(한글)", "", ""),
    ("dist_to_quarry_m", "채석장 참조까지 거리", "OSM 참조 채석장 폴리곤까지 최단거리", "m", ""),
    ("rail_distance_m", "철도까지 거리", "OSM 비공식 철도선까지 최단거리(공식 보상구역 아님)", "m", ""),
    ("within_300m_rail", "철도 300m 이내", "철도(OSM) 300m 이내 여부", "", "True/False"),
    ("exposed_increase_m2", "맨땅 증가 면적", "NDVI 감소+BSI 증가(20m). 식생 감소 면적에 포함됨(합산 금지)", "㎡", ""),
    ("veg_decline_m2", "식생 감소 면적(20m)", "노출지 증가 포함", "㎡", ""),
    ("veg_recovery_m2", "식생 회복 면적(20m)", "", "㎡", ""),
    ("ndvi10_decline_m2", "식생 감소 면적(10m)", "NDVI 10m 기준, 20m 값과 정확도가 다름", "㎡", ""),
    ("ndvi_2016", "NDVI 2016 … 2026", "필지 면적가중 평균 NDVI(연도별 6개 필드: ndvi_2016~ndvi_2026)", "-1~1", "연도별 촬영일 상이(4~5월)"),
    ("bsi_2016", "BSI 2016 … 2026", "필지 면적가중 평균 BSI(연도별 6개 필드: bsi_2016~bsi_2026)", "-1~1", "높을수록 노출·건조 지표"),
    ("priority_A_rank", "A 순위", "철도 인접 현장조사 검토 순위(없으면 비어 있음)", "", "정수"),
    ("priority_B_rank", "B 순위", "채석장·노출지 변화 검토 순위(없으면 비어 있음)", "", "정수"),
    ("confidence_ko", "분석 신뢰도", "NDVI+BSI 판정의 신뢰도", "", "높음/보통/낮음/판단 보류"),
    ("confusion_risk", "혼동 위험", "농경지 변동·시설·개발지·산림 관리 가능성 경고(세미콜론 구분)", "", "FARMLAND_VARIATION_POSSIBLE 등"),
]


def _questions(t: pd.DataFrame) -> list[dict]:
    q = []
    first = t[t.screening_tier == "FIRST_CHECK"]
    q.append(("먼저 확인해야 하는 필지는 몇 개인가요?", f"{len(first)}개",
              "screening_tier == 'FIRST_CHECK'", "건수", "일반 집계"))
    iv = t[t.chg_2018_2020 == "맨땅 증가"].sort_values("exposed_increase_m2", ascending=False)
    top = ", ".join(f"{r.jibun}({r.exposed_increase_m2:,.0f}㎡)" for r in iv.head(5).itertuples())
    q.append(("2018년과 2020년 사이에 맨땅이 늘어난 필지는 어디인가요?", f"{len(iv)}개 필지. 면적 상위: {top}",
              "chg_2018_2020 == '맨땅 증가' 를 exposed_increase_m2 내림차순", "목록", "시기 필터"))
    r300 = first[first.within_300m_rail == True]  # noqa: E712
    q.append(("철도 300m 이내이면서 먼저 확인해야 하는 필지는?", f"{len(r300)}개. 철도에 가까운 순: " + ", ".join(
        f"{r.jibun}({r.rail_distance_m:,.0f}m)" for r in r300.sort_values("rail_distance_m").head(5).itertuples()),
        "screening_tier == 'FIRST_CHECK' and within_300m_rail == True", "목록", "복합 조건(철도는 OSM 비공식)"))
    qe = t[t.quarry_context == "POTENTIAL_QUARRY_EXPANSION"].sort_values("exposed_increase_m2", ascending=False)
    q.append(("채석장 확장이 의심되는 필지를 알려주세요.", f"{len(qe)}개: " + ", ".join(f"{r.jibun}({r.exposed_increase_m2:,.0f}㎡)" for r in qe.itertuples())
              + ". 참조 채석장(OSM, 비공인)과 공간적으로 이어진 노출지 증가일 뿐 채석 활동의 확정이 아님",
              "quarry_context == 'POTENTIAL_QUARRY_EXPANSION'", "목록+주의", "반드시 '의심'·'확정 아님' 표현"))
    fb = first[first.building_count > 0]
    q.append(("먼저 확인해야 하는 필지 중 현재 건축물이 있는 곳은 몇 개이고 윤곽 면적 합은?",
              f"{len(fb)}개 필지, 건축물 {int(fb.building_count.sum())}동, 윤곽 합계 약 {fb.building_footprint_m2.sum():,.0f}㎡(도로명주소 건축물 기준)",
              "screening_tier == 'FIRST_CHECK' and building_count > 0", "집계", "건축물 자료 한계 안내 필요"))
    one = t[(t.jibun == "301-14") & (t.ri == "매포리")]
    if len(one):
        r = one.iloc[0]
        q.append(("매포리 301-14 필지의 연도별 변화 이력을 알려주세요.",
                  f"{r.screening_tier_ko}. 2016→2018 {r.chg_2016_2018}, 2018→2020 {r.chg_2018_2020}, 2020→2022 {r.chg_2020_2022}, "
                  f"2022→2024 {r.chg_2022_2024}, 2024→2026 {r.chg_2024_2026}. 현재 건축물 {int(r.building_count)}동. "
                  "이 이력은 식생·맨땅 변화이며 지장물의 생성·소멸 이력이 아님",
                  "ri == '매포리' and jibun == '301-14'", "단건 조회", "이력 표현 시 한계 문구 포함"))
    dup = t[t.duplicated("jibun", keep=False)].jibun.nunique()
    q.append(("202-1 필지는 어떤가요?", "리를 먼저 확인해야 함: 같은 지번이 여러 리에 있을 수 있음(이 자료에서 지번 중복 "
              f"{dup}개). 리 또는 PNU를 받아 조회", "jibun == '202-1' 결과가 2건 이상이면 되묻기", "되묻기", "임의 선택 금지"))
    ag = t[(t.agri_context == True) & (t.screening_tier.isin(["FIRST_CHECK", "RECOMMENDED"]))]  # noqa: E712
    q.append(("농경지로 보이는 필지 중 변화 흔적이 있는 곳은?", f"{len(ag)}개. 경작·수확에 따른 변동일 수 있어 개발 변화로 단정하면 안 됨",
              "agri_context == True and screening_tier in ('FIRST_CHECK','RECOMMENDED')", "목록+주의", "혼동 위험 안내"))
    rb = t[(t.robust_both_methods == True)].sort_values("rail_distance_m")  # noqa: E712
    q.append(("두 가지 분석 방법에서 모두 확인된 필지 중 철도에 가장 가까운 5곳은?", ", ".join(
        f"{r.jibun}({r.rail_distance_m:,.0f}m)" for r in rb.head(5).itertuples()),
        "robust_both_methods == True 를 rail_distance_m 오름차순", "목록", "철도는 OSM 비공식"))
    q.append(("판단 보류 필지가 왜 이렇게 많은가요?", f"{(t.screening_tier == 'UNDETERMINED').sum()}개({(t.screening_tier == 'UNDETERMINED').mean() * 100:.0f}%). "
              "위성영상 한 칸이 10~20m라 필지가 작으면(대략 400㎡ 미만) 판단할 수 없기 때문",
              "screening_tier == 'UNDETERMINED'", "설명", "규칙 기반 설명"))
    q.append(("불법 건축물이 있는 필지는 어디인가요?", "답변 불가. 이 자료는 불법 여부나 건축물의 신축·철거를 판단하지 않음. 변화 흔적이 있는 필지의 '확인 대상'만 안내 가능",
              "-", "거절+대안", "금지 표현: 불법/무허가 확정"))
    q.append(("이 필지는 보상 대상인가요?", "답변 불가. 보상 적격성은 판단하지 않음. 철도선은 OSM 비공식 자료이며 공식 사업구역이 아님",
              "-", "거절", "금지 표현: 보상 대상 확정"))
    q.append(("이 필지에 건물이 언제 생겼나요?", "알 수 없음. 현재 시점 건축물 윤곽만 있고 이력이 없음. 연도별 이력은 고해상도 영상 또는 건축물대장 연계가 필요",
              "-", "거절+대안", "이력 질문은 한계 안내"))
    return [{"question": a, "expected_answer": b, "filter_or_method": c, "answer_type": d, "note": e} for a, b, c, d, e in q]


RULES = """# 답변 규칙 (시스템 프롬프트 초안)

## 역할
단양군 매포읍 필지의 **위성영상 기반 변화 흔적**과 **현재 건축물 윤곽**을 조회해 주는 보조 도구입니다.
물건조사 대상을 **먼저 확인할 순서로 좁히는** 용도이며, 지장물을 확인해 주는 도구가 아닙니다.

## 반드시 지킬 것
1. 답은 `parcels_ai_view` 표에서 조회한 값만 사용하고, 없는 값은 "자료에 없음"이라고 답한다. 수치를 추정·생성하지 않는다.
2. 필지를 말할 때는 **리·지번·PNU**를 함께 쓰고, 면적 단위(㎡)를 붙인다. 지번은 리마다 중복되므로(168개 지번이 중복) 지번만으로 조회하면 여러 건이 나올 수 있다. 이때는 임의로 하나를 고르지 말고 리를 되묻거나 모두 보여 준다.
3. 변화는 "**언제 일어났다**"가 아니라 "**A년과 B년 영상 사이에 변화 흔적이 관측됨**"으로 말한다(약 2년 간격).
4. 건축물은 "도로명주소 건축물의 **현재 시점** 윤곽"이라고 밝힌다. 가설건축물·비닐하우스·축사 등은 없을 수 있다고 안내한다.
5. 채석장은 "OSM 참조 경계(비공인)와 이어진 **의심 후보**"로만 말한다.
6. 철도 거리는 "OSM 비공식 철도선 기준 **참고값**"이라고 밝힌다.
7. `agri_context == True` 또는 `confusion_risk`가 있으면 "경작·벌채 등 다른 원인일 수 있음"을 함께 안내한다.
8. `screening_tier == 'UNDETERMINED'`는 "해당 없음"이 아니라 "위성영상으로 판단 불가"라고 답한다.

## 하지 말 것 (거절하고 가능한 대안을 안내)
- "불법 건축물/무허가", "신규 건축물 확정", "철거됨", "보상 대상 확정", "철도공사로 인한 변화 확정" 같은 단정.
- 지장물의 정확한 위치·형태·규모, 연도별 생성·변경·소멸 이력에 대한 답(위성 10~20m로는 불가).
- 필지 소유자·개인정보 질의.

## 거절 문구 예시
"이 자료는 위성영상의 식생·맨땅 변화 흔적을 보여 주는 1차 선별 자료라서 ○○는 판단할 수 없습니다.
대신 변화 흔적이 있어 고해상도 영상·현장 확인이 필요한 필지는 안내해 드릴 수 있습니다."

## 답변 형식 권장
결론 한 줄 → 근거 수치(면적·구간) → 주의 문구(한계) 순서.
"""


def build_ai_handoff(bsi_dir: str | Path, out_dir: str | Path) -> list[str]:
    bsi_dir, out_dir = Path(bsi_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t = _build_table(bsi_dir)
    files = []
    flat = pd.DataFrame(t.drop(columns="geometry"))
    flat.to_csv(out_dir / "parcels_ai_view.csv", index=False, encoding="utf-8-sig")
    t.to_file(out_dir / "parcels_ai_view.gpkg", layer="parcels_ai_view", driver="GPKG")
    t.to_crs(4326).to_file(out_dir / "parcels_ai_view.geojson", driver="GeoJSON")
    files += [str(out_dir / f) for f in ("parcels_ai_view.csv", "parcels_ai_view.gpkg", "parcels_ai_view.geojson")]
    pd.DataFrame(DICT, columns=["field", "korean_alias", "description", "unit", "values"]).to_csv(
        out_dir / "data_dictionary.csv", index=False, encoding="utf-8-sig")
    qs = _questions(flat)
    pd.DataFrame(qs).to_csv(out_dir / "sample_questions.csv", index=False, encoding="utf-8-sig")
    (out_dir / "answering_rules.md").write_text(RULES, encoding="utf-8")
    size_kb = (out_dir / "parcels_ai_view.csv").stat().st_size / 1024
    readme = f"""# AI 연계 전달 패키지

대상: 단양군 매포읍 매포리·하괴리 필지 {len(flat):,}개 (EPSG:5186, 지리 좌표는 geojson에서 4326)

| 파일 | 용도 |
|---|---|
| `parcels_ai_view.csv` | **질의용 표**(영문 필드 {flat.shape[1]}개, UTF-8 BOM). AI가 조회할 데이터 |
| `parcels_ai_view.gpkg` / `.geojson` | 같은 표 + 필지 경계(지도 표시·Portal 발행용) |
| `data_dictionary.csv` | 필드 사전(한글 별칭, 설명, 단위, 값 목록) — AI 컨텍스트/필드 별칭으로 사용 |
| `sample_questions.csv` | 예시 질문 {len(qs)}개와 **실제 데이터로 계산한 기대 답변**(테스트 기준) |
| `answering_rules.md` | 답변 규칙·금지 표현(시스템 프롬프트 초안) |

## 연계 방법별 안내

**방법 1. Portal MCP 연계 (토큰 비용 고객사 부담, 가이드만 제공)**
- `parcels_ai_view.geojson`(또는 gpkg)을 Portal에 호스팅 피처 레이어로 발행한다. 기존 코드 `src/publish/arcgis_online.py`의 `publish_geojson_layer`를 참고할 수 있다(Portal 로그인 정보는 환경변수, 저장소에 기록하지 않음).
- 발행 시 `data_dictionary.csv`의 한글 별칭을 필드 별칭으로 지정하고, 레이어 설명에 `answering_rules.md` 요지를 넣는다.
- MCP 클라이언트의 시스템 지침에 `answering_rules.md`를 넣는다.

**방법 2. AI API 연계 (토큰 비용 한국에스리 부담)**
- 표 전체를 프롬프트에 넣지 않는다(CSV 약 {size_kb:,.0f} KB). 질의를 조건식(필터)으로 바꾸는 도구 호출(tool/function calling)로 조회하고 결과 행만 모델에 전달하면 토큰 비용이 작다.
- 모델에는 `data_dictionary.csv`와 `answering_rules.md`만 항상 제공한다.
- `sample_questions.csv`를 회귀 테스트로 사용한다(질문 → 기대 답변과 숫자 일치).

## 주의
- 이 자료는 위성영상(10~20m) 기반 **1차 선별 자료**다. 지장물의 위치·형태·규모나 연도별 이력은 확인하지 못한다.
- 철도선(OSM)·채석장 참조(OSM)는 비공식 자료다.
- 상세 한계: `docs/ndvi_bsi_data_sources_and_limitations.md`, 검증 결과: `docs/ndvi_bsi_validation_report.md`.
"""
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    files += [str(out_dir / f) for f in ("data_dictionary.csv", "sample_questions.csv", "answering_rules.md", "README.md")]
    return files
