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

        public static void Show()
        {
            var pane = FrameworkApplication.DockPaneManager.Find(Id);
            pane?.Activate();
        }
    }

    /// <summary>
    /// 표본 검수 창: 검수표(qa/review1/qa_labels.csv)의 표본 영상을 한 장씩 보고 판정한다. 판정마다 바로 저장(label·note·judge).
    /// 층별 판정 기준은 src/interpretation/qa.py build_parcel_round의 설명과 같다.
    /// </summary>
    public partial class ReviewPaneView : UserControl
    {
        private static readonly Dictionary<string, string> StratumNames = new()
        {
            ["new_A"] = "신규 의심 A", ["gone_AB"] = "소멸 의심(전수)", ["unreg_linear"] = "미등재 · 도로·하천류 지목",
            ["seen_linear"] = "판독만 · 도로·하천류 지목", ["unreg_attached"] = "미등재 · 부속 의심",
        };

        private static string StratumName(string s) => StratumNames.TryGetValue(s, out var n) ? n : s.StartsWith("unreg_") ? $"미등재 · 지목 {s[6..]}" : s;

        private static string GuideFor(string s) => s switch
        {
            "new_A" => "왼쪽(기준 시기)에 없던 구조물이 오른쪽(비교 시기) 노란 선 안에 생겼으면 '맞음'. 지붕 도색·천막·차량·그림자는 '아님'",
            "gone_AB" => "왼쪽(기준 시기) 노란 선 안 구조물이 오른쪽(비교 시기)에 실제로 없어졌으면 '맞음'",
            "seen_linear" => "필지(흰 선) 안에 실제 구조물이 있으면 '맞음'",
            _ => "노란 선 안 덩어리 중 하나라도 등재 건물의 일부가 아닌 구조물(창고·하우스·컨테이너·가설물 등)이면 '맞음'. 등재 건물 지붕 가장자리·포장면·수목은 '아님'",
        };

        private QaTable _qa;
        private List<int> _order = new();
        private int _pos;

        /// <summary>열려 있는 검수 창(실행 이력에서 현재 결과를 바꾸면 다시 읽게).</summary>
        internal static ReviewPaneView Current { get; private set; }

        public ReviewPaneView()
        {
            InitializeComponent();
            Current = this;
        }

        private void OnLoaded(object sender, RoutedEventArgs e)
        {
            Judge.Text = Settings.Current.Judge;
            if (_qa == null || !string.Equals(_qa.Path, Settings.Current.Last?.qa, StringComparison.OrdinalIgnoreCase)) Reload();
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
            var path = Settings.Current.Last?.qa;
            if (string.IsNullOrEmpty(path) || !File.Exists(path))
            {
                FileText.Text = "검수표가 없습니다 - 먼저 [판독 실행]을 하세요";
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
            ProgressText.Text = $"검수 {done}/{total}";
            ReportBtn.IsEnabled = done > 0;
            if (_qa == null || _order.Count == 0)
            {
                Heading.Text = _qa == null ? "" : "남은 표본이 없습니다";
                Guide.Text = "";
                Chip.Source = null;
                return;
            }
            var i = _order[_pos];
            var sid = _qa.Get(i, "sample_id");
            var stratum = _qa.Get(i, "stratum");
            var label = _qa.Get(i, "label");
            var text = QaTable.Labels.FirstOrDefault(l => l.code == label).text;
            Heading.Text = $"{_pos + 1}/{_order.Count}  {sid} · {StratumName(stratum)}  PNU {_qa.Get(i, "pnu")}" + (text != null ? $"  [{text}]" : "");
            Guide.Text = GuideFor(stratum);
            Note.Text = _qa.Get(i, "note");
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
            try { _qa.Save(); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                MessageBox.Show($"검수표를 저장하지 못했습니다(엑셀 등에서 열려 있으면 닫으세요).\n{ex.Message}", "표본 검수");
                return;
            }
            if (_qa.Done == _qa.Rows.Count)
            {
                Render();
                if (MessageBox.Show("모든 표본을 검수했습니다. 정밀도를 반영해 보고서를 다시 만들까요?", "표본 검수", MessageBoxButton.YesNo) == MessageBoxResult.Yes)
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
            var w = new RunWindow { Owner = FrameworkApplication.Current.MainWindow };
            w.Show();
            w.StartReportOnly(r.@out);
        }

        private void OnKey(object sender, KeyEventArgs e)
        {
            if (e.OriginalSource is TextBox) return;  // 메모·판독자 입력 중에는 단축키 무시
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
