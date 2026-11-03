using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace GwangmyeongInterpretation
{
    /// <summary>
    /// 붙임 1 독립 미등재 필지마다 A4 1쪽 도면(위: 기준 시기, 아래: 비교 시기, 필지 정보 칸, 축척·방위)을 공간 지도 시리즈로 만들고
    /// 한 PDF로 내보낸다. 색인 레이어 = 붙임 1 SHP(SEQ순), 비교 시기 프레임은 기준 프레임 범위를 따른다(지도 프레임 연결).
    /// 만든 지도·레이아웃은 프로젝트에 남겨 두어 사용자가 손볼 수 있다.
    /// </summary>
    internal static class ParcelSheets
    {
        private static CIMColor Rgb(int r, int g, int b, double a = 100) => ColorFactory.Instance.CreateRGBColor(r, g, b, a);

        private static CIMPolygonSymbol Hollow(CIMColor c, double w) =>
            SymbolFactory.Instance.ConstructPolygonSymbol(Rgb(0, 0, 0, 0), SimpleFillStyle.Null, SymbolFactory.Instance.ConstructStroke(c, w, SimpleLineStyle.Solid));

        private static FeatureLayer AddVector(Map map, string path, string table, string name, CIMPolygonSymbol sym, string where = null)
        {
            var lyr = table != null ? MapResults.GpkgLayer(path, table, map, name)
                                    : (FeatureLayer)LayerFactory.Instance.CreateLayer(new Uri(path), map, 0, name);
            lyr.SetRenderer(new CIMSimpleRenderer { Symbol = sym.MakeSymbolReference() });
            if (where != null) lyr.SetDefinitionQuery(where);
            return lyr;
        }

        private static Map BuildMap(string name, string image, RunResult r, bool index, string objWhere)
        {
            var old = Project.Current.GetItems<MapProjectItem>().FirstOrDefault(m => m.Name == name);
            if (old != null) Project.Current.RemoveItem(old);
            var map = MapFactory.Instance.CreateMap(name, MapType.Map, MapViewingMode.Map, Basemap.None);
            map.SetSpatialReference(SpatialReferenceBuilder.CreateSpatialReference(5186));
            if (File.Exists(image)) LayerFactory.Instance.CreateLayer(new Uri(image), map, 0, "영상");
            AddVector(map, r.parcels, "parcels", "필지 경계", Hollow(Rgb(255, 255, 255), 0.6));
            AddVector(map, r.objects, "objects", "판독 덩어리", Hollow(Rgb(255, 230, 0), 1.6), objWhere);
            if (index)
                AddVector(map, r.appendix1, null, "붙임1 필지", Hollow(Rgb(200, 65, 75), 2.4));
            return map;
        }

        private static string Dyn(string field) => $"<dyn type=\"page\" property=\"attribute\" field=\"{field}\"/>";

        /// <summary>반환: 쪽수.</summary>
        public static Task<int> ExportAsync(RunResult r, string pdfPath) => QueuedTask.Run(() =>
        {
            if (!File.Exists(r.appendix1)) throw new FileNotFoundException("붙임 1 SHP가 없습니다. 보고서를 다시 만들면 생성됩니다.", r.appendix1);
            var m0 = BuildMap("필지도면_기준시기", r.image_t0, r, true, "kind = 'unregistered' AND tier = 'U'");
            var m2 = BuildMap("필지도면_비교시기", r.image_t2, r, false, "kind IN ('unregistered','new','gone')");
            var index = m0.GetLayersAsFlattenedList().OfType<FeatureLayer>().First(l => l.Name == "붙임1 필지");

            var oldLayout = Project.Current.GetItems<LayoutProjectItem>().FirstOrDefault(l => l.Name == "필지 도면(붙임 1)");
            if (oldLayout != null) Project.Current.RemoveItem(oldLayout);
            var layout = LayoutFactory.Instance.CreateLayout(210, 297, LinearUnit.Millimeters);
            layout.SetName("필지 도면(붙임 1)");

            var f0 = ElementFactory.Instance.CreateMapFrameElement(layout, EnvelopeBuilderEx.CreateEnvelope(15, 160, 195, 262), m0, "기준시기");
            var f2 = ElementFactory.Instance.CreateMapFrameElement(layout, EnvelopeBuilderEx.CreateEnvelope(15, 52, 195, 154), m2, "비교시기");

            var title = SymbolFactory.Instance.ConstructTextSymbol(Rgb(0, 0, 0), 15, "Malgun Gothic", "Bold");
            var body = SymbolFactory.Instance.ConstructTextSymbol(Rgb(0, 0, 0), 9.5, "Malgun Gothic", "Regular");
            var small = SymbolFactory.Instance.ConstructTextSymbol(Rgb(60, 60, 60), 8.5, "Malgun Gothic", "Regular");
            ElementFactory.Instance.CreateTextGraphicElement(layout, TextType.PointText, MapPointBuilderEx.CreateMapPoint(15, 280), title,
                $"독립 미등재 구조물 필지 도면 · 연번 {Dyn("SEQ")}", "제목");
            ElementFactory.Instance.CreateTextGraphicElement(layout, TextType.PointText, MapPointBuilderEx.CreateMapPoint(15, 272), small,
                "광명시흥 공공주택지구 · 보고서 붙임 1과 같은 연번 · 빨간 선 = 대상 필지, 노란 선 = 판독 덩어리, 흰 선 = 필지 경계", "부제");
            ElementFactory.Instance.CreateTextGraphicElement(layout, TextType.PointText, MapPointBuilderEx.CreateMapPoint(15, 264), body, "기준 시기 영상", "라벨1");
            ElementFactory.Instance.CreateTextGraphicElement(layout, TextType.PointText, MapPointBuilderEx.CreateMapPoint(15, 156), body, "비교 시기 영상", "라벨2");

            // 필지 정보 칸(표 형태 동적 텍스트) - 테두리 + 줄
            ElementFactory.Instance.CreateGraphicElement(layout, EnvelopeBuilderEx.CreateEnvelope(15, 14, 195, 46),
                SymbolFactory.Instance.ConstructPolygonSymbol(Rgb(244, 246, 248), SimpleFillStyle.Solid, SymbolFactory.Instance.ConstructStroke(Rgb(0, 0, 0), 0.8)), "정보칸");
            var info = string.Join("\r\n", new[]
            {
                $"PNU  {Dyn("PNU")}        지목  {Dyn("JIMOK")}        판독 상태  {Dyn("STATUS")}",
                $"소재지  {Dyn("ADDR")}",
                $"필지 면적  {Dyn("PARCEL_M2")}㎡     등재 건물 면적  {Dyn("REG_M2")}㎡     미등재 판독 면적  {Dyn("UNREG_M2")}㎡ ({Dyn("N_OBJ")}개 덩어리)",
                "현장 확인:  □ 있음  □ 없음  □ 확인불가        물건조서:  □ 등재  □ 미등재        확인자 __________",
            });
            ElementFactory.Instance.CreateTextGraphicElement(layout, TextType.PointText, MapPointBuilderEx.CreateMapPoint(19, 41), body, info, "필지정보");

            var style = Project.Current.GetItems<StyleProjectItem>().FirstOrDefault(s => s.Name == "ArcGIS 2D");
            var sb = style?.SearchScaleBars("Alternating Scale Bar 1").FirstOrDefault();
            var na = style?.SearchNorthArrows("ArcGIS North 1").FirstOrDefault();
            if (sb != null)
                ElementFactory.Instance.CreateMapSurroundElement(layout, EnvelopeBuilderEx.CreateEnvelope(130, 6, 195, 12),
                    new ScaleBarInfo { MapFrameName = f0.Name, ScaleBarStyleItem = sb }, "축척");
            if (na != null)
                ElementFactory.Instance.CreateMapSurroundElement(layout, EnvelopeBuilderEx.CreateEnvelope(183, 246, 193, 260),
                    new NorthArrowInfo { MapFrameName = f0.Name, NorthArrowStyleItem = na }, "방위");

            // 비교 시기 프레임이 기준 프레임 범위를 따르게
            var d2 = (CIMMapFrame)f2.GetDefinition();
            d2.AutoCamera = new CIMAutoCamera { Source = AutoCameraSource.MapFrameLink, AutoCameraType = AutoCameraType.Extent, MapFrameLinkName = f0.Name };
            f2.SetDefinition(d2);

            var ms = MapSeries.CreateSpatialMapSeries(layout, f0, index) as SpatialMapSeries;
            ms.PageNameField = "SEQ";
            ms.SortField = "SEQ";
            ms.ExtentOptions = ExtentFitType.BestFit;
            ms.MarginType = ArcGIS.Core.CIM.UnitType.Percent;
            ms.Margin = 80;
            layout.SetMapSeries(ms);

            var pages = layout.MapSeries.PageCount;
            layout.Export(new PDFFormat { OutputFileName = pdfPath, Resolution = 200, ImageQuality = ImageQuality.Normal },
                          new MapSeriesExportOptions { ExportPages = ExportPages.All, ExportFileOptions = ExportFileOptions.ExportAsSinglePDF });
            return pages;
        });
    }
}
