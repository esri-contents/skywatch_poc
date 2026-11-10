using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace GwangmyeongInterpretation
{
    internal class ReviewPaneViewModel : DockPane
    {
        private const string Id = "GwangmyeongInterpretation_ReviewPane";

        public static void Show() => Show(null);

        /// <summary>창을 연다. screen이 true/false면 그 모드(후보 판독/표본 검수)로 바꿔 다시 읽는다.</summary>
        public static void Show(bool? screen)
        {
            if (screen.HasValue) ReviewPaneView.ScreenMode = screen.Value;
            var pane = FrameworkApplication.DockPaneManager.Find(Id);
            pane?.Activate();
            if (screen.HasValue) ReviewPaneView.Current?.SyncMode();
        }
    }

    /// <summary>
    /// 검수·판독 창. 두 모드가 같은 화면·같은 표 형식(QaTable)을 쓴다. 판정마다 바로 저장(label·note·judge, 후보 판독은 reason도).
    /// - 표본 검수: 검수표(qa/review1/qa_labels.csv) 표본으로 정밀도를 잰다. 층별 기준은 src/interpretation/qa.py build_parcel_round와 같다.
    /// - 후보 판독: 모델 후보 전체(screen/screen_labels.csv, 번호는 현장조사와 같음)를 육안 판독해 오탐을 현장조사에서 뺀다
    ///   (src/interpretation/screen.py).
    /// </summary>
    public partial class ReviewPaneView : UserControl
    {
        private static readonly Dictionary<string, string> StratumNames = new()
        {
            ["new_A"] = "신규 의심 A", ["gone_AB"] = "소멸 의심(전수)", ["unreg_linear"] = "미등재 · 도로·하천류 지목",
            ["seen_linear"] = "판독만 · 도로·하천류 지목", ["unreg_attached"] = "미등재 · 부속 의심",
            ["unreg_noreg"] = "미등재 · 도면 있음·대장 없음",
        };

        private static string StratumName(string s) => StratumNames.TryGetValue(s, out var n) ? n : s.StartsWith("unreg_") ? $"미등재 · 지목 {s[6..]}" : s;

        private static string GuideFor(string s) => s switch
        {
            "new_A" => "왼쪽(기준 시기)에 없던 구조물이 오른쪽(비교 시기) 노란 선 안에 생겼으면 '맞음'. 지붕 도색·천막·차량·그림자는 '아님'",
            "gone_AB" => "왼쪽(기준 시기) 노란 선 안 구조물이 오른쪽(비교 시기)에 실제로 없어졌으면 '맞음'",
            "seen_linear" => "필지(흰 선) 안에 실제 구조물이 있으면 '맞음'",
            _ => "노란 선 안 덩어리 중 하나라도 등재 건물의 일부가 아닌 구조물(창고·하우스·컨테이너·가설물 등)이면 '맞음'. 등재 건물 지붕 가장자리·포장면·수목은 '아님'",
        };

        private static readonly string[] ScreenLabelTexts = { "현장조사 대상", "오탐(제외)", "판단 보류" };
        private static readonly string[] Reasons = { "", "그림자 위치 변화", "경사지붕 일조·밝기 차이", "나지→포장면", "차량·적치물",
                                                     "비닐하우스·컨테이너 색·반사", "수목·식생", "기타" };

        /// <summary>true = 후보 판독, false = 표본 검수.</summary>
        internal static bool ScreenMode { get; set; }

        private static string GuideForScreen(string stratum) => stratum switch
        {
            "screen_A" => "왼쪽(기준 시기)에 없던 구조물이 오른쪽(비교 시기) 노란 선 안에 실제로 생겼으면 ① 현장조사 대상. 그림자·지붕 밝기·포장·차량·적치물·하우스 색 변화면 ② 오탐(원인 선택). 확대·행정자료 확인이 더 필요하면 ③",
            "screen_G" => "왼쪽(기준 시기) 노란 선 안 구조물이 오른쪽(비교 시기)에 실제로 없어졌으면 ① 현장조사 대상. 그림자·밝기 차이로 안 보일 뿐이면 ② 오탐",
            "screen_R" => "노란 선 안이 건물통합정보에는 있으나 대장이 없는 건물이다. 영상에 실제 구조물(창고·하우스·컨테이너·가설물 등)이 있으면 ① 현장조사 대상. 이미 철거됐거나 포장면·수목이면 ② 오탐",
            _ => "노란 선 안에 건물 레이어에 없는 실제 구조물(창고·하우스·컨테이너·가설물 등)이 있으면 ① 현장조사 대상. 등재 건물 지붕 가장자리·포장면·수목·차량이면 ② 오탐",
        };

        private QaTable _qa;
        private List<int> _order = new();
        private int _pos;

        /// <summary>열려 있는 검수 창(실행 이력에서 현재 결과를 바꾸면 다시 읽게).</summary>
        internal static ReviewPaneView Current { get; private set; }

        public ReviewPaneView()
        {
            InitializeComponent();
            Reason.ItemsSource = Reasons;  // Render가 선택을 맞추기 전에 목록이 있어야 한다
            Current = this;
        }

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            Judge.Text = Settings.Current.Judge;
            SyncMode();
        }

        /// <summary>모드 표시(버튼 글자·원인 목록)를 맞추고, 열린 표가 현재 모드·결과의 표가 아니면 다시 읽는다.</summary>
        internal void SyncMode()
        {
            if (Mode.SelectedIndex != (ScreenMode ? 1 : 0)) Mode.SelectedIndex = ScreenMode ? 1 : 0;
            var texts = ScreenMode ? ScreenLabelTexts : QaTable.Labels.Select(l => l.text).ToArray();
            B1.Content = "① " + texts[0]; B2.Content = "② " + texts[1]; B3.Content = "③ " + texts[2];
            ReasonRow.Visibility = ScreenMode ? Visibility.Visible : Visibility.Collapsed;
            ReportBtn.Content = ScreenMode ? "현장조사 내보내기" : "보고서 다시 만들기";
            var want = ScreenMode ? Settings.Current.Last?.screen : Settings.Current.Last?.qa;
            if (_qa == null || !string.Equals(_qa.Path, want, StringComparison.OrdinalIgnoreCase)) Reload();
        }

        private void ModeChanged(object sender, SelectionChangedEventArgs e)
        {
            if (!IsLoaded) return;
            var screen = Mode.SelectedIndex == 1;
            if (screen == ScreenMode) return;
            ScreenMode = screen;
            _qa = null;
            SyncMode();
        }

        internal void Reload()
        {
            try { LoadTable(); }
            catch (Exception ex)  // 손상·잠긴 검수표 - 처리기 밖으로 나가면 Pro가 종료된다
            {
                _qa = null;
                FileText.Text = $"검수표를 읽지 못했습니다: {ex.Message}";
                Render();
            }
        }

        private void LoadTable()
        {
            var path = ScreenMode ? Settings.Current.Last?.screen : Settings.Current.Last?.qa;
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                FileText.Text = ScreenMode ? "판독표가 없습니다 - 리본의 [후보 판독]을 누르면 만듭니다" : "검수표가 없습니다 - 먼저 [판독 실행]을 하세요";
                _qa = null;
                Render();
                return;
            }
            _qa = QaTable.Load(path);
            FileText.Text = path;
            FileText.ToolTip = path;
            BuildOrder();
            var first = _order.FindIndex(i => !_qa.IsLabeled(i));
            _pos = first < 0 ? 0 : first;
            Render();
        }

        private void BuildOrder()
        {
            _order = Enumerable.Range(0, _qa.Rows.Count).Where(i => OnlyTodo.IsChecked != true || !_qa.IsLabeled(i)).ToList();
            _pos = Math.Min(_pos, Math.Max(0, _order.Count - 1));
        }

        private void Render()
        {
            var total = _qa?.Rows.Count ?? 0;
            var done = _qa?.Done ?? 0;
            Bar.Maximum = Math.Max(total, 1);
            Bar.Value = done;
            ProgressText.Text = $"{(ScreenMode ? "판독" : "검수")} {done}/{total}";
            ReportBtn.IsEnabled = done > 0;
            if (_qa == null || _order.Count == 0)
            {
                Heading.Text = _qa == null ? "" : ScreenMode ? "남은 후보가 없습니다" : "남은 표본이 없습니다";
                Guide.Text = "";
                Chip.Source = null;
                return;
            }
            var i = _order[_pos];
            var sid = _qa.Get(i, "sample_id");
            var stratum = _qa.Get(i, "stratum");
            var label = _qa.Get(i, "label");
            var li = Array.FindIndex(QaTable.Labels, l => l.code == label);
            var text = li < 0 ? null : ScreenMode ? ScreenLabelTexts[li] : QaTable.Labels[li].text;
            var what = ScreenMode ? $"{_qa.Get(i, "kind")} · {_qa.Get(i, "jimok")} {_qa.Get(i, "addr")}" : StratumName(stratum);
            Heading.Text = $"{_pos + 1}/{_order.Count}  {sid} · {what}  PNU {_qa.Get(i, "pnu")}" + (text != null ? $"  [{text}]" : "");
            Guide.Text = ScreenMode ? GuideForScreen(stratum) : GuideFor(stratum);
            Note.Text = _qa.Get(i, "note");
            if (ScreenMode)
            {
                var rs = _qa.Get(i, "reason");
                var ri = Array.IndexOf(Reasons, rs);
                if (ri >= 0) Reason.SelectedIndex = ri; else Reason.Text = rs;
            }
            foreach (var b in new[] { B1, B2, B3 })
                b.FontWeight = (string)b.Tag == label ? FontWeights.Bold : FontWeights.Normal;
            var img = Path.Combine(Path.GetDirectoryName(_qa.Path), sid + ".jpg");
            Chip.Source = File.Exists(img) ? LoadImage(img) : null;
            if (Follow.IsChecked == true) _ = ZoomAsync(i);
            Focus();
        }

        private static BitmapImage LoadImage(string path)
        {
            try
            {
                var b = new BitmapImage();
                b.BeginInit();
                b.CacheOption = BitmapCacheOption.OnLoad;  // 파일을 잠그지 않게
                b.UriSource = new Uri(path);
                b.EndInit();
                return b;
            }
            catch (Exception) { return null; }  // 손상된 영상은 비워 두고 판정은 계속
        }

        private async System.Threading.Tasks.Task ZoomAsync(int i)
        {
            if (!double.TryParse(_qa.Get(i, "x"), NumberStyles.Float, CultureInfo.InvariantCulture, out var x) ||
                !double.TryParse(_qa.Get(i, "y"), NumberStyles.Float, CultureInfo.InvariantCulture, out var y)) return;
            var view = MapView.Active;
            if (view == null) return;
            await QueuedTask.Run(() =>
            {
                var sr = SpatialReferenceBuilder.CreateSpatialReference(5186);
                view.ZoomTo(EnvelopeBuilderEx.CreateEnvelope(x - 45, y - 45, x + 45, y + 45, sr), TimeSpan.FromMilliseconds(300));
            });
        }

        private void SetLabel(string code)
        {
            if (_qa == null || _order.Count == 0) return;
            var i = _order[_pos];
            _qa.Set(i, "label", code);
            _qa.Set(i, "note", Note.Text.Trim());
            _qa.Set(i, "judge", Judge.Text.Trim());
            if (ScreenMode) _qa.Set(i, "reason", code == "not_structure" ? (Reason.Text ?? "").Trim() : "");
            try { _qa.Save(); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                MessageBox.Show($"검수표를 저장하지 못했습니다(엑셀 등에서 열려 있으면 닫으세요).\n{ex.Message}", "표본 검수");
                return;
            }
            if (_qa.Done == _qa.Rows.Count)
            {
                Render();
                var ask = ScreenMode
                    ? "모든 후보를 판독했습니다. 오탐을 뺀 대상으로 현장조사표·survey.gdb를 내보낼까요?"
                    : "모든 표본을 검수했습니다. 정밀도를 반영해 보고서를 다시 만들까요?";
                if (MessageBox.Show(ask, ScreenMode ? "후보 판독" : "표본 검수", MessageBoxButton.YesNo) == MessageBoxResult.Yes)
                    ReportClick(this, null);
                return;
            }
            Move(+1);
        }

        private void Move(int d)
        {
            if (_qa == null || _order.Count == 0) return;
            if (OnlyTodo.IsChecked == true && d > 0)
            {
                var cur = _order[_pos];
                BuildOrder();
                var next = _order.FindIndex(i => i > cur);
                _pos = next < 0 ? 0 : next;
            }
            else _pos = Math.Clamp(_pos + d, 0, _order.Count - 1);
            Render();
        }

        private void LabelClick(object sender, RoutedEventArgs e) => SetLabel((string)((System.Windows.Controls.Button)sender).Tag);
        private void PrevClick(object sender, RoutedEventArgs e) => Move(-1);
        private void NextClick(object sender, RoutedEventArgs e) => Move(+1);
        private void ReloadClick(object sender, RoutedEventArgs e) => Reload();

        /// <summary>원인을 고른 뒤 바로 2를 누를 수 있게 포커스를 창으로 돌린다(콤보상자에 있으면 단축키를 무시한다).</summary>
        private void ReasonClosed(object sender, EventArgs e) => Focus();

        private void FilterClick(object sender, RoutedEventArgs e)
        {
            if (_qa == null) return;
            BuildOrder();
            Render();
        }

        private void JudgeChanged(object sender, RoutedEventArgs e)
        {
            Settings.Current.Judge = Judge.Text.Trim();
            Settings.Current.TrySave();
        }

        private void ReportClick(object sender, RoutedEventArgs e)
        {
            var r = Settings.Current.Last;
            if (r == null) return;
            if (ScreenMode) { SurveyExportButton.Start(); return; }
            var w = new RunWindow { Owner = FrameworkApplication.Current.MainWindow };
            w.Show();
            w.StartReportOnly(r.@out);
        }

        private void OnKey(object sender, KeyEventArgs e)
        {
            if (e.OriginalSource is TextBox || e.OriginalSource is System.Windows.Controls.ComboBox || e.OriginalSource is System.Windows.Controls.ComboBoxItem) return;  // 메모·판독자·원인 입력 중에는 단축키 무시
            switch (e.Key)
            {
                case Key.D1: case Key.NumPad1: SetLabel("structure"); break;
                case Key.D2: case Key.NumPad2: SetLabel("not_structure"); break;
                case Key.D3: case Key.NumPad3: SetLabel("uncertain"); break;
                case Key.Left: Move(-1); break;
                case Key.Right: Move(+1); break;
                default: return;
            }
            e.Handled = true;
        }
    }
}
