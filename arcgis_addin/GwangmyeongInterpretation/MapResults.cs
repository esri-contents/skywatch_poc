using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.CIM;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace GwangmyeongInterpretation
{
    /// <summary>실행 결과를 지도에 올린다. 색은 보고서(generate_interpretation_report._STATUS, interpretation_figures)와 같다.</summary>
    internal static class MapResults
    {
        private static readonly (string value, string hex)[] Status =
        {
            ("있음(등재+판독)", "#1f6f78"), ("있음(등재만)", "#8fb8bd"), ("있음(판독만)", "#c8414b"), ("없음", "#d9dee2"), ("영상 부족", "#9aa3aa"),
        };
        // v2.0: 독립 = 도면·대장 모두 없음, 대장 없음 = 도면(건물통합정보)은 있으나 대장 미연계
        private static readonly (string value, string label, string hex)[] Unreg =
        {
            ("독립", "도면·대장 없음", "#c8414b"), ("대장 없음", "도면 있음·대장 없음", "#7b4fa0"),
            ("부속 의심", "부속 의심", "#e9a23b"), ("저신뢰", "저신뢰(도로·하천류)", "#8a8f94"),
        };
        private static readonly (string value, string label, string hex)[] Change = { ("new", "신규 의심", "#ffb703"), ("gone", "소멸 의심", "#3a7bd5") };

        private static CIMColor Rgb(string hex, double alpha = 100)
        {
            var c = Convert.ToInt32(hex.TrimStart('#'), 16);
            return ColorFactory.Instance.CreateRGBColor((c >> 16) & 255, (c >> 8) & 255, c & 255, alpha);
        }

        private static CIMUniqueValueRenderer Renderer(string field, (string value, string label, CIMPolygonSymbol symbol)[] classes) => new()
        {
            Fields = new[] { field },
            UseDefaultSymbol = false,
            Groups = new[]
            {
                new CIMUniqueValueGroup
                {
                    Heading = field,
                    Classes = classes.Select(c => new CIMUniqueValueClass
                    {
                        Label = c.label, Visible = true, Symbol = c.symbol.MakeSymbolReference(),
                        Values = new[] { new CIMUniqueValue { FieldValues = new[] { c.value } } },
                    }).ToArray(),
                },
            },
        };

        private static CIMPolygonSymbol Fill(string hex, double alpha, string outline, double width) =>
            SymbolFactory.Instance.ConstructPolygonSymbol(Rgb(hex, alpha), SimpleFillStyle.Solid,
                SymbolFactory.Instance.ConstructStroke(Rgb(outline), width, SimpleLineStyle.Solid));

        private static CIMPolygonSymbol Hollow(string hex, double width) =>
            SymbolFactory.Instance.ConstructPolygonSymbol(Rgb("#000000", 0), SimpleFillStyle.Null,
                SymbolFactory.Instance.ConstructStroke(Rgb(hex), width, SimpleLineStyle.Solid));

        /// <summary>GeoPackage 테이블 경로. Pro는 "파일.gpkg\main.테이블" 형식으로 연다.</summary>
        private static Uri Gpkg(string path, string table) => new(Path.Combine(path, "main." + table));

        /// <summary>GeoPackage 레이어. "main.테이블"로 안 열리면 "테이블"로 한 번 더 시도.</summary>
        internal static FeatureLayer GpkgLayer(string path, string table, ILayerContainerEdit container, string name)
        {
            try { return (FeatureLayer)LayerFactory.Instance.CreateLayer(Gpkg(path, table), container, 0, name); }
            catch (Exception) { return (FeatureLayer)LayerFactory.Instance.CreateLayer(new Uri(Path.Combine(path, table)), container, 0, name); }
        }

        public static Task AddAsync(RunResult r) => QueuedTask.Run(() =>
        {
            var map = MapView.Active?.Map ?? throw new InvalidOperationException("열린 지도가 없습니다. 지도 보기를 연 뒤 다시 실행하세요.");
            var name = "광명시흥 판독 " + Path.GetFileName(r.@out.TrimEnd('\\'));
            var group = LayerFactory.Instance.CreateGroupLayer(map, 0, name);

            // 아래부터 쌓는다(나중에 넣은 것이 위)
            if (File.Exists(r.image_t2))
            {
                var t2 = LayerFactory.Instance.CreateLayer(new Uri(r.image_t2), group, 0, "비교 시기 영상");
                t2.SetVisibility(false);
            }
            if (File.Exists(r.image_t0))
                LayerFactory.Instance.CreateLayer(new Uri(r.image_t0), group, 0, "기준 시기 영상");

            var field = string.IsNullOrEmpty(r.status_field) ? "status_t0" : r.status_field;
            var status = GpkgLayer(r.parcels, "parcels", group, "필지 판독 상태");
            status.SetRenderer(Renderer(field, Status.Select(s => (s.value, s.value, Fill(s.hex, 55, "#ffffff", 0.3))).ToArray()));
            status.SetTransparency(20);

            var unreg = GpkgLayer(r.parcels, "parcels", group, "미등재 구조물 필지");
            unreg.SetDefinitionQuery("unregistered_flag = 1");
            unreg.SetRenderer(Renderer("unregistered_conf", Unreg.Select(s => (s.value, "미등재 · " + s.label, Hollow(s.hex, 2.0))).ToArray()));

            var objs = GpkgLayer(r.objects, "objects", group, "신규·소멸 의심(A·B)");
            objs.SetDefinitionQuery("kind IN ('new','gone') AND tier IN ('A','B')");
            objs.SetRenderer(Renderer("kind", Change.Select(s => (s.value, s.label, Hollow(s.hex, 2.5))).ToArray()));

            // 붙임 1-2 SHP는 붙임 1과 같은 폴더 규칙(이름·생성 시각 접미사)으로 옆에 생긴다(deliver.NAME_NOREG)
            var a1b = string.IsNullOrEmpty(r.appendix1) ? null
                : r.appendix1.Replace("appendix1_unregistered_parcels", "appendix1b_mapped_without_register");
            if (a1b != null && File.Exists(a1b))
            {
                var l = (FeatureLayer)LayerFactory.Instance.CreateLayer(new Uri(a1b), group, 0, "붙임 1-2 · 미등재(도면 있음·대장 없음)");
                l.SetRenderer(new CIMSimpleRenderer { Symbol = Fill("#7b4fa0", 25, "#7b4fa0", 2.0).MakeSymbolReference() });
            }
            if (File.Exists(r.appendix1))  // 보고서 붙임 1과 같은 목록(SEQ = 연번)
            {
                var a1 = (FeatureLayer)LayerFactory.Instance.CreateLayer(new Uri(r.appendix1), group, 0, "붙임 1 · 미등재(도면·대장 없음)");
                a1.SetRenderer(new CIMSimpleRenderer { Symbol = Fill("#c8414b", 25, "#c8414b", 2.0).MakeSymbolReference() });
                unreg.SetVisibility(false);
            }

            var unregObjs = GpkgLayer(r.objects, "objects", group, "미등재 판독 덩어리");
            unregObjs.SetDefinitionQuery("kind = 'unregistered'");
            unregObjs.SetRenderer(Renderer("tier", new[]
            {
                ("U", "도면·대장 없음", Hollow("#c8414b", 1.2)), ("R", "도면 있음·대장 없음", Hollow("#7b4fa0", 1.2)),
                ("UA", "부속 의심", Hollow("#e9a23b", 1.2)),
            }));
            unregObjs.SetVisibility(false);

            MapView.Active.ZoomTo(status);
        });
    }
}
