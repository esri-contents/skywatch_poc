using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading.Tasks;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace GwangmyeongInterpretation
{
    internal class Module1 : Module
    {
        private static Module1 _this;
        public static Module1 Current => _this ??= (Module1)FrameworkApplication.FindModule("GwangmyeongInterpretation_Module");
    }

    /// <summary>실행 결과(@@RESULT 줄의 JSON). addin_run._result와 키가 같다.</summary>
    internal class RunResult
    {
        public string report { get; set; }
        public string @out { get; set; }
        public string parcels { get; set; }
        public string objects { get; set; }
        public string csv { get; set; }
        public string qa { get; set; }
        public string appendix1 { get; set; }
        public string survey_dir { get; set; }
        public string survey_gdb { get; set; }
        public string survey_xlsx { get; set; }
        public string image_t0 { get; set; }
        public string image_t2 { get; set; }
        public string status_field { get; set; }
        public double? minutes { get; set; }
        public List<string> warnings { get; set; }
    }

    /// <summary>사용자 설정과 마지막 실행 결과(%APPDATA%\GwangmyeongInterpretation\settings.json).</summary>
    internal class Settings
    {
        public string RepoPath { get; set; } = DefaultRepo();
        public string AnalysisPython { get; set; } = "";
        public string T0Path { get; set; } = "";
        public string T0Date { get; set; } = "2022-12-01";
        public string T2Path { get; set; } = "";
        public string T2Date { get; set; } = "2024";
        public string OutRoot { get; set; } = DefaultOutRoot();

        private static bool IsRepo(string p) => !string.IsNullOrEmpty(p) && File.Exists(Path.Combine(p, "src", "interpretation", "addin_run.py"));

        /// <summary>판독 코드 폴더 기본값: 각 고정 드라이브의 \github\skywatch_poc 중 있는 것(여유 공간 큰 드라이브 먼저).</summary>
        private static string DefaultRepo() =>
            FixedDrives().Select(d => Path.Combine(d.RootDirectory.FullName, "github", "skywatch_poc")).FirstOrDefault(IsRepo) ?? @"C:\github\skywatch_poc";

        /// <summary>출력 기본값: 여유 공간이 가장 큰 고정 드라이브의 \광명시흥_판독(한 번 실행에 1~2GB를 쓴다).</summary>
        private static string DefaultOutRoot()
        {
            var d = FixedDrives().FirstOrDefault();
            return d != null ? Path.Combine(d.RootDirectory.FullName, "광명시흥_판독")
                             : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "광명시흥_판독");
        }

        private static IEnumerable<DriveInfo> FixedDrives() =>
            DriveInfo.GetDrives().Where(d => d.DriveType == DriveType.Fixed && d.IsReady).OrderByDescending(d => d.AvailableFreeSpace);
        public bool ChangeDetection { get; set; } = true;
        public bool AddToMap { get; set; } = true;
        public bool OpenReport { get; set; } = true;
        public string Judge { get; set; } = Environment.UserName;
        public RunResult Last { get; set; }

        private static string FilePath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "GwangmyeongInterpretation", "settings.json");
        private static Settings _current;
        public static Settings Current => _current ??= Load();

        private static Settings Load()
        {
            try
            {
                if (File.Exists(FilePath))
                {
                    var s = JsonSerializer.Deserialize<Settings>(File.ReadAllText(FilePath)) ?? new Settings();
                    if (!IsRepo(s.RepoPath)) s.RepoPath = DefaultRepo();  // 저장소를 다른 드라이브로 옮긴 경우
                    return s;
                }
            }
            catch (Exception) { /* 손상된 설정은 기본값으로 */ }
            return new Settings();
        }

        public void Save()
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath));
            File.WriteAllText(FilePath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
        }
    }

    internal static class Shell
    {
        public static void Open(string path)
        {
            if (string.IsNullOrEmpty(path) || !(File.Exists(path) || Directory.Exists(path)))
            {
                MessageBox.Show($"파일이 없습니다:\n{path}", "광명시흥 판독");
                return;
            }
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
        }

        public static RunResult LastOrWarn()
        {
            var r = Settings.Current.Last;
            if (r == null)
                MessageBox.Show("아직 실행 결과가 없습니다. 먼저 [판독 실행]을 하세요.", "광명시흥 판독");
            return r;
        }
    }

    internal class RunButton : Button
    {
        protected override void OnClick() => new RunWindow { Owner = FrameworkApplication.Current.MainWindow }.Show();
    }

    internal class OpenReportButton : Button
    {
        protected override void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r != null) Shell.Open(r.report);
        }
    }

    internal class RebuildReportButton : Button
    {
        protected override void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r == null) return;
            var w = new RunWindow { Owner = FrameworkApplication.Current.MainWindow };
            w.Show();
            w.StartReportOnly(r.@out);
        }
    }

    internal class AddLayersButton : Button
    {
        protected override async void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r == null) return;
            try { await MapResults.AddAsync(r); }
            catch (Exception ex) { MessageBox.Show($"레이어 추가 실패:\n{ex.Message}", "광명시흥 판독"); }
        }
    }

    internal class OpenFolderButton : Button
    {
        protected override void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r != null) Shell.Open(r.@out);
        }
    }

    internal class HistoryButton : Button
    {
        protected override void OnClick() => new HistoryWindow { Owner = FrameworkApplication.Current.MainWindow }.Show();
    }

    internal class ReviewButton : Button
    {
        protected override void OnClick() => ReviewPaneViewModel.Show();
    }

    internal class SurveyExportButton : Button
    {
        protected override async void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r == null) return;
            await Survey.RemoveLayersAsync();
            var w = new RunWindow { Owner = FrameworkApplication.Current.MainWindow };
            w.Show();
            w.StartTask("현장조사 내보내기", $"--out {Runner.Quote(r.@out)} --survey-export", r.@out, async res =>
            {
                w.AppendLog("조사용 지오데이터베이스에 도메인·별칭·사진 첨부 설정(Pro 지오프로세싱)");
                await Survey.PrepareGdbAsync(res.survey_gdb, w.AppendLog);
                await Survey.AddLayerAsync(res.survey_gdb);
                w.AppendLog("완료 - 엑셀 조사표와 survey.gdb는 결과 폴더의 interpretation\\survey에 있습니다");
                Shell.Open(res.survey_dir);
            });
        }
    }

    internal class SurveyImportButton : Button
    {
        protected override void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r == null) return;
            var pick = MessageBox.Show("작성한 현장조사표 엑셀(.xlsx)을 고르려면 [예],\nField Maps에서 동기화한 지오데이터베이스(.gdb 폴더)를 고르려면 [아니오]를 누르세요.",
                                       "현장조사 결과 가져오기", System.Windows.MessageBoxButton.YesNoCancel);
            string src = null;
            if (pick == System.Windows.MessageBoxResult.Yes)
            {
                var d = new Microsoft.Win32.OpenFileDialog { Filter = "현장조사표|*.xlsx;*.xlsm", InitialDirectory = r.survey_dir ?? r.@out };
                if (d.ShowDialog() == true) src = d.FileName;
            }
            else if (pick == System.Windows.MessageBoxResult.No)
            {
                var d = new Microsoft.Win32.OpenFolderDialog { Title = "survey.gdb 폴더 선택", InitialDirectory = r.survey_dir ?? r.@out };
                if (d.ShowDialog() == true) src = d.FolderName;
            }
            if (src == null) return;
            var w = new RunWindow { Owner = FrameworkApplication.Current.MainWindow };
            w.Show();
            w.StartTask("현장조사 결과 가져오기", $"--out {Runner.Quote(r.@out)} --survey-import {Runner.Quote(src)}", r.@out,
                        res => { Shell.Open(res.report); return Task.CompletedTask; });
        }
    }

    internal class ParcelSheetsButton : Button
    {
        protected override async void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r == null) return;
            var pdf = Path.Combine(r.@out, "필지도면_붙임1.pdf");
            try
            {
                var pages = await ParcelSheets.ExportAsync(r, pdf);
                if (MessageBox.Show($"필지 도면 {pages}쪽을 PDF로 저장했습니다.\n{pdf}\n\n지금 열까요?", "필지 도면 PDF", System.Windows.MessageBoxButton.YesNo)
                    == System.Windows.MessageBoxResult.Yes)
                    Shell.Open(pdf);
            }
            catch (Exception ex) { MessageBox.Show($"도면 작성 실패:\n{ex.Message}", "필지 도면 PDF"); }
        }
    }
}
