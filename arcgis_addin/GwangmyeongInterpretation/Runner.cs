using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace GwangmyeongInterpretation
{
    /// <summary>
    /// 판독 코드(src.interpretation.addin_run)를 분석 파이썬 하위 프로세스로 실행하고 표준출력을 읽는다.
    /// "@@STEP i/n 설명" → 진행, "@@RESULT {json}" → 결과, "@@ERROR 메시지" → 실패, 나머지 → 로그.
    /// Pro 프로세스 안(arcgispro-py3)이 아니라 별도 프로세스로 돌리는 이유: 분할 단계가 다중 프로세스를 쓰고,
    /// Pro 기본 환경의 rasterio는 재투영 모듈(rasterio.warp)이 깨져 있다. 형식 변환(ECW·MrSID)만 Pro 파이썬에 맡긴다.
    /// </summary>
    internal static class Runner
    {
        public const string RequiredImports = "import rasterio.warp, rasterstats, sklearn, cv2, skimage, geopandas, yaml, pyarrow, matplotlib, openpyxl";
        private static readonly Regex StepRe = new(@"^@@STEP (\d+)/(\d+) (.*)$");

        public static string ProPython()
        {
            var dir = Registry.GetValue(@"HKEY_LOCAL_MACHINE\SOFTWARE\ESRI\ArcGISPro", "InstallDir", null) as string ?? @"C:\Program Files\ArcGIS\Pro\";
            var p = Path.Combine(dir, "bin", "Python", "envs", "arcgispro-py3", "python.exe");
            return File.Exists(p) ? p : null;
        }

        /// <summary>분석 파이썬 후보: 설정값 → 사용자 설치 Python 3.x(최신 우선) → PATH의 python.exe.</summary>
        public static IEnumerable<string> PythonCandidates(string configured)
        {
            if (!string.IsNullOrWhiteSpace(configured)) yield return configured;
            var local = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python");
            if (Directory.Exists(local))
                foreach (var d in Directory.GetDirectories(local, "Python3*").OrderByDescending(d => d))
                    yield return Path.Combine(d, "python.exe");
            foreach (var d in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
                if (!string.IsNullOrWhiteSpace(d) && !d.Contains("WindowsApps"))
                    yield return Path.Combine(d.Trim(), "python.exe");
        }

        // 한 번 확인한 Python은 Pro 세션 동안 다시 확인하지 않는다(패키지 import에 10~30초 걸림)
        private static string _verified;

        /// <summary>판독에 필요한 패키지를 모두 불러오는 첫 후보. 없으면 null과 마지막 오류. force면 이전 확인을 무시.</summary>
        public static async Task<(string python, string error)> FindPythonAsync(string configured, bool force = false)
        {
            if (!force && _verified != null && File.Exists(_verified) &&
                (string.IsNullOrWhiteSpace(configured) || string.Equals(configured, _verified, StringComparison.OrdinalIgnoreCase)))
                return (_verified, null);
            string err = "Python을 찾지 못함";
            foreach (var py in PythonCandidates(configured).Distinct(StringComparer.OrdinalIgnoreCase))
            {
                if (!File.Exists(py)) continue;
                var (code, output) = await CaptureAsync(py, $"-c \"{RequiredImports}; print('ok')\"", null, TimeSpan.FromSeconds(120));
                if (code == 0 && output.Contains("ok")) return (_verified = py, null);
                err = $"{py}: {output.Trim().Split('\n').LastOrDefault()}";
            }
            return (null, err);
        }

        private static ProcessStartInfo Info(string exe, string args, string workDir)
        {
            var psi = new ProcessStartInfo(exe, args)
            {
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8,
            };
            if (workDir != null) psi.WorkingDirectory = workDir;
            // Pro·PostGIS가 넣은 PROJ/GDAL 경로가 pip rasterio의 자체 데이터를 가리면 좌표계 오류가 난다
            foreach (var k in new[] { "PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "GDAL_DRIVER_PATH", "PYTHONHOME", "PYTHONPATH" })
                psi.Environment.Remove(k);
            psi.Environment["PYTHONIOENCODING"] = "utf-8";
            psi.Environment["PYTHONUTF8"] = "1";
            psi.Environment["OPENBLAS_NUM_THREADS"] = "1";
            // 배포 패키지의 Python(패키지를 자체 site-packages에 둠)은 PC 사용자의 개인 패키지와 섞이지 않게 한다
            var own = Path.Combine(Path.GetDirectoryName(exe) ?? "", "Lib", "site-packages", "rasterio");
            if (Directory.Exists(own)) psi.Environment["PYTHONNOUSERSITE"] = "1";
            return psi;
        }

        private static async Task<(int code, string output)> CaptureAsync(string exe, string args, string workDir, TimeSpan timeout)
        {
            Process p = null;
            try
            {
                p = Process.Start(Info(exe, args, workDir));
                var o = p.StandardOutput.ReadToEndAsync();
                var e = p.StandardError.ReadToEndAsync();
                using var cts = new CancellationTokenSource(timeout);
                await p.WaitForExitAsync(cts.Token);
                return (p.ExitCode, await o + await e);
            }
            catch (OperationCanceledException)
            {
                try { p?.Kill(entireProcessTree: true); } catch (Exception) { /* 이미 종료 */ }
                return (-1, $"응답 없음({timeout.TotalSeconds:0}초 초과)");
            }
            catch (Exception ex) { return (-1, ex.Message); }
            finally { p?.Dispose(); }
        }

        /// <summary>명령줄 인자 하나를 따옴표로 감싼다. 따옴표 앞·끝의 역슬래시는 두 배로(예: "D:\"가 따옴표를 삼키지 않게).</summary>
        public static string Quote(string s)
        {
            var sb = new StringBuilder("\"");
            int slashes = 0;
            foreach (var c in s)
            {
                if (c == '\\') { slashes++; continue; }
                sb.Append('\\', c == '"' ? slashes * 2 + 1 : slashes);
                slashes = 0;
                sb.Append(c);
            }
            return sb.Append('\\', slashes * 2).Append('"').ToString();
        }

        /// <summary>실행기를 돌린다. 반환: (종료 코드, 결과 JSON 또는 null, 오류 메시지 또는 null).</summary>
        public static async Task<(int code, string resultJson, string error)> RunAsync(
            string python, string repo, string args, Action<string> onLog, Action<int, int, string> onStep, CancellationToken ct)
        {
            string result = null, error = null;
            using var p = new Process { StartInfo = Info(python, "-u -m src.interpretation.addin_run " + args, repo), EnableRaisingEvents = true };
            void Line(string line)
            {
                if (line == null) return;
                var m = StepRe.Match(line);
                if (m.Success) { onStep(int.Parse(m.Groups[1].Value), int.Parse(m.Groups[2].Value), m.Groups[3].Value); onLog("▶ " + m.Groups[3].Value); }
                else if (line.StartsWith("@@RESULT ")) result = line.Substring(9);
                else if (line.StartsWith("@@ERROR ")) { error = line.Substring(8); onLog("✖ " + error); }
                else onLog(line);
            }
            p.OutputDataReceived += (_, e) => Line(e.Data);
            p.ErrorDataReceived += (_, e) => { if (!string.IsNullOrWhiteSpace(e.Data)) onLog(e.Data); };
            p.Start();
            p.BeginOutputReadLine();
            p.BeginErrorReadLine();
            try { await p.WaitForExitAsync(ct); }
            catch (OperationCanceledException)
            {
                try { p.Kill(entireProcessTree: true); } catch (Exception) { /* 이미 종료 */ }
                return (-2, null, "사용자가 취소함");
            }
            p.WaitForExit(); // 비동기 출력 읽기 마무리
            return (p.ExitCode, result, error);
        }
    }
}
