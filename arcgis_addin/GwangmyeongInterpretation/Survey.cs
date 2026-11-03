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
    /// 결과 필드와 허용 값은 src/interpretation/survey.py의 RESULT_FIELDS와 같다.
    /// </summary>
    internal static class Survey
    {
        private static readonly (string field, string alias, string domain, string[] values)[] Fields =
        {
            ("EXISTS", "구조물 실재", "D_EXISTS", new[] { "있음", "없음", "확인불가" }),
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

        private static async Task<bool> Gp(string tool, IReadOnlyList<string> args, Action<string> log)
        {
            var res = await Geoprocessing.ExecuteToolAsync(tool, args, null, null, null, GPExecuteToolFlags.None);
            if (res.IsFailed)
                log?.Invoke($"   {tool} 실패: {string.Join(" / ", res.ErrorMessages.Select(m => m.Text))}");
            return !res.IsFailed;
        }

        public static async Task PrepareGdbAsync(string gdb, Action<string> log)
        {
            var fc = Path.Combine(gdb, "survey_points");
            foreach (var (field, alias, domain, values) in Fields)
            {
                if (domain != null)
                {
                    // 다시 내보낸 경우 도메인이 이미 있으면 만들기는 실패해도 된다
                    await Gp("management.CreateDomain", Geoprocessing.MakeValueArray(gdb, domain, alias, "TEXT", "CODED"), null);
                    foreach (var v in values)
                        await Gp("management.AddCodedValueToDomain", Geoprocessing.MakeValueArray(gdb, domain, v, v), null);
                    await Gp("management.AssignDomainToField", Geoprocessing.MakeValueArray(fc, field, domain), log);
                }
                await Gp("management.AlterField", Geoprocessing.MakeValueArray(fc, field, "", alias), log);  // 이름은 그대로, 별칭만
            }
            foreach (var (field, alias) in InfoAliases)
                await Gp("management.AlterField", Geoprocessing.MakeValueArray(fc, field, "", alias), log);
            if (await Gp("management.EnableAttachments", Geoprocessing.MakeValueArray(fc), log))
                log?.Invoke("   사진 첨부 사용 설정 완료 - Field Maps에서 지점마다 사진을 붙일 수 있음");
        }

        public static Task AddLayerAsync(string gdb) => QueuedTask.Run(() =>
        {
            var map = MapView.Active?.Map;
            if (map == null) return;
            var lyr = (FeatureLayer)LayerFactory.Instance.CreateLayer(new Uri(Path.Combine(gdb, "survey_points")), map, 0, "현장조사 대상");
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
