using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Threading;
using Microsoft.Win32;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace GwangmyeongInterpretation
{
    public partial class RunWindow : ArcGIS.Desktop.Framework.Controls.ProWindow
    {
        private const string ImageFilter = "항공사진|*.tif;*.tiff;*.ecw;*.jp2;*.j2k;*.sid;*.img;*.jpg;*.jpeg;*.png;*.vrt;*.bil;*.dat|모든 파일|*.*";
        private static readonly Regex DateRe = new(@"^\d{4}(-\d{2}(-\d{2})?)?$");
        private CancellationTokenSource _cts;
        private readonly Stopwatch _clock = new();
        private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(1) };

        public RunWindow()
        {
            InitializeComponent();
            var s = Settings.Current;
            var data = Path.Combine(s.RepoPath, "data", "data_261026");
            T0Path.Text = string.IsNullOrEmpty(s.T0Path) ? Path.Combine(data, "항공사진_20221201", "광명시흥 공공주택지구.ecw") : s.T0Path;
            T2Path.Text = string.IsNullOrEmpty(s.T2Path) ? Path.Combine(data, "GRS80_0.25m(2024).jpg") : s.T2Path;
            T0Date.Text = s.T0Date;
            T2Date.Text = s.T2Date;
            OutRoot.Text = s.OutRoot;
            RepoPath.Text = s.RepoPath;
            PythonPath.Text = s.AnalysisPython;
            ChangeDetection.IsChecked = s.ChangeDetection;
            AddToMap.IsChecked = s.AddToMap;
            OpenReport.IsChecked = s.OpenReport;
            _timer.Tick += (_, _) => Elapsed.Text = _clock.Elapsed.ToString(@"hh\:mm\:ss");
        }

        private static string PickFile(string current)
        {
            var d = new OpenFileDialog { Filter = ImageFilter, Title = "항공사진 선택" };
            if (File.Exists(current)) d.InitialDirectory = Path.GetDirectoryName(current);
            return d.ShowDialog() == true ? d.FileName : null;
        }

        private static string PickFolder(string current, string title)
        {
            var d = new OpenFolderDialog { Title = title };
            if (Directory.Exists(current)) d.InitialDirectory = current;
            return d.ShowDialog() == true ? d.FolderName : null;
        }

        private void BrowseT0(object sender, RoutedEventArgs e) => T0Path.Text = PickFile(T0Path.Text) ?? T0Path.Text;
        private void BrowseT2(object sender, RoutedEventArgs e) => T2Path.Text = PickFile(T2Path.Text) ?? T2Path.Text;
        private void BrowseOut(object sender, RoutedEventArgs e) => OutRoot.Text = PickFolder(OutRoot.Text, "출력 상위 폴더") ?? OutRoot.Text;
        private void BrowseRepo(object sender, RoutedEventArgs e) => RepoPath.Text = PickFolder(RepoPath.Text, "판독 코드 폴더(skywatch_poc)") ?? RepoPath.Text;

        public void AppendLog(string line) => Dispatcher.BeginInvoke(() =>
        {
            Log.AppendText(line + Environment.NewLine);
            Log.ScrollToEnd();
        });

        private void SaveSettings()
        {
            var s = Settings.Current;
            s.T0Path = T0Path.Text.Trim(); s.T0Date = T0Date.Text.Trim();
            s.T2Path = T2Path.Text.Trim(); s.T2Date = T2Date.Text.Trim();
            s.OutRoot = OutRoot.Text.Trim(); s.RepoPath = RepoPath.Text.Trim(); s.AnalysisPython = PythonPath.Text.Trim();
            s.ChangeDetection = ChangeDetection.IsChecked == true; s.AddToMap = AddToMap.IsChecked == true; s.OpenReport = OpenReport.IsChecked == true;
            s.Save();
        }

        private string ValidateInputs()
        {
            if (!File.Exists(Path.Combine(RepoPath.Text, "src", "interpretation", "addin_run.py")))
                return $"판독 코드 폴더에 src\\interpretation\\addin_run.py가 없습니다:\n{RepoPath.Text}";
            foreach (var (label, path, date) in new[] { ("기준 시기", T0Path.Text, T0Date.Text), ("비교 시기", T2Path.Text, T2Date.Text) })
            {
                if (!File.Exists(path.Trim())) return $"{label} 영상 파일이 없습니다:\n{path}";
                if (!DateRe.IsMatch(date.Trim())) return $"{label} 촬영일 형식이 잘못됐습니다(YYYY-MM-DD, YYYY-MM, YYYY): {date}";
            }
            if (string.CompareOrdinal(T0Date.Text.Trim(), T2Date.Text.Trim()) >= 0)
                return "기준 시기 촬영일이 비교 시기보다 앞서야 합니다.";
            if (string.IsNullOrWhiteSpace(OutRoot.Text)) return "출력 상위 폴더를 지정하세요.";
            return null;
        }

        private async Task<string> ResolvePythonAsync()
        {
            AppendLog("분석 Python 확인 중…");
            var (py, err) = await Runner.FindPythonAsync(PythonPath.Text.Trim());
            if (py == null)
            {
                AppendLog("✖ " + err);
                MessageBox.Show("판독에 필요한 패키지가 설치된 Python을 찾지 못했습니다.\n" +
                                $"필요: {Runner.RequiredImports.Replace("import ", "")}\n\n{err}", "광명시흥 판독");
                return null;
            }
            PythonPath.Text = py;
            AppendLog($"분석 Python: {py}");
            AppendLog($"형식 변환 Python(ArcGIS Pro): {Runner.ProPython() ?? "없음 - ECW·MrSID는 읽을 수 없음"}");
            return py;
        }

        private async void CheckEnv(object sender, RoutedEventArgs e) => await ResolvePythonAsync();

        private void SetBusy(bool busy)
        {
            RunBtn.IsEnabled = !busy;
            CancelBtn.IsEnabled = busy;
            if (busy) { _clock.Restart(); _timer.Start(); } else { _clock.Stop(); _timer.Stop(); }
        }

        private async void RunClick(object sender, RoutedEventArgs e)
        {
            var err = ValidateInputs();
            if (err != null) { MessageBox.Show(err, "광명시흥 판독"); return; }
            SaveSettings();
            var py = await ResolvePythonAsync();
            if (py == null) return;
            SaveSettings();
            var outDir = Path.Combine(OutRoot.Text.Trim(), DateTime.Now.ToString("yyyyMMdd_HHmmss"));
            var args = $"--out {Runner.Quote(outDir)} --t0 {Runner.Quote(T0Path.Text.Trim())} --t0-date {T0Date.Text.Trim()} " +
                       $"--t2 {Runner.Quote(T2Path.Text.Trim())} --t2-date {T2Date.Text.Trim()}";
            var pro = Runner.ProPython();
            if (pro != null) args += $" --pro-python {Runner.Quote(pro)}";
            if (ChangeDetection.IsChecked != true) args += " --no-change";
            await Execute(py, args, outDir);
        }

        /// <summary>검수표 입력 후 보고서만 다시 만든다(리본의 [보고서 다시 만들기]).</summary>
        public void StartReportOnly(string outDir) => StartTask("보고서 다시 만들기", $"--out {Runner.Quote(outDir)} --report-only", outDir,
            r => { Shell.Open(r.report); return Task.CompletedTask; });

        /// <summary>판독 이외의 작업(보고서 재작성·현장조사 내보내기/가져오기)을 같은 창에서 돌린다. after는 성공 후 할 일.</summary>
        internal async void StartTask(string title, string args, string outDir, Func<RunResult, Task> after = null)
        {
            Title = $"광명시흥 판독 - {title}";
            // 작업 모드: 판독 입력란과 [판독 실행]은 쓰지 않으므로 숨긴다
            InputGrid.Visibility = OptionsBox.Visibility = RunBtn.Visibility = Visibility.Collapsed;
            Height = 520;
            var py = await ResolvePythonAsync();
            if (py == null) return;
            await Execute(py, args, outDir, after, addAndOpen: after == null);
        }

        private async Task Execute(string py, string args, string outDir, Func<RunResult, Task> after = null, bool addAndOpen = true)
        {
            SetBusy(true);
            Progress.Value = 0;
            AppendLog($"출력 폴더: {outDir}");
            _cts = new CancellationTokenSource();
            var (code, json, error) = await Runner.RunAsync(py, RepoPath.Text.Trim(), args, AppendLog,
                (i, n, text) => Dispatcher.BeginInvoke(() =>
                {
                    Progress.Maximum = n;
                    Progress.Value = i - 1;
                    StepText.Text = $"{i}/{n} {text}";
                }), _cts.Token);
            SetBusy(false);
            if (code != 0 || json == null)
            {
                StepText.Text = "실패";
                MessageBox.Show($"판독 실행이 실패했습니다.\n{error ?? $"종료 코드 {code}"}\n\n자세한 내용은 창의 로그를 확인하세요.", "광명시흥 판독");
                return;
            }
            Progress.Value = Progress.Maximum;
            var r = JsonSerializer.Deserialize<RunResult>(json);
            Settings.Current.Last = r;
            Settings.Current.Save();
            StepText.Text = $"완료 ({_clock.Elapsed:hh\\:mm\\:ss})";
            AppendLog($"보고서: {r.report}");
            if (r.warnings is { Count: > 0 })
            {
                foreach (var w in r.warnings) AppendLog("[주의] " + w);
                MessageBox.Show("완료했지만 확인이 필요한 사항이 있습니다.\n\n- " + string.Join("\n- ", r.warnings), "광명시흥 판독");
            }
            if (after != null)
            {
                try { await after(r); }
                catch (Exception ex) { AppendLog($"[경고] 후속 작업 실패: {ex.Message}"); }
                return;
            }
            if (!addAndOpen) return;
            if (AddToMap.IsChecked == true)
            {
                try { await MapResults.AddAsync(r); AppendLog("결과 레이어를 지도에 추가함"); }
                catch (Exception ex) { AppendLog($"[경고] 레이어 추가 실패: {ex.Message}"); }
            }
            if (OpenReport.IsChecked == true) Shell.Open(r.report);
        }

        private void CancelClick(object sender, RoutedEventArgs e)
        {
            if (MessageBox.Show("실행 중인 판독을 취소할까요?", "광명시흥 판독", MessageBoxButton.YesNo) == MessageBoxResult.Yes)
                _cts?.Cancel();
        }

        private void CloseClick(object sender, RoutedEventArgs e) => Close();

        protected override void OnClosing(CancelEventArgs e)
        {
            if (CancelBtn.IsEnabled &&
                MessageBox.Show("판독이 실행 중입니다. 창을 닫으면 실행을 취소합니다. 닫을까요?", "광명시흥 판독", MessageBoxButton.YesNo) != MessageBoxResult.Yes)
            {
                e.Cancel = true;
                return;
            }
            _cts?.Cancel();
            base.OnClosing(e);
        }
    }
}
