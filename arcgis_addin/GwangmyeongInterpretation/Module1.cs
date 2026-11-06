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
        public string sheets_pdf { get; set; }
        public int? sheets_pages { get; set; }
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

        /// <summary>설정 저장. 실패해도(권한·디스크) 작업은 계속한다 - 다음 Pro 실행에서 마지막 결과를 기억하지 못할 뿐.</summary>
        public void TrySave()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(FilePath));
                File.WriteAllText(FilePath, JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
            }
            catch (Exception ex) { Debug.WriteLine($"settings save failed: {ex.Message}"); }
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
            try { Process.Start(new ProcessStartInfo(path) { UseShellExecute = true }); }
            catch (Exception ex)  // 연결 프로그램 없음(예: PDF 뷰어 미설치) 등 - 예외가 처리기 밖으로 나가면 Pro가 종료된다
            {
                MessageBox.Show($"파일을 열 프로그램이 없거나 열 수 없습니다. 탐색기에서 직접 여세요.\n{path}\n\n{ex.Message}", "광명시흥 판독");
            }
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
            try { await Survey.RemoveLayersAsync(); }
            catch (Exception ex) { MessageBox.Show($"이전 현장조사 레이어를 지도에서 빼지 못했습니다. 직접 제거한 뒤 다시 실행하세요.\n{ex.Message}", "현장조사 내보내기"); return; }
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

    /// <summary>붙임 1 필지 도면 PDF. 분석 Python이 만든다(Pro 레이아웃으로 300쪽을 내보내면 Pro가 종료되는 문제가 있었다).</summary>
    internal class ParcelSheetsButton : Button
    {
        protected override void OnClick()
        {
            var r = Shell.LastOrWarn();
            if (r == null) return;
            var w = new RunWindow { Owner = FrameworkApplication.Current.MainWindow };
            w.Show();
            w.StartTask("필지 도면 PDF", $"--out {Runner.Quote(r.@out)} --sheets", r.@out, res =>
            {
                if (MessageBox.Show($"필지 도면 {res.sheets_pages}쪽을 PDF로 저장했습니다.\n{res.sheets_pdf}\n\n지금 열까요?", "필지 도면 PDF",
                                    System.Windows.MessageBoxButton.YesNo) == System.Windows.MessageBoxResult.Yes)
                    Shell.Open(res.sheets_pdf);
                return Task.CompletedTask;
            });
        }
    }
}
