using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Windows;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace GwangmyeongInterpretation
{
    /// <summary>출력 상위 폴더 아래 실행 폴더(run_info.json)를 모아 보여 준다. 상태는 폴더에만 두고 따로 저장하지 않는다.</summary>
    public partial class HistoryWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        public class Row
        {
            public string Started { get; set; }
            public string T0 { get; set; }
            public string T2 { get; set; }
            public string Minutes { get; set; }
            public string Parcels { get; set; }
            public string Unreg { get; set; }
            public string NewA { get; set; }
            public string Gone { get; set; }
            public string Reused { get; set; }
            public string Current { get; set; }
            internal RunResult Result { get; set; }
        }

        public HistoryWindow()
        {
            InitializeComponent();
            Load();
        }

        private static string Get(JsonElement e, params string[] path)
        {
            foreach (var p in path)
                if (e.ValueKind != JsonValueKind.Object || !e.TryGetProperty(p, out e)) return "";
            return e.ValueKind == JsonValueKind.String ? e.GetString() : e.ValueKind == JsonValueKind.Null ? "" : e.ToString();
        }

        public static List<Row> Scan(string root)
        {
            var rows = new List<Row>();
            if (!Directory.Exists(root)) return rows;
            var current = Settings.Current.Last?.@out;
            foreach (var dir in Directory.GetDirectories(root).OrderByDescending(d => d))
            {
                var f = Path.Combine(dir, "run_info.json");
                if (!File.Exists(f)) continue;
                try
                {
                    var j = JsonDocument.Parse(File.ReadAllText(f)).RootElement;
                    if (!j.TryGetProperty("result", out var res)) continue;  // 끝나지 않은 실행
                    var reused = new List<string>();
                    bool Yes(string v) => string.Equals(v, "true", StringComparison.OrdinalIgnoreCase);
                    if (new[] { "t0", "t2" }.Any(e => Get(j, "inputs", e, "state") == "reused" || Yes(Get(j, "inputs", e, "cached")))) reused.Add("영상");
                    if (Yes(Get(j, "reused_analysis"))) reused.Add("분석");
                    rows.Add(new Row
                    {
                        Started = Get(j, "started").Replace("T", " ").Split('+')[0],
                        T0 = $"{Get(j, "dates", "t0")} {Path.GetFileName(Get(j, "inputs", "t0", "source"))}",
                        T2 = $"{Get(j, "dates", "t2")} {Path.GetFileName(Get(j, "inputs", "t2", "source"))}",
                        Minutes = Get(j, "minutes"),
                        Parcels = Get(j, "counts", "parcels"),
                        Unreg = Get(j, "counts", "unreg_independent"),
                        NewA = Get(j, "counts", "new_A"),
                        Gone = Get(j, "counts", "gone_AB"),
                        Reused = string.Join("·", reused),
                        Current = string.Equals(Path.GetFullPath(dir), current == null ? null : Path.GetFullPath(current), StringComparison.OrdinalIgnoreCase) ? "●" : "",
                        Result = JsonSerializer.Deserialize<RunResult>(res.GetRawText()),
                    });
                }
                catch (Exception) { /* 손상된 기록은 건너뜀 */ }
            }
            return rows;
        }

        private void Load()
        {
            var root = Settings.Current.OutRoot;
            RootText.Text = $"출력 상위 폴더: {root}";
            List.ItemsSource = Scan(root);
        }

        private RunResult Selected()
        {
            if (List.SelectedItem is Row r) return r.Result;
            MessageBox.Show("목록에서 실행을 고르세요.", "실행 이력");
            return null;
        }

        private void RefreshClick(object sender, RoutedEventArgs e) => Load();

        private void SelectClick(object sender, RoutedEventArgs e)
        {
            var r = Selected();
            if (r == null) return;
            Settings.Current.Last = r;
            Settings.Current.Save();
            Load();
        }

        private void ReportClick(object sender, RoutedEventArgs e) { var r = Selected(); if (r != null) Shell.Open(r.report); }
        private void FolderClick(object sender, RoutedEventArgs e) { var r = Selected(); if (r != null) Shell.Open(r.@out); }

        private async void LayersClick(object sender, RoutedEventArgs e)
        {
            var r = Selected();
            if (r == null) return;
            try { await MapResults.AddAsync(r); }
            catch (Exception ex) { MessageBox.Show($"레이어 추가 실패:\n{ex.Message}", "실행 이력"); }
        }

        private void CloseClick(object sender, RoutedEventArgs e) => Close();
    }
}
