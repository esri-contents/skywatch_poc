using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace GwangmyeongInterpretation
{
    /// <summary>
    /// 현장조사용 survey.gdb 마무리(Pro 안에서만 가능): 코드값 도메인, 한글 별칭, 사진 첨부(attachments).
    /// 결과 필드와 허용 값은 src/interpretation/survey.py의 RESULT_FIELDS와 같다(gdb 필드 이름은 GDB_NAMES를 따른다:
    /// EXISTS는 파일 gdb 예약어라 GDAL이 EXISTS_로 바꿔 써서 gdb에서는 EXIST_YN).
    /// </summary>
    internal static class Survey
    {
        private static readonly (string field, string alias, string domain, string[] values)[] Fields =
        {
            ("EXIST_YN", "구조물 실재", "D_EXISTS", new[] { "있음", "없음", "확인불가" }),
            ("USE_TYPE", "용도", "D_USE", new[] { "주택", "창고", "축사", "비닐하우스", "컨테이너", "공장·작업장", "기타" }),
            ("REGISTERED", "물건조서 등재", "D_REG", new[] { "예", "아니오", "확인불가" }),
            ("SURVEYOR", "조사자", null, null),
            ("SURVEY_DT", "조사일", null, null),
            ("PHOTO", "사진 파일", null, null),
            ("NOTE", "비고", null, null),
        };
        private static readonly (string field, string alias)[] InfoAliases =
        {
            ("SID", "조사번호"), ("KIND", "구분"), ("ADDR", "소재지"), ("JIMOK", "지목"), ("AREA_M2", "판독 면적(㎡)"), ("IMG", "근거 영상"),
        };

        /// <summary>도구 하나를 실행한다. 실패하면 로그에 쓰고 failures에 "도구: 메시지"를 더한다(예외도 실패로 받는다).</summary>
        private static async Task<bool> Gp(string tool, IReadOnlyList<string> args, Action<string> log, List<string> failures)
        {
            string msg;
            try
            {
                var res = await Geoprocessing.ExecuteToolAsync(tool, args, null, null, null, GPExecuteToolFlags.None);
                if (res != null && !res.IsFailed) return true;
                msg = res == null ? "결과 없음" : string.Join(" / ", res.ErrorMessages.Select(m => m.Text));
            }
            catch (Exception ex) { msg = ex.Message; }
            log?.Invoke($"   {tool} 실패: {msg}");
            failures.Add($"{tool.Replace("management.", "")}: {msg}");
            return false;
        }

        /// <summary>
        /// 도메인·별칭·사진 첨부를 설정한다. 반환: 실패한 도구 목록(비어 있으면 모두 성공).
        /// survey.gdb는 내보낼 때마다 새로 만들므로(이전 것은 survey.py가 시각을 붙여 보관) 도메인이 이미 있는 경우는 없다 -
        /// 어떤 실패든 사용자에게 알린다(라이선스 부족도 여기서 드러난다. 5개 도구 모두 Esri 문서상 Basic 이상).
        /// </summary>
        public static async Task<List<string>> PrepareGdbAsync(string gdb, Action<string> log)
        {
            var failures = new List<string>();
            var fc = Path.Combine(gdb, "survey_points");
            foreach (var (field, alias, domain, values) in Fields)
            {
                if (domain != null &&
                    await Gp("management.CreateDomain", Geoprocessing.MakeValueArray(gdb, domain, alias, "TEXT", "CODED"), log, failures))
                {
                    var ok = true;
                    foreach (var v in values)
                        ok &= await Gp("management.AddCodedValueToDomain", Geoprocessing.MakeValueArray(gdb, domain, v, v), log, failures);
                    if (ok) await Gp("management.AssignDomainToField", Geoprocessing.MakeValueArray(fc, field, domain), log, failures);
                }
                await Gp("management.AlterField", Geoprocessing.MakeValueArray(fc, field, "", alias), log, failures);  // 이름은 그대로, 별칭만
            }
            foreach (var (field, alias) in InfoAliases)
                await Gp("management.AlterField", Geoprocessing.MakeValueArray(fc, field, "", alias), log, failures);
            if (await Gp("management.EnableAttachments", Geoprocessing.MakeValueArray(fc), log, failures))
                log?.Invoke("   사진 첨부 사용 설정 완료 - Field Maps에서 지점마다 사진을 붙일 수 있음");
            return failures;
        }

        public const string LayerName = "현장조사 대상";

        /// <summary>다시 내보내기 전에 이전 조사 레이어를 지도에서 뺀다(Pro가 survey.gdb를 잠가 보관이 안 되므로).</summary>
        public static Task RemoveLayersAsync() => QueuedTask.Run(() =>
        {
            var map = MapView.Active?.Map;
            if (map == null) return;
            var old = map.GetLayersAsFlattenedList().Where(l => l.Name == LayerName).ToList();
            if (old.Count > 0) map.RemoveLayers(old);
        });

        public static Task AddLayerAsync(string gdb) => QueuedTask.Run(() =>
        {
            var map = MapView.Active?.Map;
            if (map == null) return;
            var lyr = (FeatureLayer)LayerFactory.Instance.CreateLayer(new Uri(Path.Combine(gdb, "survey_points")), map, 0, LayerName);
            CIMPointSymbol Dot(string hex) => SymbolFactory.Instance.ConstructPointSymbol(
                ColorFactory.Instance.CreateRGBColor(Convert.ToInt32(hex.Substring(1, 2), 16), Convert.ToInt32(hex.Substring(3, 2), 16), Convert.ToInt32(hex.Substring(5, 2), 16)),
                9, SimpleMarkerStyle.Circle);
            var classes = new[] { ("미등재(독립)", "#c8414b"), ("신규 의심", "#ffb703"), ("소멸 의심", "#3a7bd5") }
                .Select(c => new CIMUniqueValueClass
                {
                    Label = c.Item1, Visible = true, Symbol = Dot(c.Item2).MakeSymbolReference(),
                    Values = new[] { new CIMUniqueValue { FieldValues = new[] { c.Item1 } } },
                }).ToArray();
            lyr.SetRenderer(new CIMUniqueValueRenderer
            {
                Fields = new[] { "KIND" }, UseDefaultSymbol = false,
                Groups = new[] { new CIMUniqueValueGroup { Heading = "구분", Classes = classes } },
            });
        });
    }
}
